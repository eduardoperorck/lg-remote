"""Abrir um título na TV — o objetivo do projeto.

Estratégia em camadas, sempre com queda para algo que funciona:

  1. deep link  — `params.contentTarget`, se você calibrou um para aquele app.
                  Instantâneo, mas o formato é privado de cada app; pode não existir.
  2. macro      — abre o app, espera carregar, navega até a busca e digita o título.
                  Mais lento, porém funciona em qualquer app.
  3. só abrir   — se não há macro configurada, ao menos deixa o app aberto.

Nunca falha em silêncio: o resultado sempre diz qual camada foi usada.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lgremote.tv import macros
from lgremote.tv.apps import Service
from lgremote.tv.macros import Sleeper
from lgremote.tv.session import TvSession

# De quanto em quanto tempo perguntamos à TV se o app já assumiu a tela. Curto o
# bastante para não desperdiçar espera, longo o bastante para não competir com os
# comandos do usuário pelo mesmo socket.
FOREGROUND_POLL = 0.5


@dataclass
class OpenResult:
    service_id: str
    app_id: str
    strategy: str
    title: str | None = None
    trace: list[str] = field(default_factory=list)

    @property
    def searched(self) -> bool:
        return self.strategy in {"deep_link", "macro", "episode"}


def _build_context(
    title: str,
    year: str | None,
    tmdb_id: str | None,
    season: int | None,
    episode: int | None,
) -> dict[str, str]:
    """Variáveis disponíveis nas macros.

    `episode_index` é o deslocamento a partir do episódio 1 — é ele que a macro usa em
    `times`, já que se grava a navegação até o primeiro episódio e o resto é contagem.
    """
    return {
        "title": title,
        "year": year or "",
        "tmdb_id": tmdb_id or "",
        "season": str(season) if season else "",
        "episode": str(episode) if episode else "",
        "season_index": str(max(0, season - 1)) if season else "0",
        "episode_index": str(max(0, episode - 1)) if episode else "0",
    }


class TitleOpener:
    def __init__(
        self,
        session: TvSession,
        *,
        sleep: Sleeper = asyncio.sleep,
        on_step: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._sleep = sleep
        self._on_step = on_step

    def _note(self, entry: str) -> None:
        if self._on_step is not None:
            self._on_step(entry)

    async def _await_foreground(self, app_id: str, budget: float) -> bool:
        """Espera o app chegar em primeiro plano. Devolve False se estourou o tempo.

        Medir vale mais que chutar: a tela de perfil só desenha depois que o app assume
        a tela, e esse tempo varia com o humor da TV. Perguntar é barato; contar um
        tempo fixo a partir do launch erra por baixo justamente quando a TV está lenta.
        """
        waited = 0.0
        while waited < budget:
            try:
                if await self._session.current_app() == app_id:
                    return True
            # Não saber em que app a TV está não pode abortar a abertura: seguimos pelo
            # tempo fixo, que é o comportamento de antes.
            except Exception:
                return False
            await self._sleep(FOREGROUND_POLL)
            waited += FOREGROUND_POLL
        return False

    async def _launch(self, service: Service) -> list[str]:
        """Abre o app num estado conhecido, em vez de tentar deduzir em qual ele está.

        Max e Disney+ mostram "Quem está assistindo?" toda vez que o app abre do zero —
        a ajuda do Max diz que em TV isso é por design para perfis adultos.

        Antes decidíamos rodar a macro de perfil comparando o app em primeiro plano com
        o app alvo, ANTES de lançar. Isso tinha uma armadilha que se fechava sozinha:
        parado na própria tela de perfil, o app em primeiro plano já é o alvo — então o
        tratamento do perfil era pulado exatamente na situação em que era necessário, e
        a macro de busca rodava em cima do seletor. Bastava falhar uma vez para nunca
        mais sair de lá.

        Agora, quando há macro de perfil, fechamos o app antes de abrir. Custa alguns
        segundos e devolve o que faltava: saber em que tela a TV está.
        """
        trace: list[str] = []

        if service.profile:
            self._note(f"fechando {service.app_id} para abrir do zero")
            try:
                await self._session.close_app(service.app_id)
            # Fechar é preparação, não o objetivo: app que não estava aberto recusa o
            # comando, e insistir nisso impediria a abertura de acontecer.
            except Exception:
                pass
            else:
                trace.append(f"close {service.app_id}")
                await self._sleep(service.close_settle)

        await self._session.launch(service.app_id)
        self._note(f"launch {service.app_id}")
        trace.append(f"launch {service.app_id}")

        if not service.profile:
            return trace

        if await self._await_foreground(service.app_id, service.foreground_timeout):
            trace.append("app em primeiro plano")

        self._note(f"wait {service.wait_before_profile}s (tela de perfil)")
        await self._sleep(service.wait_before_profile)
        profile_trace = await macros.run_steps(
            self._session, service.profile, {}, sleep=self._sleep, on_step=self._on_step
        )
        trace.extend([f"wait {service.wait_before_profile}s", *profile_trace])
        return trace

    async def open_service(self, service: Service) -> OpenResult:
        """Só abre o app, sem buscar nada."""
        trace = await self._launch(service)
        return OpenResult(service.id, service.app_id, "launch", trace=trace)

    async def open_title(
        self,
        service: Service,
        title: str,
        *,
        year: str | None = None,
        tmdb_id: str | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> OpenResult:
        context = _build_context(title, year, tmdb_id, season, episode)

        if service.content_target:
            target = macros.render(service.content_target, context)
            params: dict[str, Any] = {"contentTarget": target}
            await self._session.launch_with_params(service.app_id, params)
            return OpenResult(
                service.id, service.app_id, "deep_link", title, [f"contentTarget {target!r}"]
            )

        launch_trace = await self._launch(service)
        if not service.can_search:
            return OpenResult(service.id, service.app_id, "launch", title, launch_trace)

        # Digitar antes do app terminar de carregar joga as teclas no vazio —
        # é a causa nº 1 de macro que "funciona às vezes".
        self._note(f"wait {service.wait_after_launch}s (carregando o app)")
        await self._sleep(service.wait_after_launch)
        trace = await macros.run_steps(
            self._session, service.search, context, sleep=self._sleep, on_step=self._on_step
        )
        result = OpenResult(
            service.id,
            service.app_id,
            "macro",
            title,
            [*launch_trace, f"wait {service.wait_after_launch}s", *trace],
        )

        if episode is None or not service.can_pick_episode:
            # Sem macro de episódio a série fica aberta na página inicial dela — que é
            # exatamente onde o fluxo já chegava antes. Nunca pior que o estado atual.
            return result

        self._note(f"wait {service.wait_before_episode}s (abrindo a série)")
        await self._sleep(service.wait_before_episode)
        episode_trace = await macros.run_steps(
            self._session, service.episode, context, sleep=self._sleep, on_step=self._on_step
        )
        result.strategy = "episode"
        result.trace.extend([f"wait {service.wait_before_episode}s", *episode_trace])
        return result
