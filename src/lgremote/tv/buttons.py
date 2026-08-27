"""Botões aceitos pelo socket de input do webOS.

Whitelist proposital: o nome do botão vem da rede (o PWA) e vira um comando na TV.
Aceitar string arbitrária seria entregar o socket de input pra qualquer um na LAN.
"""

from __future__ import annotations

NAVIGATION = ("UP", "DOWN", "LEFT", "RIGHT", "ENTER", "BACK", "EXIT", "HOME", "MENU")
MEDIA = ("PLAY", "PAUSE", "STOP", "REWIND", "FASTFORWARD")
VOLUME = ("VOLUMEUP", "VOLUMEDOWN", "MUTE")
CHANNEL = ("CHANNELUP", "CHANNELDOWN")
COLORS = ("RED", "GREEN", "YELLOW", "BLUE")
DIGITS = tuple(str(digit) for digit in range(10))
EXTRA = ("INFO", "DASH", "ASTERISK", "CC", "GUIDE", "QMENU")

VALID_BUTTONS: frozenset[str] = frozenset(
    NAVIGATION + MEDIA + VOLUME + CHANNEL + COLORS + DIGITS + EXTRA
)


class UnknownButtonError(ValueError):
    """Botão fora da whitelist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Botão desconhecido: {name!r}")
        self.name = name


def normalize(name: str) -> str:
    """Valida e normaliza o nome de um botão ('enter' -> 'ENTER')."""
    candidate = name.strip().upper()
    if candidate not in VALID_BUTTONS:
        raise UnknownButtonError(name)
    return candidate
