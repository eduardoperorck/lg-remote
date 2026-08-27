"""Ramos interativos do assistente.

Um terminal real não é testável aqui, então trocamos as perguntas por respostas
enfileiradas. O que importa é o que o assistente FAZ com cada resposta.
"""

from __future__ import annotations

import pytest

from lgremote import setup_wizard
from lgremote.config import Settings
from lgremote.setup_wizard import Abort, _ask_mac, _ask_pin, _choose_host
from lgremote.tv.discovery import TvCandidate
from lgremote.tv.session import TvSession
from tests.conftest import FakeWebOsClient


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Enfileira respostas; cada pergunta consome a próxima."""
    queue: list[str] = []

    def fake_ask(prompt: str, default: str = "") -> str:
        if not queue:
            raise AssertionError(f"pergunta sem resposta preparada: {prompt!r}")
        answer = queue.pop(0)
        return answer if answer != "" else default

    def fake_confirm(prompt: str, *, default: bool = True) -> bool:
        if not queue:
            raise AssertionError(f"confirmação sem resposta preparada: {prompt!r}")
        return queue.pop(0).lower().startswith("s")

    monkeypatch.setattr(setup_wizard, "_ask", fake_ask)
    monkeypatch.setattr(setup_wizard, "_confirm", fake_confirm)
    return queue


@pytest.fixture
def no_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reprova o teste se a varredura /24 for acionada."""

    async def explode(**_: object) -> list[TvCandidate]:
        raise AssertionError("não deveria varrer a rede")

    monkeypatch.setattr(setup_wizard, "find_tvs", explode)


# --- escolha da TV -------------------------------------------------------


async def test_host_informado_pula_a_varredura(
    answers: list[str], no_scan: None
) -> None:
    assert (await _choose_host("192.168.0.10", _saved())).host == "192.168.0.10"


async def test_uma_tv_encontrada_pede_confirmacao(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv("192.168.0.10")]))
    answers.append("s")
    assert (await _choose_host(None, _saved())).host == "192.168.0.10"


async def test_confirmacao_negada_permite_digitar_outro_ip(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duas TVs LG na casa: a identificação rotula, mas quem decide é o usuário."""
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv("192.168.0.99")]))
    answers.extend(["n", "192.168.0.10"])
    assert (await _choose_host(None, _saved())).host == "192.168.0.10"


async def test_varias_tvs_deixam_escolher(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_wizard, "find_tvs", _returns([_tv("10.0.0.2"), _tv("10.0.0.3", "OLED55C1")])
    )
    answers.append("2")
    assert (await _choose_host(None, _saved())).host == "10.0.0.3"


async def test_escolha_invalida_aborta_em_vez_de_chutar(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv("10.0.0.2"), _tv("10.0.0.3")]))
    answers.append("9")
    with pytest.raises(Abort):
        await _choose_host(None, _saved())


async def test_nenhuma_tv_encontrada_cai_para_ip_manual(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([]))
    answers.append("192.168.1.50")
    assert (await _choose_host(None, _saved())).host == "192.168.1.50"


# --- reaproveitar o que já está salvo -------------------------------------


async def test_ip_salvo_que_responde_dispensa_a_varredura(
    answers: list[str], monkeypatch: pytest.MonkeyPatch, no_scan: None
) -> None:
    """É este atalho que faz rodar o setup de novo custar um segundo."""
    monkeypatch.setattr(setup_wizard, "identify_any", _identifies({"192.168.2.104": _tv_uuid()}))

    chosen = await _choose_host(None, _saved(host="192.168.2.104", uuid="tv-uuid"))

    assert chosen.host == "192.168.2.104"
    assert chosen.uuid == "tv-uuid"


async def test_ip_salvo_mudo_cai_para_a_varredura(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "identify_any", _identifies({}))
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv("10.0.0.7")]))
    answers.append("s")

    assert (await _choose_host(None, _saved(host="192.168.2.104"))).host == "10.0.0.7"


async def test_tv_reconhecida_pelo_uuid_no_ip_novo_nao_pergunta_nada(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trocar de IP é assunto do roteador, não uma decisão a passar para o usuário.

    A fila de respostas fica vazia de propósito: qualquer pergunta quebra o teste.
    """
    monkeypatch.setattr(setup_wizard, "identify_any", _identifies({}))
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv_uuid("192.168.2.117")]))

    chosen = await _choose_host(None, _saved(host="192.168.2.104", uuid="tv-uuid"))

    assert chosen.host == "192.168.2.117"


