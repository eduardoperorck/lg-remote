"""Conexão, whitelist de botões e reconexão."""

from __future__ import annotations

import asyncio

import pytest
from aiowebostv import WebOsTvPairError
from aiowebostv.exceptions import WebOsTvServiceNotFoundError

from lgremote.tv.buttons import UnknownButtonError, normalize
from lgremote.tv.session import (
    REDISCOVER_COOLDOWN,
    TvError,
    TvPairError,
    TvSession,
    TvUnreachableError,
)
from tests.conftest import FakeWebOsClient


async def test_button_normaliza_minusculas(session: TvSession, fake_tv: FakeWebOsClient) -> None:
    await session.button("enter")
    assert fake_tv.buttons == ["ENTER"]


async def test_botao_fora_da_whitelist_nao_chega_na_tv(
    session: TvSession, fake_tv: FakeWebOsClient
) -> None:
    with pytest.raises(UnknownButtonError):
        await session.button("LUNA_SHELL")
    assert fake_tv.calls == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [("UP", "UP"), ("down", "DOWN"), (" enter ", "ENTER"), ("7", "7"), ("VolumeUp", "VOLUMEUP")],
)
def test_normalize_aceita_variacoes(name: str, expected: str) -> None:
    assert normalize(name) == expected


async def test_insert_text_usa_o_ime_e_nao_o_toast(
    session: TvSession, fake_tv: FakeWebOsClient
) -> None:
    """send_message do aiowebostv mostra um aviso flutuante — não digita nada."""
    await session.insert_text("The Last of Us")
    payload = fake_tv.named("com.webos.service.ime/insertText")
    assert payload == [{"text": "The Last of Us", "replace": 0}]
    assert fake_tv.named("toast") == []


async def test_desligar_manda_o_comando_mesmo_sem_estado_preenchido(
    session: TvSession, fake_tv: FakeWebOsClient
) -> None:
    """O `power_off` da aiowebostv desiste em silêncio se `tv_state.is_on` for falso —
    e em webOS antigo esse campo pode nunca ser preenchido. Se o socket está aberto, a
    TV está ligada; mandamos o comando direto."""
    fake_tv.is_on = False

    await session.power_off()

    assert fake_tv.named("command:system/turnOff") == [None]
    assert fake_tv.named("power_off") == []  # não passou pelo helper com guard


async def test_reconecta_uma_vez_quando_a_conexao_cai(
    session: TvSession, fake_tv: FakeWebOsClient
) -> None:
    fake_tv.fail_times = 1
    await session.button("HOME")
    assert fake_tv.buttons == ["HOME"]


async def test_desiste_depois_da_segunda_falha(
    session: TvSession, fake_tv: FakeWebOsClient
) -> None:
    """Insistir mais que isso só faz o celular ficar esperando uma TV que não está lá."""
    fake_tv.fail_times = 5
    with pytest.raises(TvUnreachableError):
        await session.button("HOME")


async def test_guarda_a_chave_nova_no_primeiro_pareamento() -> None:
    saved: list[str] = []
    fake = FakeWebOsClient("10.0.0.5", None)
    fake.client_key = "chave-nova"
    tv = TvSession("10.0.0.5", None, client_factory=lambda h, k: fake, on_client_key=saved.append)

    await tv.connect()

    assert saved == ["chave-nova"]
    assert tv.client_key == "chave-nova"


async def test_host_inalcancavel_vira_erro_legivel() -> None:
    class DeadClient(FakeWebOsClient):
        async def connect(self) -> bool:
            raise OSError("no route to host")

    tv = TvSession("10.0.0.9", "k", client_factory=lambda h, k: DeadClient(h, k))
    with pytest.raises(TvUnreachableError, match=r"10\.0\.0\.9"):
        await tv.connect()


# --- tradução de erro: o que virava traceback na tela --------------------


async def test_recusa_de_pareamento_nao_escapa_como_traceback(
    fake_tv: FakeWebOsClient,
) -> None:
    """WebOsTvPairError é IRMÃO do CommandError, não filho — por isso escapava.

    Era ele que chegava cru até o console como `403 User denied access`.
    """
    fake_tv.connect_error = WebOsTvPairError("403 User denied access")
    tv = TvSession("10.0.0.5", None, client_factory=lambda h, k: fake_tv)

    with pytest.raises(TvPairError):
        await tv.connect()


async def test_chave_salva_recusada_manda_parear_de_novo(fake_tv: FakeWebOsClient) -> None:
    fake_tv.connect_error = WebOsTvPairError("403 User denied access")
    tv = TvSession("10.0.0.5", "chave-velha", client_factory=lambda h, k: fake_tv)

    with pytest.raises(TvPairError, match="lgremote pair"):
        await tv.connect()


