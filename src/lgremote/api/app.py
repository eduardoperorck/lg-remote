"""FastAPI: rotas de controle + serve o PWA."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lgremote.api.runner import ActionRunner
from lgremote.catalog.tmdb import CatalogDisabledError, CatalogError, Title, TmdbClient
from lgremote.config import APPS_FILE, PRESETS_FILE, Settings, write_env_value
from lgremote.tv import macros, wol
from lgremote.tv.apps import (
    MACRO_KEYS,
    MacroWriteError,
    ServiceCatalog,
    ServiceConfigError,
    load_catalog,
    write_macro,
)
from lgremote.tv.buttons import UnknownButtonError
from lgremote.tv.discovery import locate_tv
from lgremote.tv.opener import TitleOpener
from lgremote.tv.presets import Preset, PresetError, load_presets, now_stamp, save_presets, slugify
from lgremote.tv.recorder import Recorder, RecorderError
from lgremote.tv.session import TvError, TvSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Mesmo respiro que o `delay` padrão das macros usa entre repetições.
BUTTON_REPEAT_DELAY = 0.2


class ButtonRequest(BaseModel):
    name: str
    # Deslizar o polegar vira vários passos de uma vez; mandar uma requisição por passo
    # inundaria a TV e a rede. O teto é o mesmo das macros, por coerência.
    times: int = Field(default=1, ge=1, le=macros.MAX_REPEAT)


class TextRequest(BaseModel):
    text: str = Field(max_length=200)
    enter: bool = False


class VolumeRequest(BaseModel):
    value: int = Field(ge=0, le=100)


class MuteRequest(BaseModel):
    mute: bool


class LaunchRequest(BaseModel):
    service_id: str


class OpenTitleRequest(BaseModel):
    service_id: str
    title: str = Field(min_length=1, max_length=200)
    year: str | None = None
    tmdb_id: int | None = None
    season: int | None = Field(default=None, ge=1, le=100)
    episode: int | None = Field(default=None, ge=1, le=500)


class RecordStartRequest(BaseModel):
    # "preset" salva em presets.yaml (legenda/áudio); as demais gravam a macro
    # correspondente no apps.yaml, que é o que permite calibrar sem editar YAML.
    target: str = "preset"
    service_id: str | None = None
    # O título que você vai digitar durante a gravação de `search`. Ele vira {title}.
    sample_title: str = Field(default="", max_length=200)


class RecordStopRequest(BaseModel):
    label: str = Field(default="", max_length=40)
    service_id: str | None = None


def _build_session(settings: Settings) -> TvSession:
    """Sessão que se vira sozinha quando o DHCP troca o IP da TV.

    É o que evita ter que rodar o assistente de novo por causa de um endereço novo:
    o servidor reencontra a TV pela identidade e regrava o `.env` na hora.
    """

    async def relocate() -> str | None:
        found = await locate_tv(uuid=settings.tv_uuid, mac=settings.tv_mac)
        return found.host if found else None

    return TvSession(
        settings.tv_host,
        settings.tv_client_key or None,
        rediscover=relocate,
        on_host_change=lambda host: write_env_value("TV_HOST", host),
    )


class AppState:
    """Tudo que vive enquanto o servidor está de pé.

    `session` e `tmdb` são injetáveis para os testes rodarem sem TV e sem rede.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session: TvSession | None = None,
        tmdb: TmdbClient | None = None,
        presets_file: Path | None = None,
    ) -> None:
        self.settings = settings
        self.catalog: ServiceCatalog = load_catalog(APPS_FILE)
        self.session = session or _build_session(settings)
        self.runner = ActionRunner()
        # O `on_step` é o que faz o celular mostrar em que ponto a abertura está. Sem
        # ele, o PWA só recebia o resultado no fim — e uma falha no meio dos ~25s era
        # indistinguível de nada ter acontecido.
        self.opener = TitleOpener(self.session, on_step=self.runner.note)
        self.recorder = Recorder()
        self.presets_file = presets_file or PRESETS_FILE
        self.presets = load_presets(self.presets_file)
        self.tmdb = tmdb
        if self.tmdb is None and settings.has_catalog:
            self.tmdb = TmdbClient(settings.tmdb_token)

    async def current_service_id(self) -> str | None:
        """Qual serviço está em foco na TV agora — define o contexto do gravador."""
        try:
            app_id = await self.session.current_app()
        except TvError:
            return None
        service = self.catalog.by_app_id(app_id or "")
        return service.id if service else None

    async def shutdown(self) -> None:
        await self.runner.cancel()
        await self.session.close()
        if self.tmdb is not None:
            await self.tmdb.close()