async def test_uuid_de_outra_tv_no_ip_salvo_forca_a_varredura(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """O IP foi reaproveitado por outro aparelho — confiar nele mandaria comando à toa."""
    monkeypatch.setattr(
        setup_wizard, "identify_any", _identifies({"192.168.2.104": _tv_uuid(uuid="outra")})
    )
    monkeypatch.setattr(setup_wizard, "find_tvs", _returns([_tv_uuid("192.168.2.117")]))

    chosen = await _choose_host(None, _saved(host="192.168.2.104", uuid="tv-uuid"))

    assert chosen.host == "192.168.2.117"


# --- PIN -----------------------------------------------------------------


def test_pin_invalido_e_perguntado_de_novo(answers: list[str]) -> None:
    answers.extend(["12", "abcd", "123456"])
    assert _ask_pin() == "123456"


def test_pin_vazio_aceita_a_sugestao(answers: list[str]) -> None:
    """O default vem do próprio prompt; responder vazio precisa aproveitá-lo."""
    answers.append("")
    pin = _ask_pin()
    assert pin.isdigit()
    assert len(pin) == 4


def test_reconfiguracao_mantem_o_pin_atual_com_enter(answers: list[str]) -> None:
    """Rodar o setup de novo não pode trocar o PIN que já está no celular."""
    answers.append("")
    assert _ask_pin("2503") == "2503"


# --- MAC -----------------------------------------------------------------


def test_mac_detectado_nao_incomoda_o_usuario(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "arp_mac", lambda ip: "aa:bb:cc:dd:ee:ff")
    assert _ask_mac("192.168.0.10") == "aa:bb:cc:dd:ee:ff"


def test_mac_invalido_e_perguntado_de_novo(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard, "arp_mac", lambda ip: None)
    answers.extend(["nao-e-mac", "AA:BB:CC:DD:EE:FF"])
    assert _ask_mac("192.168.0.10") == "AA:BB:CC:DD:EE:FF"


def test_mac_e_opcional(answers: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem MAC o controle funciona inteiro, menos ligar a TV — não pode travar aqui."""
    monkeypatch.setattr(setup_wizard, "arp_mac", lambda ip: None)
    answers.append("")
    assert _ask_mac("192.168.0.10") == ""


def test_mac_ja_salvo_sobrevive_ao_enter(
    answers: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tabela ARP esfria; o Enter não pode apagar o MAC que já estava funcionando."""
    monkeypatch.setattr(setup_wizard, "arp_mac", lambda ip: None)
    answers.append("")
    assert _ask_mac("192.168.0.10", "aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"


def _tv(host: str, model: str | None = None) -> TvCandidate:
    return TvCandidate(host, 3001, release_version="6.4.0", model=model)


def _tv_uuid(host: str = "192.168.2.104", uuid: str = "tv-uuid") -> TvCandidate:
    return TvCandidate(host, 3001, release_version="6.4.0", uuid=uuid)


def _saved(*, host: str = "", uuid: str = "", client_key: str = "") -> Settings:
    """Settings de mentira: o .env real não pode influenciar o teste."""
    return Settings(tv_host=host, tv_uuid=uuid, tv_client_key=client_key)


def _identifies(known: dict[str, TvCandidate]):  # type: ignore[no-untyped-def]
    async def stub(host: str, **_: object) -> TvCandidate | None:
        return known.get(host)

    return stub


def _returns(value: list[TvCandidate]):  # type: ignore[no-untyped-def]
    async def stub(**_: object) -> list[TvCandidate]:
        return value

    return stub


# --- reaproveitar (ou não) a chave salva ---------------------------------


def _session_that(error: Exception | None):  # type: ignore[no-untyped-def]
    """Fábrica de TvSession cuja conexão falha do jeito pedido."""

    def factory(host: str, key: str | None, **kwargs: object) -> TvSession:
        fake = FakeWebOsClient(host, key)
        fake.connect_error = error
        # A classe real, não `setup_wizard.TvSession`: no teste ela já é esta fábrica.
        return TvSession(host, key, client_factory=lambda h, k: fake, **kwargs)  # type: ignore[arg-type]

    return factory


async def test_chave_boa_e_reaproveitada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_wizard, "TvSession", _session_that(None))

    assert await setup_wizard._reuse_key(setup_wizard.ChosenTv("10.0.0.5"), "chave") == "chave"


async def test_chave_recusada_pede_novo_pareamento(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiowebostv import WebOsTvPairError

    monkeypatch.setattr(
        setup_wizard, "TvSession", _session_that(WebOsTvPairError("403 User denied access"))
    )

    assert await setup_wizard._reuse_key(setup_wizard.ChosenTv("10.0.0.5"), "chave") is None


async def test_tv_desligada_no_teste_da_chave_nao_vira_repareamento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mandar parear com uma TV que está na tomada errada só gera frustração."""
    monkeypatch.setattr(setup_wizard, "TvSession", _session_that(OSError("no route to host")))
    monkeypatch.setattr(setup_wizard, "identify_any", _identifies({}))

    with pytest.raises(setup_wizard.TvUnreachableError):
        await setup_wizard._reuse_key(setup_wizard.ChosenTv("10.0.0.5"), "chave")


async def test_prompt_ignorado_conta_como_chave_morta(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TV responde ao identify: então ela está viva, e o timeout foi no pedido."""
    monkeypatch.setattr(setup_wizard, "TvSession", _session_that(TimeoutError()))
    monkeypatch.setattr(setup_wizard, "identify_any", _identifies({"10.0.0.5": _tv("10.0.0.5")}))

    assert await setup_wizard._reuse_key(setup_wizard.ChosenTv("10.0.0.5"), "chave") is None
