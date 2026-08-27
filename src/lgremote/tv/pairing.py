"""Registro (pareamento) na TV, com prazo humano e diagnóstico do "não".

Por que não usar o `client.connect()` da aiowebostv aqui: ela dá **10 segundos** para
alguém pegar o controle e aceitar (`RECEIVE_TIMEOUT`, constante de módulo), e o erro
que ela levanta joga fora a única informação que resolve o caso difícil — quanto tempo
a TV demorou para negar. Uma recusa que volta na hora prova que a TV nunca chegou a
mostrar o pedido na tela, e isso muda completamente o conselho que damos ao usuário.

O handshake é curto e a mensagem de registro vem da própria aiowebostv, então a chave
obtida aqui tem exatamente as mesmas permissões que ela pediria.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any

import aiohttp
from aiowebostv.handshake import REGISTRATION_MESSAGE

from lgremote.tv.discovery import SSAP_SECURE_PORT, identify_any

PAIR_TIMEOUT = 60.0
# Abaixo disto a TV respondeu rápido demais para ter havido gente lendo o pedido na
# tela: ela negou sozinha.
INSTANT_DENIAL = 2.0
HELLO_TIMEOUT = 10.0

REFUSED_WITHOUT_PROMPT = """A TV recusou sozinha, sem chegar a mostrar o pedido na tela.
  Quase sempre é um destes três:
    1. A TV está em standby. Com o Quick Start+ ligado ela continua respondendo na
       rede, mas a tela apagada não tem como exibir o pedido. Ligue a TV e deixe na
       tela inicial (fora de qualquer app) antes de tentar de novo.
    2. Uma recusa antiga ficou gravada. Na TV: Configurações → Geral → Dispositivos →
       Dispositivos externos → apague o histórico de conexões.
    3. Configurações → Geral → Mobile TV On desligado."""

REFUSED_BY_USER = """A TV mostrou o pedido e ele foi recusado.
  Tente de novo e escolha "Sim" com o controle físico."""


class PairError(RuntimeError):
    """Base do que pode dar errado ao registrar este PC na TV."""


class PairRefusedError(PairError):
    """A TV disse não.

    `prompted` distingue os dois "não" que exigem conselhos opostos: se a TV exibiu o
    pedido, é caso de tentar de novo e aceitar; se não exibiu, tentar de novo não
    adianta nada até resolver o standby ou o histórico de conexões.
    """

    def __init__(self, detail: str, *, prompted: bool) -> None:
        super().__init__(REFUSED_BY_USER if prompted else REFUSED_WITHOUT_PROMPT)
        self.detail = detail
        self.prompted = prompted


class PairTimeoutError(PairError):
    """A TV mostrou o pedido e ninguém aceitou dentro do prazo."""


class PairUnreachableError(PairError):
    """Não deu nem para falar com a TV."""


def _registration_message() -> dict[str, Any]:
    """Cópia do manifesto da aiowebostv, sem chave — é o que faz a TV perguntar."""
    message: dict[str, Any] = copy.deepcopy(REGISTRATION_MESSAGE)
    message["payload"]["client-key"] = None
    return message


async def _await_prompt(
    websocket: aiohttp.ClientWebSocketResponse,
    prompt_timeout: float,
    now: Callable[[], float],
) -> str:
    """Espera a decisão do usuário na TV e devolve o client key."""
    started = now()
    try:
        response = await websocket.receive_json(timeout=prompt_timeout)
    except TimeoutError as exc:
        raise PairTimeoutError(
            f"A TV mostrou o pedido, mas ninguém aceitou em {prompt_timeout:.0f} segundos."
        ) from exc

    if response.get("type") == "error":
        detail = str(response.get("error", "sem detalhe"))
        raise PairRefusedError(detail, prompted=(now() - started) >= INSTANT_DENIAL)

    if response.get("type") == "registered":
        key = response.get("payload", {}).get("client-key")
        if key:
            return str(key)

    raise PairError(f"A TV respondeu algo que não sei ler no pareamento: {response!r}")


async def pair(
    host: str,
    port: int | None = None,
    *,
    prompt_timeout: float = PAIR_TIMEOUT,
    now: Callable[[], float] = time.monotonic,
) -> str:
    """Registra este PC na TV e devolve o client key.

    `port=None` descobre a porta pelo `identify_any` — é o caso do `lgremote pair
    --host`, onde ninguém varreu a rede antes. O assistente já sabe a porta e passa.

    `now` é injetável para o teste medir a demora da recusa sem esperar de verdade.
    """
    if port is None:
        candidate = await identify_any(host)
        if candidate is None:
            raise PairUnreachableError(
                f"Não achei nenhuma TV webOS em {host}. "
                "Confira se ela está ligada e na mesma rede que este PC."
            )
        port = candidate.port

    scheme = "wss" if port == SSAP_SECURE_PORT else "ws"
    try:
        async with aiohttp.ClientSession() as http:
            # Certificado autoassinado: validar aqui reprovaria toda TV LG de verdade.
            websocket = await http.ws_connect(f"{scheme}://{host}:{port}", ssl=False)
            try:
                # Um pedido por vez, resposta lida antes do próximo: mandar tudo de
                # uma vez embaralharia as respostas e a leitura pegaria a errada.
                await websocket.send_json({"id": "hello", "type": "hello", "payload": {}})
                await websocket.receive_json(timeout=HELLO_TIMEOUT)

                # Não é enfeite: o firmware novo exige o getSystemInfo ANTES do
                # registro. A aiowebostv faz o mesmo, pelo mesmo motivo.
                await websocket.send_json(
                    {
                        "id": "sysinfo",
                        "type": "request",
                        "uri": "ssap://system/getSystemInfo",
                        "payload": {},
                    }
                )
                await websocket.receive_json(timeout=HELLO_TIMEOUT)

                await websocket.send_json(_registration_message())
                first = await websocket.receive_json(timeout=HELLO_TIMEOUT)

                # Chave já aceita de cara: acontece quando a TV lembra deste PC.
                if first.get("type") == "registered":
                    key = first.get("payload", {}).get("client-key")
                    if key:
                        return str(key)
                if first.get("type") == "error":
                    raise PairRefusedError(
                        str(first.get("error", "sem detalhe")), prompted=False
                    )

                return await _await_prompt(websocket, prompt_timeout, now)
            finally:
                await websocket.close()
    except (TimeoutError, OSError, aiohttp.ClientError, ValueError, TypeError) as exc:
        raise PairUnreachableError(
            f"Não consegui falar com a TV em {host}:{port}. "
            "Confira se ela está ligada, na mesma rede, e se o IP ainda é esse."
        ) from exc
