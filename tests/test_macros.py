"""Parsing e execução das macros do apps.yaml."""

from __future__ import annotations

import pytest

from lgremote.tv import macros
from lgremote.tv.buttons import UnknownButtonError
from lgremote.tv.macros import ButtonStep, ClearStep, EnterStep, MacroError, TextStep, WaitStep
from lgremote.tv.session import TvSession
from tests.conftest import FakeWebOsClient


def test_parse_reconhece_cada_tipo_de_passo() -> None:
    steps = macros.parse_steps(
        [
            {"button": "left", "times": 3, "delay": 0.1},
            {"wait": 2},
            {"text": "{title}"},
            {"enter": True},
            {"clear": 40},
        ]
    )
    assert steps == [
        ButtonStep("LEFT", 3, 0.1),
        WaitStep(2.0),
        TextStep("{title}"),
        EnterStep(),
        ClearStep(40),
    ]


def test_passo_sem_acao_e_erro_claro() -> None:
    with pytest.raises(MacroError, match="sem ação"):
        macros.parse_steps([{"delay": 1}])


def test_botao_invalido_no_yaml_falha_no_parse() -> None:
    """Melhor quebrar ao carregar o YAML do que no meio de uma macro rodando."""
    with pytest.raises(UnknownButtonError):
        macros.parse_steps([{"button": "SUPER_SECRETO"}])


@pytest.mark.parametrize("bad", [{"button": "UP", "times": 0}, {"button": "UP", "times": 99}])
def test_repeticao_absurda_e_rejeitada(bad: dict[str, object]) -> None:
    with pytest.raises(MacroError, match="times"):
        macros.parse_steps([bad])


def test_wait_gigante_e_rejeitado() -> None:
    with pytest.raises(MacroError, match="wait"):
        macros.parse_steps([{"wait": 600}])


def test_render_substitui_placeholders() -> None:
    assert macros.render("{title} ({year})", {"title": "Fallout", "year": "2024"}) == (
        "Fallout (2024)"
    )


def test_render_nao_quebra_com_chaves_no_titulo() -> None:
    """str.format() explodiria aqui; títulos do TMDb são texto arbitrário."""
    assert macros.render("{title}", {"title": "Tudo {sic} Bem"}) == "Tudo {sic} Bem"


def test_render_ignora_placeholder_desconhecido() -> None:
    assert macros.render("{title} {nada}", {"title": "X"}) == "X {nada}"


async def test_run_steps_executa_na_ordem(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    steps = macros.parse_steps(
        [{"button": "LEFT", "times": 2}, {"wait": 1.5}, {"text": "{title}"}, {"enter": True}]
    )

    trace = await macros.run_steps(
        session, steps, {"title": "Severance"}, sleep=instant_sleep
    )

    assert fake_tv.buttons == ["LEFT", "LEFT"]
    assert fake_tv.named("com.webos.service.ime/insertText") == [
        {"text": "Severance", "replace": 1}
    ]
    assert fake_tv.named("com.webos.service.ime/sendEnterKey") == [None]
    assert trace == ["button LEFT", "button LEFT", "wait 1.5s", "text 'Severance'", "enter"]


async def test_run_steps_espera_entre_repeticoes_mas_nao_depois_da_ultima(
    session: TvSession, instant_sleep
) -> None:
    """Um sleep sobrando ao fim de cada repetição somaria segundos à macro inteira."""
    steps = macros.parse_steps([{"button": "UP", "times": 3, "delay": 0.2}])
    await macros.run_steps(session, steps, sleep=instant_sleep)
    assert instant_sleep.waits == [0.2, 0.2]


# --- repetição variável (é assim que se chega a um episódio) -------------


def test_times_aceita_template() -> None:
    (step,) = macros.parse_steps([{"button": "RIGHT", "times": "{episode_index}"}])
    assert step == ButtonStep("RIGHT", "{episode_index}")


@pytest.mark.parametrize(
    ("episode_index", "expected"),
    [("0", 0), ("4", 4), ("30", 30)],
)
def test_times_resolve_com_o_contexto(episode_index: str, expected: int) -> None:
    step = ButtonStep("RIGHT", "{episode_index}")
    assert step.repeat({"episode_index": episode_index}) == expected


def test_times_absurdo_e_limitado_em_vez_de_disparar_400_toques() -> None:
    """Contexto errado não pode virar uma enxurrada de comandos na TV."""
    step = ButtonStep("RIGHT", "{episode_index}")
    assert step.repeat({"episode_index": "999"}) == macros.MAX_REPEAT


def test_times_negativo_vira_zero() -> None:
    step = ButtonStep("RIGHT", "{episode_index}")
    assert step.repeat({"episode_index": "-3"}) == 0


def test_times_que_nao_resolve_para_numero_e_erro_claro() -> None:
    step = ButtonStep("RIGHT", "{titulo}")
    with pytest.raises(MacroError, match="não é um número"):
        step.repeat({"titulo": "Fallout"})


async def test_macro_com_times_variavel_executa(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    steps = macros.parse_steps([{"button": "RIGHT", "times": "{episode_index}"}])

    await macros.run_steps(session, steps, {"episode_index": "3"}, sleep=instant_sleep)

    assert fake_tv.buttons == ["RIGHT", "RIGHT", "RIGHT"]


async def test_times_zero_nao_aperta_nada(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Episódio 1 = índice 0: o passo existe, mas não deve mover o foco."""
    steps = macros.parse_steps([{"button": "RIGHT", "times": "{episode_index}"}])

    await macros.run_steps(session, steps, {"episode_index": "0"}, sleep=instant_sleep)

    assert fake_tv.buttons == []


async def test_on_step_recebe_cada_passo_ao_vivo(session: TvSession, instant_sleep) -> None:
    seen: list[str] = []
    steps = macros.parse_steps([{"button": "UP"}, {"text": "oi"}])

    await macros.run_steps(session, steps, sleep=instant_sleep, on_step=seen.append)

    assert seen == ["button UP", "text 'oi'"]
