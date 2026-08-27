"""Registro na TV: o caminho que estourava um traceback na cara do usuário."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiohttp import web
from aiowebostv.handshake import REGISTRATION_MESSAGE

from lgremote.tv import pairing
from lgremote.tv.pairing import (
    PairError,
    PairRefusedError,
    PairTimeoutError,
    PairUnreachableError,
    pair,
)

HELLO = {"deviceType": "tv", "deviceOS": "webOS", "deviceUUID": "tv-uuid"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@asynccontextmanager
async def fake_tv(behaviour: str, *, delay: float = 0.0) -> AsyncIterator[tuple[int, list[Any]]]:
    """TV de mentira que responde ao registro do jeito que `behaviour` mandar.

    `seen` guarda as mensagens recebidas: é o que permite conferir que mandamos o
    manifesto certo, e na ordem que o firmware novo exige.
    """
    seen: list[Any] = []

    async def handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        async for message in websocket:
            data = message.json()
            seen.append(data)
            if data.get("type") == "hello":
                await websocket.send_json({"type": "hello", "payload": HELLO})
            elif "getSystemInfo" in str(data.get("uri", "")):
                await websocket.send_json({"type": "response", "payload": {"returnValue": True}})
            elif data.get("type") == "register":
                await _register(websocket, behaviour, delay)
        return websocket

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield port, seen
    finally:
        await runner.cleanup()


async def _register(websocket: web.WebSocketResponse, behaviour: str, delay: float) -> None:
    if behaviour == "aceita_direto":
        await websocket.send_json({"type": "registered", "payload": {"client-key": "chave-nova"}})
        return
    if behaviour == "nega_na_hora":
        await websocket.send_json({"type": "error", "error": "403 User denied access"})
        return

    await websocket.send_json({"type": "response", "payload": {"pairingType": "PROMPT"}})
    if delay:
        await asyncio.sleep(delay)
    if behaviour == "pergunta_e_aceita":
        await websocket.send_json({"type": "registered", "payload": {"client-key": "chave-nova"}})
    elif behaviour == "pergunta_e_nega":
        await websocket.send_json({"type": "error", "error": "403 User denied access"})
    # "pergunta_e_cala": nada mais é enviado, o cliente tem de estourar o prazo


def _clock(*readings: float) -> Callable[[], float]:
    """Relógio de mentira: mede a demora da recusa sem gastar o tempo de verdade."""
    values = list(readings)

    def now() -> float:
        return values.pop(0) if len(values) > 1 else values[0]

    return now


# --- caminho feliz -------------------------------------------------------


async def test_prompt_aceito_devolve_a_chave() -> None:
    async with fake_tv("pergunta_e_aceita") as (port, _):
        assert await pair("127.0.0.1", port) == "chave-nova"


async def test_tv_que_ja_conhece_o_pc_registra_sem_perguntar() -> None:
    async with fake_tv("aceita_direto") as (port, _):
        assert await pair("127.0.0.1", port) == "chave-nova"


async def test_registro_usa_o_manifesto_da_aiowebostv() -> None:
    """Manifesto igual, permissões iguais: a chave daqui serve para a aiowebostv.

    Se este teste cair, a chave obtida aqui pode não abrir tudo o que o controle usa.
    """
    async with fake_tv("aceita_direto") as (port, seen):
        await pair("127.0.0.1", port)

    registration = next(msg for msg in seen if msg.get("type") == "register")
    assert registration["payload"]["manifest"] == REGISTRATION_MESSAGE["payload"]["manifest"]
    assert registration["payload"]["client-key"] is None


async def test_sysinfo_vem_antes_do_registro() -> None:
    """Firmware novo exige o getSystemInfo antes de registrar — a ordem é o contrato."""
    async with fake_tv("aceita_direto") as (port, seen):
        await pair("127.0.0.1", port)

    kinds = [msg.get("type") for msg in seen]
    uris = [str(msg.get("uri", "")) for msg in seen]
    assert kinds.index("hello") < next(i for i, uri in enumerate(uris) if "getSystemInfo" in uri)
    assert next(i for i, uri in enumerate(uris) if "getSystemInfo" in uri) < kinds.index("register")


# --- os dois "não", que pedem conselhos opostos --------------------------


async def test_recusa_imediata_e_diagnosticada_como_tv_que_nao_perguntou() -> None:
    """O caso real: 403 sem nada aparecer na tela. Mandar "aceite na TV" não ajudaria."""
    async with fake_tv("nega_na_hora") as (port, _):
        with pytest.raises(PairRefusedError) as caught:
            await pair("127.0.0.1", port)

    assert caught.value.prompted is False
    assert "standby" in str(caught.value)
    assert caught.value.detail == "403 User denied access"


async def test_recusa_depois_de_um_prompt_demorado_e_recusa_de_gente() -> None:
    async with fake_tv("pergunta_e_nega") as (port, _):
        with pytest.raises(PairRefusedError) as caught:
            # 0s ao começar a esperar, 9s ao chegar a resposta: alguém leu e recusou.
            await pair("127.0.0.1", port, now=_clock(0.0, 9.0))

    assert caught.value.prompted is True
    assert "standby" not in str(caught.value)


async def test_recusa_rapida_depois_do_prompt_ainda_conta_como_automatica() -> None:
    """Recusa gravada na TV: ela desenha o pedido e responde antes de dar tempo de ler."""
    async with fake_tv("pergunta_e_nega") as (port, _):
        with pytest.raises(PairRefusedError) as caught:
            await pair("127.0.0.1", port, now=_clock(0.0, 0.3))

    assert caught.value.prompted is False


async def test_ajuda_da_recusa_automatica_cita_as_tres_causas() -> None:
    """O texto É a correção: se ele encolher, o usuário volta a ficar sem saída."""
    texto = pairing.REFUSED_WITHOUT_PROMPT
    assert "standby" in texto
    assert "histórico de conexões" in texto
    assert "Mobile TV On" in texto


# --- prazo ---------------------------------------------------------------


async def test_ninguem_aceita_vira_erro_proprio_e_nao_tv_sumida() -> None:
    async with fake_tv("pergunta_e_cala") as (port, _):
        with pytest.raises(PairTimeoutError):
            await pair("127.0.0.1", port, prompt_timeout=0.2)


async def test_prazo_padrao_e_muito_maior_que_o_da_aiowebostv() -> None:
    """A aiowebostv dá 10s (RECEIVE_TIMEOUT) — menos do que leva para achar o controle."""
    assert pairing.PAIR_TIMEOUT >= 60


async def test_prazo_maior_salva_quem_demorou_a_aceitar() -> None:
    async with fake_tv("pergunta_e_aceita", delay=0.3) as (port, _):
        assert await pair("127.0.0.1", port, prompt_timeout=5.0) == "chave-nova"


# --- o que não pode virar traceback --------------------------------------


async def test_porta_morta_vira_erro_explicado() -> None:
    with pytest.raises(PairUnreachableError):
        await pair("127.0.0.1", _free_port(), prompt_timeout=0.2)


async def test_todo_erro_de_pareamento_tem_a_mesma_base() -> None:
    """O `main` captura só a base — se algum ficar de fora, volta o traceback cru."""
    for kind in (PairRefusedError, PairTimeoutError, PairUnreachableError):
        assert issubclass(kind, PairError)
