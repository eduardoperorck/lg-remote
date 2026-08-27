"""Catálogo de serviços: liga provedor do TMDb -> app da TV -> macro de busca."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lgremote.tv import macros
from lgremote.tv.macros import Step, parse_steps

_LOGGER = logging.getLogger(__name__)


class ServiceConfigError(ValueError):
    """apps.yaml malformado."""


@dataclass(frozen=True)
class Service:
    """Um serviço de streaming e como abri-lo nesta TV."""

    id: str
    label: str
    app_id: str
    tmdb_names: tuple[str, ...] = ()
    match: tuple[str, ...] = ()
    wait_after_launch: float = 8.0
    content_target: str | None = None
    search: tuple[Step, ...] = ()
    # Tela "Quem está assistindo?". Ter esta macro preenchida é o que faz o app ser
    # SEMPRE fechado antes de abrir: é a única forma de garantir em que tela ele está.
    # Deduzir isso do app em primeiro plano não funciona — parado no próprio seletor de
    # perfis o app já é o de sempre, e o tratamento era desligado justo quando precisava.
    profile: tuple[Step, ...] = ()
    # Contado a partir do momento em que o app aparece em primeiro plano, não do launch.
    wait_before_profile: float = 5.0
    # Pausa entre fechar e reabrir: o webOS precisa de um instante para soltar o app.
    close_settle: float = 1.5
    # Teto para esperar o app chegar em primeiro plano antes de cair no tempo fixo.
    foreground_timeout: float = 20.0
    # Da página da série até tocar um episódio. Usa {episode_index} para o deslocamento.
    episode: tuple[Step, ...] = ()
    # Tempo para a página da série carregar depois que a busca a abre.
    wait_before_episode: float = 4.0

    @property
    def can_search(self) -> bool:
        return bool(self.search)

    @property
    def can_pick_episode(self) -> bool:
        return bool(self.episode)


@dataclass
class ServiceCatalog:
    services: list[Service] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)

    def by_id(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def by_app_id(self, app_id: str) -> Service | None:
        return next((s for s in self.services if s.app_id == app_id), None)

    def by_tmdb_provider(self, provider_name: str) -> Service | None:
        """Casa o nome que o TMDb devolve com um serviço.

        O TMDb varia o rótulo ('Max', 'Max Amazon Channel'), então cai para
        comparação por prefixo/termo antes de desistir.
        """
        wanted = provider_name.strip().casefold()
        for service in self.services:
            if any(name.casefold() == wanted for name in service.tmdb_names):
                return service
        for service in self.services:
            if any(term.casefold() in wanted for term in service.match):
                return service
        return None

    def shortcut_services(self) -> list[Service]:
        resolved = (self.by_id(sid) for sid in self.shortcuts)
        return [service for service in resolved if service is not None]


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _profile_wait(raw: dict[str, Any], service_id: str, wait_after_launch: float) -> float:
    """Espera até a tela de perfil, nunca menor que o tempo de carga do app.

    A tela de perfil aparece DEPOIS de o app carregar. Configurar menos que isso manda o
    ENTER num app que ainda está subindo — e como botão no webOS vai sem confirmação, o
    toque some em silêncio e a macro de busca continua rodando em cima do seletor de
    perfis. Era exatamente o caso do Max (8s de perfil contra 12s de carga).
    """
    configured = float(raw.get("wait_before_profile", 5.0))
    # Sem macro de perfil o campo não é usado; avisar aqui seria barulho em todo boot.
    if not raw.get("profile") or configured >= wait_after_launch:
        return configured

    _LOGGER.warning(
        "%s: wait_before_profile (%.0fs) é menor que wait_after_launch (%.0fs); usando %.0fs",
        service_id,
        configured,
        wait_after_launch,
        wait_after_launch,
    )
    return wait_after_launch


def parse_catalog(data: dict[str, Any]) -> ServiceCatalog:
    raw_services = data.get("services") or []
    if not isinstance(raw_services, list):
        raise ServiceConfigError("'services' precisa ser uma lista")

    services: list[Service] = []
    for raw in raw_services:
        if not isinstance(raw, dict) or "id" not in raw:
            raise ServiceConfigError(f"serviço sem 'id': {raw!r}")
        service_id = str(raw["id"])
        app_id = str(raw.get("app_id") or "").strip()
        if not app_id:
            raise ServiceConfigError(
                f"serviço {service_id!r} sem app_id — rode `lgremote discover`"
            )
        wait_after_launch = float(raw.get("wait_after_launch", 8.0))
        services.append(
            Service(
                id=service_id,
                label=str(raw.get("label", service_id)),
                app_id=app_id,
                tmdb_names=_as_tuple(raw.get("tmdb_names")),
                match=_as_tuple(raw.get("match")),
                wait_after_launch=wait_after_launch,
                content_target=raw.get("content_target") or None,
                search=tuple(parse_steps(raw.get("search"))),
                profile=tuple(parse_steps(raw.get("profile"))),
                wait_before_profile=_profile_wait(raw, service_id, wait_after_launch),
                close_settle=float(raw.get("close_settle", 1.5)),
                foreground_timeout=float(raw.get("foreground_timeout", 20.0)),
                episode=tuple(parse_steps(raw.get("episode"))),
                wait_before_episode=float(raw.get("wait_before_episode", 4.0)),
            )
        )

    known = {service.id for service in services}
    shortcuts = [sid for sid in _as_tuple(data.get("shortcuts")) if sid in known]
    return ServiceCatalog(services=services, shortcuts=shortcuts or [s.id for s in services])


def load_catalog(path: Path) -> ServiceCatalog:
    if not path.exists():
        raise ServiceConfigError(f"Não achei {path}. O arquivo veio no repositório — foi apagado?")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_catalog(data)


# --- descoberta contra a TV real -----------------------------------------


@dataclass(frozen=True)
class Discovery:
    service_id: str
    app_id: str
    app_title: str
    changed: bool


def match_installed_apps(
    catalog: ServiceCatalog, installed: list[dict[str, Any]]
) -> tuple[list[Discovery], list[Service]]:
    """Confere cada serviço contra os apps instalados na TV.

    IDs de app mudam por região e por ano do modelo, então ler da TV é sempre mais
    confiável que qualquer lista publicada na internet.

    Devolve (encontrados, não encontrados).
    """
    by_id = {str(app.get("id", "")): app for app in installed}
    found: list[Discovery] = []
    missing: list[Service] = []

    for service in catalog.services:
        exact = by_id.get(service.app_id)
        if exact is not None:
            found.append(
                Discovery(service.id, service.app_id, str(exact.get("title", "")), changed=False)
            )
            continue

        candidate = _find_by_title(service, installed)
        if candidate is None:
            missing.append(service)
            continue

        found.append(
            Discovery(
                service.id,
                str(candidate.get("id", "")),
                str(candidate.get("title", "")),
                changed=True,
            )
        )

    return found, missing


def _find_by_title(service: Service, installed: list[dict[str, Any]]) -> dict[str, Any] | None:
    terms = [term.casefold() for term in service.match] or [service.label.casefold()]
    for app in installed:
        title = str(app.get("title", "")).casefold()
        if any(term in title for term in terms):
            return app
    return None


_SERVICE_LINE = re.compile(r"^\s*-\s+id:\s*(\S+)\s*$")
_APP_ID_LINE = re.compile(r"^(\s*)app_id:\s*.*$")
_KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][\w-]*):\s*(.*)$")
_ITEM_LINE = re.compile(r"^\s*-\s")

MACRO_KEYS = ("profile", "search", "episode")


def apply_discoveries(path: Path, discoveries: list[Discovery]) -> list[str]:
    """Reescreve só as linhas `app_id:` que mudaram, preservando comentários.

    PyYAML perderia todos os comentários do apps.yaml na volta, e são eles que
    explicam como calibrar as macros. Edição por linha mantém o arquivo intacto.
    """
    updates = {d.service_id: d.app_id for d in discoveries if d.changed}
    if not updates:
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    current: str | None = None
    applied: list[str] = []

    for index, line in enumerate(lines):
        service_match = _SERVICE_LINE.match(line)
        if service_match:
            current = service_match.group(1)
            continue
        if current in updates and (app_match := _APP_ID_LINE.match(line)):
            lines[index] = f"{app_match.group(1)}app_id: {updates[current]}"
            applied.append(f"{current} -> {updates[current]}")
            current = None

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return applied


class MacroWriteError(ValueError):
    """Não deu para gravar a macro no apps.yaml."""


def _service_bounds(lines: list[str], service_id: str) -> tuple[int, int]:
    """Onde começa e termina o bloco de um serviço. Fim é exclusivo."""
    start = -1
    for index, line in enumerate(lines):
        match = _SERVICE_LINE.match(line)
        if match and match.group(1) == service_id:
            start = index
            break
    if start < 0:
        raise MacroWriteError(f"Serviço {service_id!r} não existe no apps.yaml.")

    for index in range(start + 1, len(lines)):
        if _SERVICE_LINE.match(lines[index]) or (
            _ITEM_LINE.match(lines[index]) and not lines[index].startswith(" " * 6)
        ):
            return start, index
    return start, len(lines)


def _key_bounds(lines: list[str], start: int, end: int, key: str) -> tuple[int, int, str]:
    """Onde está a chave dentro do serviço, e com que indentação. Fim é exclusivo.

    Uma lista pode estar em bloco (`search:` seguido de itens `- {...}`) ou em linha
    (`episode: []`). Os dois têm de ser reconhecidos, senão regravar um serviço que
    nunca foi calibrado deixaria o `[]` antigo logo acima da lista nova.
    """
    for index in range(start + 1, end):
        match = _KEY_LINE.match(lines[index])
        if not match or match.group(2) != key:
            continue

        indent = match.group(1)
        stop = index + 1
        # Só as linhas MAIS indentadas que a chave pertencem a ela: parar na próxima
        # chave do mesmo nível é o que impede engolir o resto do serviço.
        while stop < end and (
            not lines[stop].strip()
            or lines[stop].startswith(indent + " ")
            or _ITEM_LINE.match(lines[stop])
        ):
            if lines[stop].strip() and not lines[stop].startswith(indent + " "):
                break
            stop += 1
        return index, stop, indent

    raise MacroWriteError(f"Chave {key!r} não existe no serviço — acrescente-a no apps.yaml.")


def write_macro(path: Path, service_id: str, key: str, steps: Sequence[Step]) -> Path:
    """Grava uma macro gravada na TV dentro do apps.yaml, sem perder os comentários.

    Mesma razão do `apply_discoveries`: o PyYAML devolveria o arquivo sem nenhum dos
    comentários que explicam como calibrar. Aqui só as linhas da chave são trocadas.

    Faz uma cópia `.bak` antes de escrever — este arquivo é do usuário, e pode estar
    aberto num editor enquanto a gravação acontece.
    """
    if key not in MACRO_KEYS:
        raise MacroWriteError(f"Só sei gravar {', '.join(MACRO_KEYS)} — recebi {key!r}.")
    if not steps:
        raise MacroWriteError("Gravação sem nenhum passo.")

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    start, end = _service_bounds(lines, service_id)
    key_start, key_end, indent = _key_bounds(lines, start, end, key)

    block = [f"{indent}{key}:"]
    block += [f"{indent}  - {macros.step_to_yaml(step)}" for step in steps]

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    rewritten = lines[:key_start] + block + lines[key_end:]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    # Reler é o que garante que não escrevemos YAML inválido no arquivo do usuário —
    # e o `.bak` ao lado é o caminho de volta se algum dia isto falhar.
    load_catalog(path)
    return backup
