"""Gravação de sequências: você navega na TV, o app anota.

O menu de legenda/áudio muda por app e por título, então adivinhar a sequência não
escala. Aqui o caminho é o inverso: você faz o que já faria com o controle, e o que
sai é uma macro pronta.

A compressão é o que separa uma gravação usável de um despejo de eventos: toques
repetidos viram `times`, e as suas hesitações humanas viram esperas arredondadas —
ou desaparecem, quando são curtas demais para importar.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from lgremote.tv.macros import ButtonStep, Step, TextStep, WaitStep

# Abaixo disto, a pausa é ruído de digitação: o app já espera `delay` entre repetições.
MIN_GAP_SECONDS = 0.6
# Acima disto, é você pensando ou olhando a TV — não deve virar espera fixa na macro.
MAX_GAP_SECONDS = 5.0
# Uma gravação esquecida aberta captura toques que não são dela.
DEFAULT_TTL_SECONDS = 300.0
MAX_EVENTS = 200


class RecorderError(RuntimeError):
    """Operação inválida no estado atual da gravação."""


@dataclass(frozen=True)
class ButtonEvent:
    name: str
    at: float
    # Preenchido quando o evento é texto digitado, não um botão do direcional.
    text: str | None = None


@dataclass
class Recording:
    """Uma gravação em andamento."""

    service_id: str | None
    app_id: str | None
    started_at: float
    # Para onde vai o resultado: um preset de legenda/áudio, ou uma macro do apps.yaml.
    target: str = "preset"
    # O título digitado durante a gravação, que vira {title} na macro de busca.
    sample_title: str = ""
    events: list[ButtonEvent] = field(default_factory=list)

    def add(self, name: str, at: float, *, text: str | None = None) -> None:
        if len(self.events) >= MAX_EVENTS:
            raise RecorderError(
                f"Gravação longa demais ({MAX_EVENTS} toques). Pare e grave um trecho menor."
            )
        self.events.append(ButtonEvent(name, at, text))


def compress(
    events: list[ButtonEvent], *, started_at: float, sample_title: str = ""
) -> list[Step]:
    """Transforma toques crus numa macro legível e editável.

    A primeira pausa (entre iniciar a gravação e o primeiro toque) é descartada de
    propósito: é o tempo que você levou para pegar o celular, não parte da sequência.

    `sample_title` é o título que você digitou durante a gravação: ele vira `{title}`
    para a macro servir a qualquer outro título depois. Sem isso, a macro gravada só
    saberia buscar aquela série específica.
    """
    steps: list[Step] = []
    previous_at = started_at

    for event in events:
        gap = event.at - previous_at
        previous_at = event.at

        # A espera vem ANTES do botão: reproduz o ritmo em que a TV recebeu os toques.
        if steps and gap >= MIN_GAP_SECONDS:
            steps.append(WaitStep(round(min(gap, MAX_GAP_SECONDS), 1)))

        if event.text is not None:
            steps.append(TextStep(_templatize(event.text, sample_title)))
            continue

        last = steps[-1] if steps else None
        if isinstance(last, ButtonStep) and last.name == event.name and isinstance(last.times, int):
            steps[-1] = ButtonStep(last.name, last.times + 1, last.delay)
        else:
            steps.append(ButtonStep(event.name))

    return steps


def _templatize(text: str, sample_title: str) -> str:
    """Troca o título de exemplo pelo placeholder, sem diferenciar maiúsculas."""
    if not sample_title:
        return text
    lowered = text.casefold()
    position = lowered.find(sample_title.casefold())
    if position < 0:
        return text
    return text[:position] + "{title}" + text[position + len(sample_title) :]


class Recorder:
    """Uma gravação por vez — duas ao mesmo tempo misturariam os toques."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._ttl = ttl_seconds
        self._recording: Recording | None = None

    @property
    def active(self) -> bool:
        self._expire_if_stale()
        return self._recording is not None

    @property
    def count(self) -> int:
        return len(self._recording.events) if self._recording else 0

    @property
    def service_id(self) -> str | None:
        return self._recording.service_id if self._recording else None

    @property
    def target(self) -> str:
        return self._recording.target if self._recording else "preset"

    def _expire_if_stale(self) -> None:
        if self._recording is None:
            return
        if self._clock() - self._recording.started_at > self._ttl:
            self._recording = None

    def start(
        self,
        *,
        service_id: str | None,
        app_id: str | None,
        target: str = "preset",
        sample_title: str = "",
    ) -> None:
        self._expire_if_stale()
        if self._recording is not None:
            raise RecorderError("Já existe uma gravação em andamento. Pare a atual primeiro.")
        self._recording = Recording(
            service_id=service_id,
            app_id=app_id,
            started_at=self._clock(),
            target=target,
            sample_title=sample_title,
        )

    def observe(self, button: str) -> None:
        """Anota um toque. Silencioso quando não há gravação — é o caminho normal."""
        self._expire_if_stale()
        if self._recording is None:
            return
        self._recording.add(button, self._clock())

    def observe_text(self, text: str) -> None:
        """Anota o que foi digitado.

        Sem isto, gravar a macro de busca capturava a navegação até o campo e parava ali
        — justamente antes do passo que faz a busca acontecer.
        """
        self._expire_if_stale()
        if self._recording is None:
            return
        self._recording.add("__text__", self._clock(), text=text)

    def cancel(self) -> None:
        self._recording = None

    def stop(self) -> tuple[list[Step], str | None]:
        """Encerra e devolve (passos, service_id). Levanta se não houver o que gravar."""
        self._expire_if_stale()
        if self._recording is None:
            raise RecorderError("Nenhuma gravação em andamento (ou ela expirou).")

        recording, self._recording = self._recording, None
        if not recording.events:
            raise RecorderError("Você não apertou nenhum botão — nada para salvar.")

        steps = compress(
            recording.events,
            started_at=recording.started_at,
            sample_title=recording.sample_title,
        )
        return steps, recording.service_id
