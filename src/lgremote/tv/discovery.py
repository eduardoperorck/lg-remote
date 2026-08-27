"""Achar a TV na rede sem o usuário ir caçar o IP no menu de Configurações.

Não usamos SSDP: no Windows ele esbarra em firewall de multicast com frequência e
falha em silêncio. Varrer a porta SSAP na própria sub-rede é bruto, porém previsível
— e a porta 3001 aberta é uma assinatura bem específica de TV LG.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Any

import aiohttp

SSAP_SECURE_PORT = 3001  # webOS 2018+
SSAP_LEGACY_PORT = 3000  # TVs 2014-2017
PROBE_TIMEOUT = 0.4
IDENTIFY_TIMEOUT = 4.0
MAX_CONCURRENCY = 128

_MAC_PATTERN = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")


@dataclass(frozen=True)
class TvCandidate:
    """Uma TV webOS confirmada na rede, identificada sem parear."""

    host: str
    port: int
    os_version: str | None = None
    release_version: str | None = None
    uuid: str | None = None
    model: str | None = None

    @property
    def label(self) -> str:
        """Como a TV aparece na lista de escolha do assistente."""
        parts = [self.host]
        version = self.release_version or self.os_version
        if version:
            parts.append(f"webOS {version}")
        if self.model:
            parts.append(self.model)
        return "  ·  ".join(parts)


def local_ipv4() -> str | None:
    """IP desta máquina na LAN (o socket UDP não chega a enviar nada)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address: str = sock.getsockname()[0]
            return address
    except OSError:
        return None


def subnet_hosts(local_ip: str) -> list[str]:
    """Todos os /24 vizinhos, menos a própria máquina."""
    octets = local_ip.split(".")
    if len(octets) != 4:
        return []
    prefix = ".".join(octets[:3])
    return [f"{prefix}.{last}" for last in range(1, 255) if f"{prefix}.{last}" != local_ip]


async def _probe(host: str, port: int, connect_timeout: float) -> str | None:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), connect_timeout)
    except (TimeoutError, OSError):
        return None
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return host