def create_app(
    settings: Settings,
    *,
    session: TvSession | None = None,
    tmdb: TmdbClient | None = None,
    presets_file: Path | None = None,
) -> FastAPI:
    state = AppState(settings, session=session, tmdb=tmdb, presets_file=presets_file)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await state.shutdown()

    app = FastAPI(title="lg-remote", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(TvError)
    async def tv_falhou(_: Request, exc: Exception) -> JSONResponse:
        """A TV falhando não é bug do servidor.

        Sem isto vira 500 com texto genérico, e o celular mostra "Erro 500" para o que
        na verdade é "a TV recusou a chave salva" — que tem conserto conhecido.
        """
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_502_BAD_GATEWAY)

    def require_pin(x_remote_pin: Annotated[str | None, Header()] = None) -> None:
        """Sem isso, qualquer aparelho na rede (ou visita no Wi-Fi) controla a TV."""
        expected = settings.ui_pin
        if not expected:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "UI_PIN não configurado no .env — o servidor não aceita comandos sem PIN.",
            )
        if not x_remote_pin or not secrets.compare_digest(x_remote_pin, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "PIN incorreto")

    guarded = [Depends(require_pin)]

    # --- estado ----------------------------------------------------------

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """Aberta de propósito: o PWA precisa saber o que mostrar antes do PIN."""
        return {
            # Da sessão, não das settings: depois de a TV trocar de IP o valor lido do
            # .env em memória está velho, e o celular mostraria um endereço que não existe.
            "tv_host": state.session.host,
            "paired": settings.is_paired,
            "catalog_enabled": state.tmdb is not None,
            "can_wake": bool(settings.tv_mac),
            "shortcuts": [
                {
                    "id": service.id,
                    "label": service.label,
                    "can_search": service.can_search,
                    "can_pick_episode": service.can_pick_episode,
                }
                for service in state.catalog.shortcut_services()
            ],
        }

    @app.get("/api/status", dependencies=guarded)
    async def get_status() -> dict[str, Any]:
        try:
            current = await state.session.current_app()
            volume = await state.session.get_volume()
        except TvError as exc:
            return {"online": False, "detail": str(exc)}
        service = state.catalog.by_app_id(current or "")
        return {
            "online": True,
            "current_app": current,
            "current_service": service.label if service else None,
            # O PWA usa isto para mostrar só os presets do app que está na tela.
            "current_service_id": service.id if service else None,
            "volume": volume,
            "recording": state.recorder.active,
            "recording_presses": state.recorder.count,
        }

    @app.get("/api/action", dependencies=guarded)
    async def get_action() -> dict[str, Any]:
        return state.runner.state.as_dict()

    # --- controle direto -------------------------------------------------

    @app.post("/api/button", dependencies=guarded)
    async def press_button(payload: ButtonRequest) -> dict[str, str]:
        for index in range(payload.times):
            try:
                await state.session.button(payload.name)
            except UnknownButtonError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            # Depois de chegar na TV: gravar um toque que falhou daria macro mentirosa.
            try:
                state.recorder.observe(payload.name.strip().upper())
            except RecorderError as exc:
                state.recorder.cancel()
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            # O mesmo respiro que as macros dão entre repetições: sem ele a TV engole
            # toques mandados em rajada.
            if index < payload.times - 1:
                await asyncio.sleep(BUTTON_REPEAT_DELAY)
        return {"status": "ok"}

    @app.post("/api/text", dependencies=guarded)
    async def send_text(payload: TextRequest) -> dict[str, str]:
        await state.session.insert_text(payload.text, replace=True)
        if payload.enter:
            await state.session.send_enter()
        # Depois de chegar na TV, como no /api/button: gravar o que falhou daria uma
        # macro mentirosa. É este passo que faz a busca acontecer — sem ele, a macro
        # gravada levaria até o campo de texto e pararia ali.
        state.recorder.observe_text(payload.text)
        return {"status": "ok"}

    @app.post("/api/volume", dependencies=guarded)
    async def set_volume(payload: VolumeRequest) -> dict[str, str]:
        await state.session.set_volume(payload.value)
        return {"status": "ok"}

    @app.post("/api/mute", dependencies=guarded)
    async def set_mute(payload: MuteRequest) -> dict[str, str]:
        await state.session.set_mute(payload.mute)
        return {"status": "ok"}

    @app.post("/api/power/off", dependencies=guarded)
    async def power_off() -> dict[str, str]:
        await state.session.power_off()
        return {"status": "ok"}

    @app.post("/api/power/on", dependencies=guarded)
    async def power_on() -> dict[str, str]:
        if not settings.tv_mac:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "TV_MAC não configurado no .env — sem ele não dá para ligar a TV.",
            )
        wol.wake(settings.tv_mac)
        return {"status": "ok", "detail": "magic packet enviado"}

    # --- apps e títulos --------------------------------------------------

    @app.post("/api/launch", dependencies=guarded)
    async def launch(payload: LaunchRequest) -> dict[str, str]:
        service = state.catalog.by_id(payload.service_id)
        if service is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Serviço desconhecido")
        await state.session.launch(service.app_id)
        return {"status": "ok", "app_id": service.app_id}

    @app.get("/api/search", dependencies=guarded)
    async def search(q: Annotated[str, Query(min_length=1, max_length=100)]) -> dict[str, Any]:
        tmdb = _require_catalog()
        try:
            titles = await tmdb.search(q)
        except (CatalogError, CatalogDisabledError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {"results": [_serialize_title(title, state.catalog) for title in titles]}

    @app.post("/api/open", status_code=status.HTTP_202_ACCEPTED, dependencies=guarded)
    async def open_title(payload: OpenTitleRequest) -> JSONResponse:
        service = state.catalog.by_id(payload.service_id)
        if service is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Serviço desconhecido")

        async def action() -> list[str]:
            result = await state.opener.open_title(
                service,
                payload.title,
                year=payload.year,
                tmdb_id=str(payload.tmdb_id) if payload.tmdb_id else None,
                season=payload.season,
                episode=payload.episode,
            )
            return result.trace

        label = f"Abrindo {service.label}: {payload.title}"
        if payload.season and payload.episode:
            label += f" T{payload.season}E{payload.episode}"
            if not service.can_pick_episode:
                # Dizer isto agora evita você achar que o app travou esperando o episódio.
                label += " (sem macro de episódio — vai parar na página da série)"
        if not state.runner.start(label, action):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Já tem uma ação rodando na TV. Espere terminar."
            )
        return JSONResponse({"status": "started", "label": label}, status.HTTP_202_ACCEPTED)

    @app.get("/api/seasons/{tmdb_id}", dependencies=guarded)
    async def get_seasons(tmdb_id: int) -> dict[str, Any]:
        tmdb = _require_catalog()
        try:
            seasons = await tmdb.get_seasons(tmdb_id)
        except (CatalogError, CatalogDisabledError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {
            "seasons": [
                {
                    "season_number": season.season_number,
                    "name": season.name,
                    "episode_count": season.episode_count,
                }
                for season in seasons
            ]
        }

    @app.get("/api/episodes/{tmdb_id}/{season_number}", dependencies=guarded)
    async def get_episodes(tmdb_id: int, season_number: int) -> dict[str, Any]:
        tmdb = _require_catalog()
        try:
            episodes = await tmdb.get_episodes(tmdb_id, season_number)
        except (CatalogError, CatalogDisabledError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {
            "episodes": [
                {
                    "season_number": episode.season_number,
                    "episode_number": episode.episode_number,
                    "name": episode.name,
                    "still_url": episode.still_url,
                    "runtime": episode.runtime,
                }
                for episode in episodes
            ]
        }

    # --- gravador --------------------------------------------------------

    @app.get("/api/record", dependencies=guarded)
    async def record_state() -> dict[str, Any]:
        return {
            "recording": state.recorder.active,
            "presses": state.recorder.count,
            "service_id": state.recorder.service_id,
            "target": state.recorder.target,
        }

    @app.post("/api/record/start", dependencies=guarded)
    async def record_start(payload: RecordStartRequest | None = None) -> dict[str, Any]:
        request = payload or RecordStartRequest()
        if request.target != "preset" and request.target not in MACRO_KEYS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Alvo desconhecido: {request.target!r}",
            )

        # Para macro do apps.yaml o serviço pode vir escolhido na tela; para preset ele
        # tem de ser o app em foco, senão o preset nasce preso ao serviço errado.
        service_id = request.service_id or await state.current_service_id()
        if service_id is None or state.catalog.by_id(service_id) is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Abra o app na TV antes de gravar — é ele que define de quem é a gravação.",
            )
        try:
            state.recorder.start(
                service_id=service_id,
                app_id=None,
                target=request.target,
                sample_title=request.sample_title.strip(),
            )
        except RecorderError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"status": "recording", "service_id": service_id, "target": request.target}

    @app.post("/api/record/cancel", dependencies=guarded)
    async def record_cancel() -> dict[str, str]:
        state.recorder.cancel()
        return {"status": "ok"}

    @app.post("/api/record/stop", dependencies=guarded)
    async def record_stop(payload: RecordStopRequest) -> dict[str, Any]:
        target = state.recorder.target
        try:
            steps, recorded_service = state.recorder.stop()
        except RecorderError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        service_id = payload.service_id or recorded_service
        if service_id is None or state.catalog.by_id(service_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Serviço desconhecido")

        if target in MACRO_KEYS:
            try:
                backup = write_macro(APPS_FILE, service_id, target, steps)
            except (MacroWriteError, ServiceConfigError, OSError) as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            # Recarregar na hora: sem isto a macro nova só valeria no próximo boot do
            # servidor, e você testaria a antiga achando que testou a que gravou.
            state.catalog = load_catalog(APPS_FILE)
            return {
                "status": "saved",
                "target": target,
                "service_id": service_id,
                "steps": [macros.step_to_yaml(step) for step in steps],
                "backup": str(backup),
            }

        if not payload.label.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dê um nome ao preset.")

        preset = Preset(
            id=slugify(payload.label),
            label=payload.label.strip(),
            service_id=service_id,
            steps=tuple(steps),
            recorded_at=now_stamp(),
        )
        try:
            state.presets.put(preset)
            save_presets(state.presets_file, state.presets)
        except (PresetError, OSError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        return {"status": "saved", "preset": _serialize_preset(preset)}

    # --- presets ---------------------------------------------------------

    @app.get("/api/presets", dependencies=guarded)
    async def list_presets(service_id: str | None = None) -> dict[str, Any]:
        presets = (
            state.presets.for_service(service_id) if service_id else state.presets.all_presets()
        )
        return {"presets": [_serialize_preset(preset) for preset in presets]}

    @app.post("/api/presets/{service_id}/{preset_id}/run", dependencies=guarded)
    async def run_preset(service_id: str, preset_id: str) -> JSONResponse:
        preset = state.presets.get(service_id, preset_id)
        if preset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset não encontrado")

        async def action() -> list[str]:
            return await macros.run_steps(state.session, preset.steps)

        if not state.runner.start(preset.label, action):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Já tem uma ação rodando na TV. Espere terminar."
            )
        return JSONResponse({"status": "started", "label": preset.label}, status.HTTP_202_ACCEPTED)

    @app.delete("/api/presets/{service_id}/{preset_id}", dependencies=guarded)
    async def delete_preset(service_id: str, preset_id: str) -> dict[str, str]:
        if not state.presets.remove(service_id, preset_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset não encontrado")
        save_presets(state.presets_file, state.presets)
        return {"status": "deleted"}

    def _require_catalog() -> TmdbClient:
        if state.tmdb is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Busca desligada: configure TMDB_TOKEN no .env.",
            )
        return state.tmdb

    # O PWA por último: um mount em "/" engoliria as rotas registradas depois dele.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _serialize_preset(preset: Preset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "label": preset.label,
        "service_id": preset.service_id,
        "steps": len(preset.steps),
        "recorded_at": preset.recorded_at,
    }


def _serialize_title(title: Title, catalog: ServiceCatalog) -> dict[str, Any]:
    """Traduz provedores do TMDb em serviços que ESTA TV sabe abrir.

    O celular só deve mostrar botão para o que realmente dá para abrir; oferecer um
    serviço sem app instalado é prometer o que não se cumpre.
    """
    services: list[dict[str, str]] = []
    seen: set[str] = set()
    for provider in title.providers:
        service = catalog.by_tmdb_provider(provider.provider_name)
        if service is None or service.id in seen:
            continue
        seen.add(service.id)
        services.append({"id": service.id, "label": service.label})

    return {
        "tmdb_id": title.tmdb_id,
        "media_type": title.media_type,
        "name": title.name,
        "year": title.year,
        "poster_url": title.poster_url,
        "providers": [provider.provider_name for provider in title.providers],
        "services": services,
    }
