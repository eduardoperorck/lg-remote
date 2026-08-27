"""Conexão SSAP persistente com a TV.

Um WebSocket aberto e reutilizado: reconectar a cada toque deixaria o direcional
lento demais para navegar. Comandos são serializados por um lock — o socket de
input do webOS embaralha as respostas se dois comandos saem juntos.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiowebostv import WebOsClient, WebOsTvPairError
from aiowebostv.endpoints import INSERT_TEXT, POWER_OFF, SEND_DELETE, SEND_ENTER

# A base não é reexportada no topo do pacote, mas é ela que interessa: pegar só o
# WebOsTvCommandError deixava o WebOsTvPairError (que é irmão, não filho) escapar até
# virar traceback na tela do usuário.
from aiowebostv.exceptions import WebOsTvError

from lgremote.tv import buttons

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

ClientFactory = Callable[[str, str | None], Any]
# Devolve o IP novo da TV, ou None se não achou.
Rediscover = Callable[[], Awaitable[str | None]]

# Uma varredura /24 custa segundos e o PWA pergunta o estado a cada 5. Sem esta
# folga, uma TV simplesmente desligada viraria uma varredura atrás da outra.
REDISCOVER_COOLDOWN = 120.0


class TvError(RuntimeError):
    """Qualquer problema de conversa com a TV — o CLI captura por esta base."""


class TvUnreachableError(TvError):
    """A TV não respondeu — desligada da tomada, fora da rede ou IP trocado."""


class TvPairError(TvError):
    """A TV recusou a autorização, ou a chave salva deixou de valer."""


class TvSession:
    """Dona da conexão com a TV. Uma instância por processo."""

    def __init__(
        self,
        host: str,
        client_key: str | None = None,
        *,
        client_factory: ClientFactory = WebOsClient,
        on_client_key: Callable[[str], None] | None = None,
        rediscover: Rediscover | None = None,
        on_host_change: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.client_key = client_key
        self._factory = client_factory
        self._on_client_key = on_client_key
        self._rediscover = rediscover
        self._on_host_change = on_host_change
        self._now = now
        self._last_rediscover = 0.0
        self._rediscovering = False
        self._client: Any | None = None
        self._lock = asyncio.Lock()

    # --- ciclo de vida ---------------------------------------------------

    async def connect(self) -> Any:
        """Garante uma conexão viva e devolve o client. Idempotente."""
        if self._client is not None and self._client.is_connected():
            return self._client

        try:
            return await self._open()
        except TvUnreachableError:
            # O IP pode ter mudado no DHCP. Se conseguirmos reencontrar a TV pela
            # identidade, o usuário nem fica sabendo que algo aconteceu.
            if await self._relocate() is None:
                raise
            return await self._open()

    async def _open(self) -> Any:
        client = self._client or self._factory(self.host, self.client_key)
        try:
            await client.connect()
        except WebOsTvPairError as exc:
            await self._discard(client)
            raise TvPairError(
                "A TV recusou a chave salva — ela vale até alguém apagar este PC da "
                "lista de dispositivos. Rode `lgremote pair` para parear de novo."
                if self.client_key
                else "A TV recusou a autorização."
            ) from exc
        except (TimeoutError, WebOsTvError, OSError) as exc:
            await self._discard(client)
            raise TvUnreachableError(
                f"Não consegui falar com a TV em {self.host}. "
                "Confira se ela está ligada, na mesma rede, e se o IP ainda é esse."
            ) from exc

        # No primeiro pareamento a TV devolve a chave; guardamos para não pedir de novo.
        new_key = getattr(client, "client_key", None)
        if new_key and new_key != self.client_key:
            self.client_key = new_key
            if self._on_client_key is not None:
                self._on_client_key(new_key)

        self._client = client
        return client

    async def _discard(self, client: Any) -> None:
        """Descarta o client que falhou.

        Sem fechar aqui, a `ClientSession` que a aiowebostv cria sozinha fica aberta:
        num servidor que roda o dia inteiro é uma por tentativa frustrada.
        """
        self._client = None
        close_session = getattr(client, "close_client_session", None)
        if close_session is not None and getattr(client, "created_client_session", False):
            with contextlib.suppress(OSError):
                await close_session()

    async def _relocate(self) -> str | None:
        """Procura a TV na rede e adota o IP novo, respeitando o intervalo mínimo."""
        if self._rediscover is None or self._rediscovering:
            return None
        if self._now() - self._last_rediscover < REDISCOVER_COOLDOWN:
            return None

        self._rediscovering = True
        try:
            found = await self._rediscover()
        except Exception:
            # Procurar a TV é socorro, não comando: se a busca quebrar, quem tem de
            # chegar ao usuário é o erro original
            # ("não falei com a TV"), não um erro secundário do resgate.
            _LOGGER.warning("a varredura atrás da TV falhou", exc_info=True)
            return None
        finally:
            self._last_rediscover = self._now()
            self._rediscovering = False

        if not found or found == self.host:
            return None

        _LOGGER.info("a TV mudou de IP: %s -> %s", self.host, found)
        self._client = None
        self.host = found
        if self._on_host_change is not None:
            self._on_host_change(found)
        return found

    async def close(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        await client.disconnect()
        close_session = getattr(client, "close_client_session", None)
        if close_session is not None and getattr(client, "created_client_session", False):
            await close_session()

    @property
    def connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected())

    # --- execução --------------------------------------------------------

    async def _call(self, action: Callable[[Any], Awaitable[T]]) -> T:
        """Executa um comando, com uma única tentativa de reconexão.

        Uma reconexão só: se a segunda também falhar, o problema é a TV, e insistir
        só faz o PWA travar esperando.
        """
        async with self._lock:
            client = await self.connect()
            try:
                return await action(client)
            except (TimeoutError, WebOsTvError, OSError) as exc:
                _LOGGER.warning("comando falhou (%s), reconectando", exc)
                self._client = None
                client = await self.connect()
                try:
                    return await action(client)
                except (TimeoutError, WebOsTvError, OSError) as retry_exc:
                    raise TvUnreachableError(str(retry_exc)) from retry_exc

    # --- controle --------------------------------------------------------

    async def button(self, name: str) -> None:
        valid = buttons.normalize(name)
        await self._call(lambda client: client.button(valid))

    async def click(self) -> None:
        await self._call(lambda client: client.click())

    async def move(self, d_x: int, d_y: int) -> None:
        await self._call(lambda client: client.move(d_x, d_y))

    async def insert_text(self, text: str, *, replace: bool = False) -> None:
        """Digita no campo focado da TV usando o IME (o teclado nativo).

        É isto que faz a busca dentro do Max/Netflix acontecer sem direcional.
        """
        payload = {"text": text, "replace": 1 if replace else 0}
        await self._call(lambda client: client.request(INSERT_TEXT, payload))

    async def send_enter(self) -> None:
        await self._call(lambda client: client.request(SEND_ENTER))

    async def delete_characters(self, count: int) -> None:
        await self._call(lambda client: client.request(SEND_DELETE, {"count": count}))

    async def set_volume(self, volume: int) -> None:
        await self._call(lambda client: client.set_volume(volume))

    async def get_volume(self) -> int | None:
        return await self._call(lambda client: client.get_volume())

    async def set_mute(self, mute: bool) -> None:
        await self._call(lambda client: client.set_mute(mute))

    async def power_off(self) -> None:
        """Desliga a TV.

        Manda o comando direto em vez de usar `client.power_off()`, que só age quando
        `tv_state.is_on` já foi preenchido pelas assinaturas de estado — em webOS antigo
        esse campo pode ficar vazio e o desligar viraria um silêncio sem erro nenhum.
        Se o WebSocket está aberto, a TV está ligada: o guard não acrescenta nada aqui.

        `command` e não `request` porque a TV desligando não devolve resposta — esperar
        por uma travaria até estourar o timeout.
        """
        await self._call(lambda client: client.command("request", POWER_OFF))

    async def toast(self, message: str) -> None:
        """Mostra um aviso flutuante na TV. Útil para confirmar que o comando chegou."""
        await self._call(lambda client: client.send_message(message))

    # --- apps ------------------------------------------------------------

    async def list_apps(self) -> list[dict[str, Any]]:
        result = await self._call(lambda client: client.get_apps())
        return list(result or [])

    async def current_app(self) -> str | None:
        return await self._call(lambda client: client.get_current_app())

    async def launch(self, app_id: str) -> dict[str, Any]:
        return await self._call(lambda client: client.launch_app(app_id))

    async def close_app(self, app_id: str) -> dict[str, Any]:
        """Fecha o app. Serve para forçar uma abertura fria ao calibrar a tela de perfil."""
        return await self._call(lambda client: client.close_app(app_id))

    async def launch_with_params(self, app_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._call(lambda client: client.launch_app_with_params(app_id, params))

    async def launch_with_content_id(self, app_id: str, content_id: str) -> dict[str, Any]:
        return await self._call(
            lambda client: client.launch_app_with_content_id(app_id, content_id)
        )