async def scan_port(
    hosts: list[str], port: int, *, probe_timeout: float = PROBE_TIMEOUT
) -> list[str]:
    """Testa a porta em todos os hosts em paralelo, limitado para não afogar a rede."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(host: str) -> str | None:
        async with semaphore:
            return await _probe(host, port, probe_timeout)

    results = await asyncio.gather(*(guarded(host) for host in hosts))
    return [host for host in results if host is not None]


def parse_hello(payload: dict[str, Any], host: str, port: int) -> TvCandidate | None:
    """Traduz a resposta do handshake em candidato — ou None se não for TV webOS."""
    if payload.get("deviceOS") != "webOS" or payload.get("deviceType") != "tv":
        return None
    return TvCandidate(
        host=host,
        port=port,
        os_version=payload.get("deviceOSVersion"),
        release_version=payload.get("deviceOSReleaseVersion"),
        uuid=payload.get("deviceUUID"),
    )


async def identify(
    host: str, port: int = SSAP_SECURE_PORT, *, reply_timeout: float = IDENTIFY_TIMEOUT
) -> TvCandidate | None:
    """Pergunta à TV quem ela é, sem parear.

    O `hello` e o `getSystemInfo` acontecem ANTES do registro no protocolo SSAP, então
    nada aparece na tela da TV. Serve para dois fins: eliminar da varredura qualquer
    aparelho que só tenha a porta aberta, e rotular as TVs quando há mais de uma.
    """
    scheme = "wss" if port == SSAP_SECURE_PORT else "ws"
    try:
        async with aiohttp.ClientSession() as session:
            # A TV usa certificado autoassinado — validar aqui reprovaria toda TV real.
            async with asyncio.timeout(reply_timeout):
                websocket = await session.ws_connect(f"{scheme}://{host}:{port}", ssl=False)
            try:
                async with asyncio.timeout(reply_timeout):
                    await websocket.send_json({"id": "hello", "type": "hello", "payload": {}})
                    hello = await websocket.receive_json()

                if hello.get("type") != "hello":
                    return None
                candidate = parse_hello(hello.get("payload", {}), host, port)
                if candidate is None:
                    return None

                # Orçamento próprio: perder o modelo não pode invalidar a TV inteira.
                return await _with_model(websocket, candidate, reply_timeout)
            finally:
                await websocket.close()
    except (TimeoutError, OSError, aiohttp.ClientError, ValueError, TypeError, KeyError):
        return None


async def _with_model(
    websocket: aiohttp.ClientWebSocketResponse,
    candidate: TvCandidate,
    reply_timeout: float,
) -> TvCandidate:
    """Anexa o modelo, se a TV responder. Nem todo firmware devolve isto sem pareamento."""
    request = {
        "id": "sysinfo",
        "type": "request",
        "uri": "ssap://system/getSystemInfo",
        "payload": {},
    }
    try:
        async with asyncio.timeout(reply_timeout):
            await websocket.send_json(request)
            response = await websocket.receive_json()
    except (TimeoutError, OSError, aiohttp.ClientError, ValueError, TypeError):
        return candidate

    model = response.get("payload", {}).get("modelName")
    if not model:
        return candidate
    return TvCandidate(
        host=candidate.host,
        port=candidate.port,
        os_version=candidate.os_version,
        release_version=candidate.release_version,
        uuid=candidate.uuid,
        model=str(model),
    )


async def identify_any(
    host: str, *, reply_timeout: float = IDENTIFY_TIMEOUT
) -> TvCandidate | None:
    """Identifica uma TV num IP conhecido, tentando as duas portas.

    O par 3001/3000 é o mesmo do `find_tvs`; separar em função própria é o que permite
    conferir o IP salvo em ~1 segundo, em vez de varrer a sub-rede inteira de novo.
    """
    for port in (SSAP_SECURE_PORT, SSAP_LEGACY_PORT):
        candidate = await identify(host, port, reply_timeout=reply_timeout)
        if candidate is not None:
            return candidate
    return None


async def find_tvs(*, probe_timeout: float = PROBE_TIMEOUT) -> list[TvCandidate]:
    """TVs LG confirmadas na sub-rede local."""
    local = local_ipv4()
    if local is None:
        return []
    hosts = subnet_hosts(local)
    if not hosts:
        return []

    port = SSAP_SECURE_PORT
    open_hosts = await scan_port(hosts, port, probe_timeout=probe_timeout)
    if not open_hosts:
        # Só cai aqui em TVs de 2014-2017, que não expõem a porta segura.
        port = SSAP_LEGACY_PORT
        open_hosts = await scan_port(hosts, port, probe_timeout=probe_timeout)
    if not open_hosts:
        return []

    identified = await asyncio.gather(*(identify(host, port) for host in open_hosts))
    return [candidate for candidate in identified if candidate is not None]


async def locate_tv(*, uuid: str = "", mac: str = "") -> TvCandidate | None:
    """Reencontra a TV na rede depois que o IP salvo parou de responder.

    O `deviceUUID` não muda com o DHCP, então é a pista boa. O MAC serve de reserva
    para quem pareou antes de o UUID passar a ser gravado. Sem nenhuma das duas, só
    aceitamos o resultado se houver exatamente uma TV — chutar entre duas apontaria o
    controle para a TV errada da casa.
    """
    candidates = await find_tvs()
    if not candidates:
        return None

    if uuid:
        return next((found for found in candidates if found.uuid == uuid), None)

    if mac:
        wanted = mac.replace("-", ":").lower()
        for found in candidates:
            # `arp` é subprocesso bloqueante: chamado direto aqui, ele congelaria o
            # event loop do servidor por até 5s a cada candidato.
            if await asyncio.to_thread(arp_mac, found.host) == wanted:
                return found
        return None

    return candidates[0] if len(candidates) == 1 else None


def parse_arp_mac(output: str, ip: str) -> str | None:
    """Extrai o MAC de uma saída de `arp`.

    Formatos diferem: Windows usa 'aa-bb-cc-dd-ee-ff' em colunas, Linux usa
    '? (192.168.0.10) at aa:bb:cc:dd:ee:ff [ether] on eth0'.
    """
    for line in output.splitlines():
        if ip not in line:
            continue
        match = _MAC_PATTERN.search(line)
        if match:
            return match.group(0).replace("-", ":").lower()
    return None


def arp_mac(ip: str) -> str | None:
    """MAC da TV pela tabela ARP — evita pedir ao usuário algo que a máquina já sabe.

    Só funciona se já houve tráfego com o host, o que é o caso logo após o scan.
    Nunca levanta: é um conforto opcional, não pode derrubar a configuração inteira.
    """
    try:
        # Comando fixo, sem shell: o `ip` vem do nosso próprio scan, não do usuário.
        result = subprocess.run(
            ["arp", "-a", ip],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # Bytes, não text=True: o `arp` do Windows responde no code page do console
    # (cp850 no Brasil) e decodificar como UTF-8 estoura. Só precisamos do MAC,
    # que é ASCII — o resto da linha pode virar '?' sem prejuízo.
    raw = result.stdout or b""
    return parse_arp_mac(raw.decode("utf-8", errors="replace"), ip)
