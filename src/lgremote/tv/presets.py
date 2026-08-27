"""Presets: sequências de botões gravadas por você, salvas por serviço.

Existem porque não há API para legenda/áudio dentro do Max ou Disney+ — o menu é
desenhado pelo próprio app e só responde a botões. Em vez de eu chutar a sequência e
você calibrar no YAML, você grava navegando como sempre faz.

Este arquivo é escrito pela máquina. O `apps.yaml` continua sendo o hand-written, com os
comentários que explicam como calibrar — uma regravação nunca encosta nele.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lgremote.tv.macros import Step, parse_steps

MAX_PRESETS_PER_SERVICE = 20
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_HEADER = """\
# Presets gravados pelo app (Gravar > navegar na TV > Parar).
#
# ATENÇÃO: este arquivo é REESCRITO pelo gravador — comentários que você
# adicionar aqui serão perdidos. Editar os passos à mão funciona, mas só
# até a próxima gravação daquele preset.
#
# Para calibrar a busca dentro dos apps, use o config/apps.yaml.
"""


class PresetError(ValueError):
    """Preset inválido ou arquivo malformado."""


def slugify(label: str) -> str:
    """'Legenda PT + áudio original' -> 'legenda-pt-audio-original'."""
    normalized = unicodedata.normalize("NFKD", label)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug or "preset"


@dataclass(frozen=True)
class Preset:
    """Uma sequência nomeada, pertencente a um serviço."""

    id: str
    label: str
    service_id: str
    steps: tuple[Step, ...]
    recorded_at: str | None = None

    def as_yaml(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "steps": [step_to_yaml(step) for step in self.steps],
        }
        if self.recorded_at:
            data["recorded_at"] = self.recorded_at
        return data


def step_to_yaml(step: Step) -> dict[str, Any]:
    """Serializa um passo no mesmo formato que o `apps.yaml` usa."""
    from lgremote.tv.macros import ButtonStep, ClearStep, EnterStep, TextStep, WaitStep

    match step:
        case ButtonStep() as button:
            data: dict[str, Any] = {"button": button.name}
            if button.times != 1:
                data["times"] = button.times
            if button.delay != 0.2:
                data["delay"] = button.delay
            return data
        case WaitStep(seconds):
            return {"wait": seconds}
        case TextStep(template):
            return {"text": template}
        case EnterStep():
            return {"enter": True}
        case ClearStep(count):
            return {"clear": count}
    raise PresetError(f"Passo não serializável: {step!r}")


@dataclass
class PresetStore:
    """Todos os presets, agrupados por serviço."""

    by_service: dict[str, list[Preset]]

    def for_service(self, service_id: str) -> list[Preset]:
        return list(self.by_service.get(service_id, []))

    def get(self, service_id: str, preset_id: str) -> Preset | None:
        return next((p for p in self.for_service(service_id) if p.id == preset_id), None)

    def all_presets(self) -> list[Preset]:
        return [preset for presets in self.by_service.values() for preset in presets]

    def put(self, preset: Preset) -> None:
        """Adiciona ou substitui. Regravar com o mesmo nome sobrescreve, que é o esperado."""
        presets = self.by_service.setdefault(preset.service_id, [])
        for index, existing in enumerate(presets):
            if existing.id == preset.id:
                presets[index] = preset
                return
        if len(presets) >= MAX_PRESETS_PER_SERVICE:
            raise PresetError(
                f"Limite de {MAX_PRESETS_PER_SERVICE} presets para {preset.service_id}. "
                "Apague algum antes de gravar outro."
            )
        presets.append(preset)

    def remove(self, service_id: str, preset_id: str) -> bool:
        presets = self.by_service.get(service_id)
        if not presets:
            return False
        remaining = [p for p in presets if p.id != preset_id]
        if len(remaining) == len(presets):
            return False
        self.by_service[service_id] = remaining
        return True


def parse_store(data: dict[str, Any] | None) -> PresetStore:
    by_service: dict[str, list[Preset]] = {}
    for service_id, raw_presets in (data or {}).items():
        if not isinstance(raw_presets, list):
            raise PresetError(f"'{service_id}' deveria ser uma lista de presets")
        presets: list[Preset] = []
        for raw in raw_presets:
            if not isinstance(raw, dict) or "id" not in raw:
                raise PresetError(f"preset sem 'id' em {service_id}: {raw!r}")
            presets.append(
                Preset(
                    id=str(raw["id"]),
                    label=str(raw.get("label", raw["id"])),
                    service_id=str(service_id),
                    steps=tuple(parse_steps(raw.get("steps"))),
                    recorded_at=raw.get("recorded_at"),
                )
            )
        by_service[str(service_id)] = presets
    return PresetStore(by_service=by_service)


def load_presets(path: Path) -> PresetStore:
    """Arquivo ausente é normal: significa que você ainda não gravou nada."""
    if not path.exists():
        return PresetStore(by_service={})
    return parse_store(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_presets(path: Path, store: PresetStore) -> None:
    payload = {
        service_id: [preset.as_yaml() for preset in presets]
        for service_id, presets in sorted(store.by_service.items())
        if presets
    }
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER + "\n" + body, encoding="utf-8")


def now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
