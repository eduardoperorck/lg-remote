"""Wake-on-LAN — o único jeito confiável de ligar a TV.

O `system/turnOn` do SSAP só funciona se a TV ainda estiver acordada o suficiente
para manter o WebSocket, o que raramente é o caso quando ela está "desligada".
O magic packet chega na placa de rede mesmo em standby, desde que a TV tenha
"Configurações > Geral > Mobile TV On / Ativar via Wi-Fi" ligado.
"""

from __future__ import annotations

import re
import socket

_MAC_SEPARATORS = re.compile(r"[:\-.\s]")
_BROADCAST_PORTS = (9, 7)


class InvalidMacError(ValueError):
    """MAC malformado."""


def parse_mac(mac: str) -> bytes:
    """'AA:BB:CC:DD:EE:FF' -> 6 bytes. Aceita ':', '-', '.' ou nada como separador."""
    cleaned = _MAC_SEPARATORS.sub("", mac).strip()
    if len(cleaned) != 12:
        raise InvalidMacError(f"MAC deve ter 12 dígitos hex, recebi {mac!r}")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise InvalidMacError(f"MAC não é hexadecimal: {mac!r}") from exc


def build_magic_packet(mac: str) -> bytes:
    """6 bytes 0xFF seguidos do MAC repetido 16 vezes."""
    return b"\xff" * 6 + parse_mac(mac) * 16


def wake(mac: str, broadcast: str = "255.255.255.255") -> None:
    """Dispara o magic packet no broadcast da rede.

    Manda nas portas 9 e 7: modelos diferentes escutam em uma ou outra, e o pacote
    é UDP sem resposta — não custa nada mandar nas duas.
    """
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in _BROADCAST_PORTS:
            sock.sendto(packet, (broadcast, port))
