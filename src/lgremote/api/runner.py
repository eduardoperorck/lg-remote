"""Executor de uma ação longa por vez.

Abrir um título leva ~20s (esperar o app carregar + navegar + digitar). Segurar a
requisição HTTP todo esse tempo faria o PWA parecer travado e estouraria timeout no
Safari. Então a ação roda em background e o celular pergunta o progresso.

Uma ação por vez de propósito: duas macros simultâneas mandariam teclas concorrentes
para a mesma TV e as duas falhariam.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class ActionState:
    status: str = "idle"  # idle | running | done | error
    label: str = ""
    detail: str = ""
    # O passo que está acontecendo AGORA. Sem isto, abrir um título eram ~25 segundos de
    # tela parada: quando algo dava errado no meio, parecia que nada tinha acontecido.
    step: str = ""
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "label": self.label,
            "detail": self.detail,
            "step": self.step,
            "trace": self.trace,
        }


class ActionRunner:
    def __init__(self) -> None:
        self.state = ActionState()
        self._task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def note(self, step: str) -> None:
        """Publica o passo atual. Serve de `on_step` para quem executa a ação."""
        self.state.step = step

    def start(self, label: str, action: Callable[[], Awaitable[list[str]]]) -> bool:
        """Dispara a ação. Devolve False se já havia uma rodando."""
        if self.busy:
            return False

        self.state = ActionState(status="running", label=label)
        self._task = asyncio.create_task(self._run(action))
        return True

    async def _run(self, action: Callable[[], Awaitable[list[str]]]) -> None:
        try:
            trace = await action()
        # Captura ampla de propósito: rodando em background, qualquer erro que escape
        # some no vazio — e o celular fica esperando uma ação que já morreu.
        except Exception as exc:
            _LOGGER.exception("ação falhou: %s", self.state.label)
            self.state.status = "error"
            self.state.detail = str(exc)
        else:
            self.state.status = "done"
            self.state.trace = trace
            self.state.step = ""

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.state = ActionState()
