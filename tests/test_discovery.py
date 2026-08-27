"""Descoberta da TV na rede."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiohttp import web

from lgremote.tv import discovery
from lgremote.tv.discovery import (
    TvCandidate,
    parse_arp_mac,
    parse_hello,
    scan_port,
    subnet_hosts,
)

HELLO_TV = {
    "protocolVersion": 1,
    "deviceType": "tv",
    "deviceOS": "webOS",
    "deviceOSVersion": "4.1.0",
    "deviceOSReleaseVersion": "6.4.0",
    "deviceUUID": "1e2aa17c-1d53-03db-7e5e-a779f6c18cc5",
}

WINDOWS_ARP = """
Interface: 192.168.0.20 --- 0xa
  Endereço da Internet   Endereço físico       Tipo
  192.168.0.1            a0-b1-c2-d3-e4-f5     dinâmico
  192.168.0.10           AA-BB-CC-DD-EE-FF     dinâmico
"""

LINUX_ARP = "? (192.168.0.10) at aa:bb:cc:dd:ee:ff [ether] on eth0\n"


def test_arp_windows() -> None:
    assert parse_arp_mac(WINDOWS_ARP, "192.168.0.10") == "aa:bb:cc:dd:ee:ff"


def test_arp_linux() -> None:
    assert parse_arp_mac(LINUX_ARP, "192.168.0.10") == "aa:bb:cc:dd:ee:ff"


def test_arp_nao_confunde_hosts_vizinhos() -> None:
    assert parse_arp_mac(WINDOWS_ARP, "192.168.0.1") == "a0:b1:c2:d3:e4:f5"


def test_arp_sem_entrada_devolve_none() -> None:
    assert parse_arp_mac(WINDOWS_ARP, "192.168.0.99") is None


def test_arp_windows_em_cp850_nao_quebra(monkeypatch: pytest.MonkeyPatch) -> None:
    """O `arp` do Windows responde no code page do console, não em UTF-8.

    Decodificar como UTF-8 estourava e derrubava a configuração inteira no fim.
    O MAC é ASCII, então o lixo ao redor pode virar '?' sem prejuízo.
    """
    # 0x87 é 'ç' em cp850 (de "Endereço") — exatamente o byte que derrubou a
    # configuração real em 08/08/2026.
    cp850 = b"  Endere\x87o          192.168.2.104   aa-bb-cc-dd-ee-ff   din\xa2mico\n"

    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=cp850, stderr=b"")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    assert discovery.arp_mac("192.168.2.104") == "aa:bb:cc:dd:ee:ff"


def test_arp_sem_saida_nao_quebra(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=None, stderr=b"")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    assert discovery.arp_mac("192.168.2.104") is None


def test_arp_indisponivel_nao_quebra(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("arp não existe neste sistema")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    assert discovery.arp_mac("192.168.2.104") is None


def test_subnet_cobre_a_faixa_menos_a_propria_maquina() -> None:
    hosts = subnet_hosts("192.168.0.20")
    assert len(hosts) == 253
    assert "192.168.0.1" in hosts
    assert "192.168.0.254" in hosts
    assert "192.168.0.20" not in hosts


@pytest.mark.parametrize("bad", ["", "192.168.0", "não é ip"])
def test_ip_malformado_nao_gera_varredura(bad: str) -> None:
    assert subnet_hosts(bad) == []


async def test_scan_devolve_so_quem_respondeu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Varrer 253 hosts não pode virar 253 falsos positivos por erro de tratamento."""
    alive = {"192.168.0.10"}

    async def fake_probe(host: str, port: int, connect_timeout: float) -> str | None:
        return host if host in alive else None

    monkeypatch.setattr(discovery, "_probe", fake_probe)

    found = await scan_port(["192.168.0.9", "192.168.0.10", "192.168.0.11"], 3001)

    assert found == ["192.168.0.10"]


async def test_scan_de_rede_vazia_nao_quebra() -> None:
    assert await scan_port([], 3001) == []


# --- identificação (handshake pré-pareamento) ----------------------------


def test_hello_de_tv_webos_vira_candidato() -> None:
    candidate = parse_hello(HELLO_TV, "192.168.0.10", 3001)
    assert candidate is not None
    assert candidate.release_version == "6.4.0"
    assert candidate.uuid == "1e2aa17c-1d53-03db-7e5e-a779f6c18cc5"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"deviceOS": "webOS"},  # sem deviceType
        {"deviceType": "tv"},  # sem deviceOS
        {"deviceOS": "Tizen", "deviceType": "tv"},  # Samsung
        {"deviceOS": "webOS", "deviceType": "monitor"},
    ],
)
def test_aparelho_que_nao_e_tv_lg_e_descartado(payload: dict[str, object]) -> None:
    """Porta 3001 aberta não basta: qualquer serviço pode estar ali."""
    assert parse_hello(payload, "192.168.0.10", 3001) is None


def test_label_junta_o_que_a_tv_informou() -> None:
    candidate = TvCandidate("192.168.0.10", 3001, release_version="6.4.0", model="OLED55C1")
    assert candidate.label == "192.168.0.10  ·  webOS 6.4.0  ·  OLED55C1"


def test_label_cai_para_o_ip_quando_a_tv_nao_informa_nada() -> None:
    assert TvCandidate("192.168.0.10", 3001).label == "192.168.0.10"


def test_label_usa_os_version_quando_nao_ha_release() -> None:
    candidate = TvCandidate("192.168.0.10", 3001, os_version="4.1.0")
    assert candidate.label == "192.168.0.10  ·  webOS 4.1.0"


