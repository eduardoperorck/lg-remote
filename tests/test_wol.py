"""Magic packet de Wake-on-LAN."""

from __future__ import annotations

import pytest

from lgremote.tv.wol import InvalidMacError, build_magic_packet, parse_mac


@pytest.mark.parametrize(
    "mac",
    ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "aabb.ccdd.eeff", "AABBCCDDEEFF"],
)
def test_aceita_os_formatos_que_a_tv_mostra(mac: str) -> None:
    """A TV escreve o MAC com ':' na tela; roteadores usam '-' ou '.'."""
    assert parse_mac(mac) == b"\xaa\xbb\xcc\xdd\xee\xff"


@pytest.mark.parametrize("mac", ["AA:BB:CC:DD:EE", "", "ZZ:BB:CC:DD:EE:FF", "aabbccddeeff00"])
def test_mac_invalido_e_recusado(mac: str) -> None:
    with pytest.raises(InvalidMacError):
        parse_mac(mac)


def test_pacote_tem_o_formato_do_padrao() -> None:
    packet = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == b"\xaa\xbb\xcc\xdd\xee\xff" * 16
