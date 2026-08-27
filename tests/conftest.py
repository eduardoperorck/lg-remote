"""TV falsa: grava os comandos em vez de mandar para a rede.

Toda a lógica que importa (macros, camadas do opener, whitelist, catálogo) é testável
sem a TV ligada. O que só dá para verificar com a TV real está no README, em
"Verificar com a TV".
"""

from __future__ import annotations

from typing import Any

import pytest

from lgremote.tv.session import TvSession


class FakeWebOsClient:
    """Imita o pedaço do WebOsClient que a TvSession usa."""

    def __init__(self, host: str, client_key: str | None = None) -> None:
        self.host = host
        self.client_key = client_key or "chave-de-teste"
        self.created_client_session = False
        self.calls: list[tuple[str, Any]] = []
        self._connected = False
        self.fail_times = 0
        # Reprova a conexão — é o que permite testar a tradução de erro e a
        # redescoberta sem precisar de uma TV de verdade recusando de verdade.
        self.connect_error: Exception | None = None
        self.apps: list[dict[str, Any]] = []
        self.current = "com.webos.app.livetv"
        self.volume = 12
        # Espelha `tv_state.is_on` da aiowebostv, que só é preenchido pelas assinaturas
        # de estado — em webOS antigo pode ficar sempre falso.
        self.is_on = True

    # --- ciclo de vida ---
    async def connect(self) -> bool:
        if self.connect_error is not None:
            raise self.connect_error
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # --- comandos ---
    def _record(self, name: str, payload: Any = None) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            self._connected = False
            raise OSError("conexão caiu")
        self.calls.append((name, payload))

    async def button(self, name: str) -> None:
        self._record("button", name)

    async def click(self) -> None:
        self._record("click")

    async def move(self, d_x: int, d_y: int, down: int = 0) -> None:
        self._record("move", (d_x, d_y))

    async def request(self, uri: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._record(uri, payload)
        return {"returnValue": True}

    async def launch_app(self, app: str) -> dict[str, Any]:
        self._record("launch", app)
        self.current = app
        return {"returnValue": True}

    async def close_app(self, app: str) -> dict[str, Any]:
        self._record("close", app)
        if self.current == app:
            self.current = "com.webos.app.livetv"
        return {"returnValue": True}

    async def launch_app_with_params(self, app: str, params: dict[str, Any]) -> dict[str, Any]:
        self._record("launch_params", (app, params))
        self.current = app
        return {"returnValue": True}

    async def launch_app_with_content_id(self, app: str, content_id: str) -> dict[str, Any]:
        self._record("launch_content", (app, content_id))
        return {"returnValue": True}

    async def get_apps(self) -> list[dict[str, Any]]:
        self._record("get_apps")
        return self.apps

    async def get_current_app(self) -> str:
        self._record("get_current_app")
        return self.current

    async def get_volume(self) -> int:
        self._record("get_volume")
        return self.volume

    async def set_volume(self, volume: int) -> dict[str, Any]:
        self._record("set_volume", volume)
        self.volume = volume
        return {"returnValue": True}

    async def set_mute(self, mute: bool) -> dict[str, Any]:
        self._record("set_mute", mute)
        return {"returnValue": True}

    async def power_off(self) -> None:
        # Reproduz o guard da aiowebostv: sem estado preenchido, não faz nada.
        if not self.is_on:
            return
        self._record("power_off")

    async def command(self, request_type: str, uri: str, payload: Any = None) -> None:
        """Envio sem esperar resposta — usado onde a TV não responde (ex.: desligar)."""
        self._record(f"command:{uri}", payload)

    async def send_message(self, message: str) -> dict[str, Any]:
        self._record("toast", message)
        return {"returnValue": True}

    # --- ajudantes de teste ---
    @property
    def buttons(self) -> list[str]:
        return [payload for name, payload in self.calls if name == "button"]

    def named(self, name: str) -> list[Any]:
        return [payload for call, payload in self.calls if call == name]


@pytest.fixture
def fake_tv() -> FakeWebOsClient:
    return FakeWebOsClient("10.0.0.5")


@pytest.fixture
def session(fake_tv: FakeWebOsClient) -> TvSession:
    return TvSession("10.0.0.5", "chave", client_factory=lambda host, key: fake_tv)


@pytest.fixture
def instant_sleep():  # type: ignore[no-untyped-def]
    """Sleeper que registra a espera sem gastar o tempo real."""
    waits: list[float] = []

    async def sleeper(seconds: float) -> None:
        waits.append(seconds)

    sleeper.waits = waits  # type: ignore[attr-defined]
    return sleeper