async def test_find_tvs_descarta_porta_aberta_que_nao_responde_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O caso que motivou isto: um aparelho qualquer aparecendo como se fosse a TV."""
    monkeypatch.setattr(discovery, "local_ipv4", lambda: "192.168.0.20")
    monkeypatch.setattr(
        discovery,
        "scan_port",
        _async_returns(["192.168.0.10", "192.168.0.77"]),
    )

    async def fake_identify(host: str, port: int) -> TvCandidate | None:
        return TvCandidate(host, port) if host == "192.168.0.10" else None

    monkeypatch.setattr(discovery, "identify", fake_identify)

    found = await discovery.find_tvs()

    assert [candidate.host for candidate in found] == ["192.168.0.10"]


def _async_returns(value):  # type: ignore[no-untyped-def]
    async def stub(*_: object, **__: object):  # type: ignore[no-untyped-def]
        return value

    return stub


# --- handshake real contra um servidor websocket -------------------------
#
# parse_hello é pura; o que quebra na prática é o ws_connect/receive_json.
# Estes testes sobem uma TV falsa de verdade e falam SSAP com ela.


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@asynccontextmanager
async def fake_tv_server(
    hello_payload: dict[str, Any] | None, model: str | None = None
) -> AsyncIterator[int]:
    """Sobe um servidor que responde SSAP como uma TV webOS responderia."""

    async def handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        async for message in websocket:
            data = message.json()
            if data.get("type") == "hello":
                if hello_payload is None:
                    await websocket.send_json({"type": "error", "id": "hello"})
                else:
                    await websocket.send_json({"type": "hello", "payload": hello_payload})
            elif "getSystemInfo" in str(data.get("uri", "")):
                payload = {"returnValue": True}
                if model:
                    payload["modelName"] = model
                await websocket.send_json({"type": "response", "payload": payload})
        return websocket

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield port
    finally:
        await runner.cleanup()


async def test_identify_conversa_ssap_de_verdade() -> None:
    async with fake_tv_server(HELLO_TV, model="OLED55C1PSA") as port:
        candidate = await discovery.identify("127.0.0.1", port)

    assert candidate is not None
    assert candidate.release_version == "6.4.0"
    assert candidate.model == "OLED55C1PSA"
    assert candidate.label.endswith("webOS 6.4.0  ·  OLED55C1PSA")


async def test_identify_mantem_a_tv_quando_o_modelo_nao_vem() -> None:
    """Firmware antigo não devolve modelo sem pareamento — não pode invalidar a TV."""
    async with fake_tv_server(HELLO_TV, model=None) as port:
        candidate = await discovery.identify("127.0.0.1", port)

    assert candidate is not None
    assert candidate.model is None


async def test_identify_descarta_quem_nao_fala_ssap() -> None:
    async with fake_tv_server(None) as port:
        assert await discovery.identify("127.0.0.1", port) is None


async def test_identify_em_porta_morta_devolve_none() -> None:
    assert await discovery.identify("127.0.0.1", _free_port(), reply_timeout=1.0) is None


# --- reencontrar a TV pela identidade ------------------------------------


def _candidate(host: str, uuid: str = "") -> TvCandidate:
    return TvCandidate(host, 3001, uuid=uuid or None)


def _found(*candidates: TvCandidate):  # type: ignore[no-untyped-def]
    async def stub(**_: object) -> list[TvCandidate]:
        return list(candidates)

    return stub


async def test_identify_any_cai_para_a_porta_legada(monkeypatch: pytest.MonkeyPatch) -> None:
    """TVs de 2014-2017 não expõem a 3001 — desistir nela perderia a TV inteira."""
    tried: list[int] = []

    async def fake_identify(host: str, port: int, **_: object) -> TvCandidate | None:
        tried.append(port)
        return _candidate(host) if port == 3000 else None

    monkeypatch.setattr(discovery, "identify", fake_identify)

    assert await discovery.identify_any("10.0.0.5") is not None
    assert tried == [3001, 3000]


async def test_uuid_acha_a_tv_no_ip_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    """O IP muda no DHCP; o deviceUUID não. É por ele que o servidor se conserta."""
    monkeypatch.setattr(
        discovery, "find_tvs", _found(_candidate("10.0.0.2", "outra"), _candidate("10.0.0.9", "eu"))
    )

    found = await discovery.locate_tv(uuid="eu")

    assert found is not None
    assert found.host == "10.0.0.9"


async def test_uuid_desconhecido_nao_devolve_qualquer_tv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery, "find_tvs", _found(_candidate("10.0.0.2", "outra")))

    assert await discovery.locate_tv(uuid="eu") is None


async def test_sem_uuid_cai_para_o_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plano B para quem pareou antes de o TV_UUID passar a ser gravado."""
    monkeypatch.setattr(
        discovery, "find_tvs", _found(_candidate("10.0.0.2"), _candidate("10.0.0.9"))
    )
    monkeypatch.setattr(
        discovery, "arp_mac", lambda ip: "aa:bb:cc:dd:ee:ff" if ip == "10.0.0.9" else None
    )

    found = await discovery.locate_tv(mac="AA-BB-CC-DD-EE-FF")

    assert found is not None
    assert found.host == "10.0.0.9"


async def test_uma_tv_so_e_sem_identidade_assume_que_e_ela(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery, "find_tvs", _found(_candidate("10.0.0.9")))

    found = await discovery.locate_tv()

    assert found is not None
    assert found.host == "10.0.0.9"


async def test_duas_tvs_sem_identidade_nao_chuta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chutar aqui apontaria o controle para a TV errada da casa."""
    monkeypatch.setattr(
        discovery, "find_tvs", _found(_candidate("10.0.0.2"), _candidate("10.0.0.9"))
    )

    assert await discovery.locate_tv() is None


async def test_rede_vazia_devolve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "find_tvs", _found())

    assert await discovery.locate_tv(uuid="eu") is None
