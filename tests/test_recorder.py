"""Gravação de sequências e compressão em passos."""

from __future__ import annotations

import pytest

from lgremote.tv.macros import ButtonStep, TextStep, WaitStep
from lgremote.tv.recorder import ButtonEvent, Recorder, RecorderError, compress


class FakeClock:
    """Relógio controlado — gravação depende de tempo e teste não pode dormir."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def events(*pairs: tuple[str, float]) -> list[ButtonEvent]:
    return [ButtonEvent(name, at) for name, at in pairs]


# --- compressão ----------------------------------------------------------


def test_toques_repetidos_viram_times() -> None:
    """Sem isto, ir até o fim de um menu viraria 8 linhas de RIGHT no YAML."""
    steps = compress(events(("RIGHT", 1.0), ("RIGHT", 1.1), ("RIGHT", 1.2)), started_at=0.0)
    assert steps == [ButtonStep("RIGHT", 3)]


def test_pausa_curta_e_ignorada() -> None:
    """Abaixo do limite é ritmo de dedo, não intenção — o delay padrão já cobre."""
    steps = compress(events(("UP", 1.0), ("DOWN", 1.3)), started_at=0.0)
    assert steps == [ButtonStep("UP"), ButtonStep("DOWN")]


def test_pausa_longa_vira_wait() -> None:
    steps = compress(events(("ENTER", 1.0), ("DOWN", 3.2)), started_at=0.0)
    assert steps == [ButtonStep("ENTER"), WaitStep(2.2), ButtonStep("DOWN")]


def test_hesitacao_gigante_e_limitada() -> None:
    """Você parou para olhar a TV; isso não deve virar 40s de espera na macro."""
    steps = compress(events(("ENTER", 1.0), ("DOWN", 41.0)), started_at=0.0)
    assert steps == [ButtonStep("ENTER"), WaitStep(5.0), ButtonStep("DOWN")]


def test_tempo_ate_o_primeiro_toque_e_descartado() -> None:
    """É o tempo de pegar o celular, não parte da sequência."""
    steps = compress(events(("UP", 30.0)), started_at=0.0)
    assert steps == [ButtonStep("UP")]


def test_wait_separa_repeticoes_do_mesmo_botao() -> None:
    """Dois grupos de RIGHT com pausa entre eles não podem virar um grupo só."""
    steps = compress(
        events(("RIGHT", 1.0), ("RIGHT", 1.1), ("RIGHT", 5.0), ("RIGHT", 5.1)), started_at=0.0
    )
    assert steps == [ButtonStep("RIGHT", 2), WaitStep(3.9), ButtonStep("RIGHT", 2)]


def test_gravacao_vazia_nao_gera_passos() -> None:
    assert compress([], started_at=0.0) == []


# --- ciclo de vida -------------------------------------------------------


def test_fluxo_completo() -> None:
    clock = FakeClock()
    recorder = Recorder(clock=clock)

    recorder.start(service_id="max", app_id="com.wbd.stream")
    assert recorder.active

    for button in ("DOWN", "DOWN", "ENTER"):
        clock.advance(0.2)
        recorder.observe(button)

    assert recorder.count == 3
    steps, service_id = recorder.stop()

    assert service_id == "max"
    assert steps == [ButtonStep("DOWN", 2), ButtonStep("ENTER")]
    assert not recorder.active


def test_toques_fora_de_gravacao_sao_ignorados() -> None:
    """O caminho normal do direcional passa pelo mesmo lugar — não pode acumular nada."""
    recorder = Recorder()
    recorder.observe("UP")
    recorder.observe("DOWN")
    assert recorder.count == 0
    assert not recorder.active


def test_duas_gravacoes_ao_mesmo_tempo_sao_recusadas() -> None:
    recorder = Recorder()
    recorder.start(service_id="max", app_id=None)
    with pytest.raises(RecorderError, match="andamento"):
        recorder.start(service_id="disney", app_id=None)


def test_parar_sem_gravar_e_erro_claro() -> None:
    with pytest.raises(RecorderError, match="Nenhuma gravação"):
        Recorder().stop()


def test_parar_sem_ter_apertado_nada_avisa() -> None:
    recorder = Recorder()
    recorder.start(service_id="max", app_id=None)
    with pytest.raises(RecorderError, match="nenhum botão"):
        recorder.stop()


def test_gravacao_esquecida_expira() -> None:
    """Senão ela captura toques de horas depois, que não são dela."""
    clock = FakeClock()
    recorder = Recorder(clock=clock, ttl_seconds=60)
    recorder.start(service_id="max", app_id=None)

    clock.advance(61)

    assert not recorder.active
    recorder.observe("UP")
    assert recorder.count == 0


def test_cancelar_descarta_tudo() -> None:
    recorder = Recorder()
    recorder.start(service_id="max", app_id=None)
    recorder.observe("UP")
    recorder.cancel()
    assert not recorder.active


def test_gravacao_longa_demais_e_barrada() -> None:
    """Um limite evita que um toque preso gere um YAML de milhares de linhas."""
    clock = FakeClock()
    recorder = Recorder(clock=clock)
    recorder.start(service_id="max", app_id=None)
    with pytest.raises(RecorderError, match="longa demais"):
        for _ in range(300):
            clock.advance(0.05)
            recorder.observe("UP")


# --- gravar as macros do apps.yaml ---------------------------------------


def test_texto_digitado_entra_na_macro() -> None:
    """Sem isto a gravação da busca parava no campo de texto — antes do passo útil."""
    clock = FakeClock()
    recorder = Recorder(clock=clock)
    recorder.start(service_id="max", app_id=None, target="search")

    clock.advance(1.0)
    recorder.observe("ENTER")
    clock.advance(1.0)
    recorder.observe_text("The Last of Us")

    steps, _ = recorder.stop()

    assert TextStep("The Last of Us") in steps


def test_titulo_digitado_vira_placeholder() -> None:
    """É o que torna a macro reutilizável: gravada uma vez, serve para qualquer título."""
    clock = FakeClock()
    recorder = Recorder(clock=clock)
    recorder.start(
        service_id="max", app_id=None, target="search", sample_title="The Last of Us"
    )

    clock.advance(1.0)
    recorder.observe_text("The Last of Us")

    steps, _ = recorder.stop()

    assert steps == [TextStep("{title}")]


def test_titulo_casa_sem_diferenciar_maiusculas() -> None:
    """O teclado da TV pode devolver capitalização diferente da que você informou."""
    clock = FakeClock()
    recorder = Recorder(clock=clock)
    recorder.start(service_id="max", app_id=None, target="search", sample_title="fallout")

    clock.advance(1.0)
    recorder.observe_text("Fallout")

    steps, _ = recorder.stop()

    assert steps == [TextStep("{title}")]


def test_texto_sem_o_titulo_fica_literal() -> None:
    """Digitar outra coisa não pode virar {title}: seria uma macro que busca errado."""
    clock = FakeClock()
    recorder = Recorder(clock=clock)
    recorder.start(service_id="max", app_id=None, target="search", sample_title="Fallout")

    clock.advance(1.0)
    recorder.observe_text("configurações")

    steps, _ = recorder.stop()

    assert steps == [TextStep("configurações")]


def test_alvo_padrao_continua_sendo_preset() -> None:
    """Gravar legenda/áudio é o caminho antigo e não pode ter mudado."""
    recorder = Recorder(clock=FakeClock())
    recorder.start(service_id="max", app_id=None)

    assert recorder.target == "preset"
