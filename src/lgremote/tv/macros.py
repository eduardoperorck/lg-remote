"""Macros de navegação: a sequência que leva um app até a busca e digita o título.

Por que macro e não deep link: o webOS aceita `params.contentTarget`, mas o formato
é definido por cada app e não é documentado para Max/Disney+. A macro reproduz o que
uma pessoa faria com o controle — e isso funciona em qualquer app, hoje e depois.

Como é frágil por natureza (depende do layout do app), mora no YAML, não aqui.
Quando o Max mudar de tela, você ajusta `config/apps.yaml` e roda `lgremote try-title`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lgremote.tv import buttons

Sleeper = Callable[[float], Awaitable[None]]

MAX_REPEAT = 30
MAX_WAIT_SECONDS = 30.0


class MacroError(ValueError):
    """Passo inválido no YAML."""


class SupportsMacroSteps(Protocol):
    """O pedaço da TvSession que uma macro usa (facilita testar sem TV)."""

    async def button(self, name: str) -> None: ...
    async def insert_text(self, text: str, *, replace: bool = False) -> None: ...
    async def send_enter(self) -> None: ...
    async def delete_characters(self, count: int) -> None: ...


@dataclass(frozen=True)
class ButtonStep:
    """Um botão, repetido N vezes.

    `times` aceita um template (ex.: "{episode_index}") porque é assim que se chega a um
    episódio específico: grava-se a navegação até o episódio 1 e o número faz o resto.
    """

    name: str
    times: int | str = 1
    delay: float = 0.2

    def repeat(self, context: Mapping[str, str]) -> int:
        """Resolve quantas vezes apertar, já com o contexto da execução."""
        if isinstance(self.times, int):
            return self.times

        rendered = render(self.times, context).strip()
        try:
            resolved = int(rendered)
        except ValueError as exc:
            raise MacroError(
                f"'times' virou {rendered!r}, que não é um número. Template: {self.times!r}"
            ) from exc
        # Um índice fora da faixa significa contexto errado (ou episódio inexistente).
        # Prender no limite é melhor que apertar 400 vezes e sair do ar.
        return max(0, min(resolved, MAX_REPEAT))


@dataclass(frozen=True)
class WaitStep:
    seconds: float


@dataclass(frozen=True)
class TextStep:
    template: str


@dataclass(frozen=True)
class EnterStep:
    pass


@dataclass(frozen=True)
class ClearStep:
    count: int


Step = ButtonStep | WaitStep | TextStep | EnterStep | ClearStep


def _as_positive_float(value: Any, field: str, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MacroError(f"{field} precisa ser um número, recebi {value!r}") from exc
    if number < 0 or number > maximum:
        raise MacroError(f"{field} deve ficar entre 0 e {maximum}, recebi {number}")
    return number


def _parse_times(raw: Any) -> int | str:
    """`times` pode ser um número fixo ou um template resolvido na execução."""
    if isinstance(raw, str) and "{" in raw:
        return raw
    try:
        times = int(raw)
    except (TypeError, ValueError) as exc:
        raise MacroError(f"'times' precisa ser número ou template, recebi {raw!r}") from exc
    if not 1 <= times <= MAX_REPEAT:
        raise MacroError(f"'times' deve ficar entre 1 e {MAX_REPEAT}, recebi {times}")
    return times


def parse_step(raw: Mapping[str, Any]) -> Step:
    """Converte um item do YAML num passo. Erra alto: YAML torto vira erro claro."""
    if "button" in raw:
        return ButtonStep(
            name=buttons.normalize(str(raw["button"])),
            times=_parse_times(raw.get("times", 1)),
            delay=_as_positive_float(raw.get("delay", 0.2), "delay", 5.0),
        )
    if "wait" in raw:
        return WaitStep(_as_positive_float(raw["wait"], "wait", MAX_WAIT_SECONDS))
    if "text" in raw:
        return TextStep(str(raw["text"]))
    if "enter" in raw:
        return EnterStep()
    if "clear" in raw:
        count = int(raw["clear"])
        if not 1 <= count <= 200:
            raise MacroError(f"'clear' deve ficar entre 1 e 200, recebi {count}")
        return ClearStep(count)
    raise MacroError(f"Passo sem ação reconhecida: {dict(raw)!r}")


def parse_steps(raw_steps: Sequence[Mapping[str, Any]] | None) -> list[Step]:
    return [parse_step(step) for step in (raw_steps or [])]


def step_to_yaml(step: Step) -> str:
    """Serializa um passo no estilo compacto que o apps.yaml usa à mão.

    Escrito aqui, e não com o PyYAML, porque o `{button: UP, times: 3}` de uma linha só
    é o que torna o arquivo legível e editável — o dump padrão quebraria cada chave numa
    linha e o arquivo deixaria de convidar a edição manual.
    """
    match step:
        case ButtonStep(name, times, delay):
            parts = [f"button: {name}"]
            if times != 1:
                # Template vai entre aspas: `times: {episode_index}` sem elas é um mapa.
                parts.append(f"times: {times!r}" if isinstance(times, str) else f"times: {times}")
            if delay != 0.2:
                parts.append(f"delay: {delay}")
            return "{" + ", ".join(parts) + "}"
        case WaitStep(seconds):
            return "{" + f"wait: {seconds}" + "}"
        case TextStep(template):
            return "{" + f'text: "{template}"' + "}"
        case EnterStep():
            return "{enter: true}"
        case ClearStep(count):
            return "{" + f"clear: {count}" + "}"
    raise MacroError(f"Passo que não sei serializar: {step!r}")


def render(template: str, context: Mapping[str, str]) -> str:
    """Substitui {title}, {year}... sem usar str.format.

    Títulos vêm do TMDb e podem conter chaves; format() explodiria ou vazaria atributos.
    """
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


async def run_steps(
    session: SupportsMacroSteps,
    steps: Sequence[Step],
    context: Mapping[str, str] | None = None,
    *,
    sleep: Sleeper = asyncio.sleep,
    on_step: Callable[[str], None] | None = None,
) -> list[str]:
    """Executa a macro e devolve o rastro do que fez.

    `on_step` recebe cada passo assim que acontece: é o que permite ao `try-title`
    imprimir ao vivo enquanto você olha a TV para descobrir onde a macro erra.
    """
    ctx = dict(context or {})
    trace: list[str] = []

    def record(entry: str) -> None:
        trace.append(entry)
        if on_step is not None:
            on_step(entry)

    for step in steps:
        match step:
            case ButtonStep() as button_step:
                repeat = button_step.repeat(ctx)
                for index in range(repeat):
                    await session.button(button_step.name)
                    record(f"button {button_step.name}")
                    if button_step.delay and index < repeat - 1:
                        await sleep(button_step.delay)
            case WaitStep(seconds):
                await sleep(seconds)
                record(f"wait {seconds}s")
            case TextStep(template):
                text = render(template, ctx)
                await session.insert_text(text, replace=True)
                record(f"text {text!r}")
            case EnterStep():
                await session.send_enter()
                record("enter")
            case ClearStep(count):
                await session.delete_characters(count)
                record(f"clear {count}")

    return trace