async def test_erro_de_servico_da_biblioteca_tambem_e_traduzido(
    fake_tv: FakeWebOsClient,
) -> None:
    """Só o CommandError era capturado; os outros filhos do WebOsTvError escapavam."""
    fake_tv.connect_error = WebOsTvServiceNotFoundError("sem serviço")
    tv = TvSession("10.0.0.5", "k", client_factory=lambda h, k: fake_tv)

    with pytest.raises(TvUnreachableError):
        await tv.connect()


async def test_falha_de_conexao_nao_deixa_client_pendurado(fake_tv: FakeWebOsClient) -> None:
    """Um client meio-conectado guardado aqui seria reusado e falharia sem pista."""
    fake_tv.connect_error = WebOsTvPairError("403")
    tv = TvSession("10.0.0.5", None, client_factory=lambda h, k: fake_tv)

    with pytest.raises(TvPairError):
        await tv.connect()

    assert tv.connected is False


def test_todo_erro_de_tv_tem_a_mesma_base() -> None:
    """O CLI captura só `TvError`: um irmão de fora volta a virar traceback."""
    assert issubclass(TvUnreachableError, TvError)
    assert issubclass(TvPairError, TvError)


# --- a TV que mudou de IP ------------------------------------------------


class MovingTv:
    """Sessão cuja TV só atende em `alive_at`, com contagem de varreduras.

    `found` é o que a varredura devolve — None imita a TV que está simplesmente
    desligada, que é o caso em que a carência precisa segurar.
    """

    def __init__(self, *, alive_at: str | None, found: str | None) -> None:
        self.scans = 0
        self.hosts: list[str] = []
        self.time = 1000.0

        def factory(host: str, key: str | None) -> FakeWebOsClient:
            fake = FakeWebOsClient(host, key)
            if host != alive_at:
                fake.connect_error = OSError("no route to host")
            return fake

        async def rediscover() -> str | None:
            self.scans += 1
            return found

        self.session = TvSession(
            "192.168.2.104",
            "chave",
            client_factory=factory,
            rediscover=rediscover,
            on_host_change=self.hosts.append,
            now=lambda: self.time,
        )


async def test_ip_trocado_e_reencontrado_e_gravado() -> None:
    """O ponto da mudança: o roteador troca o IP e o usuário não fica sabendo."""
    tv = MovingTv(alive_at="192.168.2.117", found="192.168.2.117")

    await tv.session.button("HOME")

    assert tv.session.host == "192.168.2.117"
    assert tv.hosts == ["192.168.2.117"]


async def test_tv_desligada_nao_vira_tempestade_de_varredura() -> None:
    """O PWA pergunta o estado a cada 5s; sem carência seria uma varredura por pergunta."""
    tv = MovingTv(alive_at=None, found=None)

    for _ in range(5):
        with pytest.raises(TvUnreachableError):
            await tv.session.button("HOME")

    assert tv.scans == 1


async def test_varredura_volta_a_ser_permitida_depois_da_carencia() -> None:
    tv = MovingTv(alive_at=None, found=None)

    with pytest.raises(TvUnreachableError):
        await tv.session.button("HOME")
    tv.time += REDISCOVER_COOLDOWN + 1
    with pytest.raises(TvUnreachableError):
        await tv.session.button("HOME")

    assert tv.scans == 2


async def test_comandos_simultaneos_nao_multiplicam_a_varredura() -> None:
    tv = MovingTv(alive_at=None, found=None)

    results = await asyncio.gather(
        tv.session.button("HOME"), tv.session.button("BACK"), return_exceptions=True
    )

    assert all(isinstance(item, TvUnreachableError) for item in results)
    assert tv.scans == 1


async def test_chave_recusada_nao_dispara_varredura(fake_tv: FakeWebOsClient) -> None:
    """A TV RESPONDEU para recusar: procurá-la na rede seria caçar o que já foi achado."""
    scans = []

    async def rediscover() -> str | None:
        scans.append(1)
        return None

    fake_tv.connect_error = WebOsTvPairError("403")
    session = TvSession(
        "10.0.0.5", "chave", client_factory=lambda h, k: fake_tv, rediscover=rediscover
    )

    with pytest.raises(TvPairError):
        await session.connect()

    assert scans == []


async def test_varredura_que_quebra_nao_esconde_o_erro_original() -> None:
    """Procurar a TV é socorro: se o socorro falha, quem aparece é o problema de origem."""

    async def rediscover() -> str | None:
        raise RuntimeError("a varredura explodiu")

    def factory(host: str, key: str | None) -> FakeWebOsClient:
        fake = FakeWebOsClient(host, key)
        fake.connect_error = OSError("no route to host")
        return fake

    session = TvSession("10.0.0.5", "k", client_factory=factory, rediscover=rediscover)

    with pytest.raises(TvUnreachableError):
        await session.connect()


async def test_sessao_sem_rediscover_continua_como_antes(fake_tv: FakeWebOsClient) -> None:
    fake_tv.connect_error = OSError("no route to host")
    session = TvSession("10.0.0.5", "k", client_factory=lambda h, k: fake_tv)

    with pytest.raises(TvUnreachableError):
        await session.connect()
