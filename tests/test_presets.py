"""Persistência dos presets gravados."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgremote.tv.macros import ButtonStep, WaitStep
from lgremote.tv.presets import (
    MAX_PRESETS_PER_SERVICE,
    Preset,
    PresetError,
    PresetStore,
    load_presets,
    save_presets,
    slugify,
)


def preset(label: str, service_id: str = "max") -> Preset:
    return Preset(
        id=slugify(label),
        label=label,
        service_id=service_id,
        steps=(ButtonStep("DOWN", 2), WaitStep(1.5), ButtonStep("ENTER")),
        recorded_at="2026-08-10 21:00",
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Dublado", "dublado"),
        ("Legenda PT + áudio original", "legenda-pt-audio-original"),
        ("  Áudio Inglês  ", "audio-ingles"),
        ("!!!", "preset"),  # sobrou nada utilizável
    ],
)
def test_slug_de_rotulo_humano(label: str, expected: str) -> None:
    assert slugify(label) == expected


def test_arquivo_ausente_e_normal(tmp_path: Path) -> None:
    """Antes da primeira gravação não existe arquivo — não é erro."""
    store = load_presets(tmp_path / "nao-existe.yaml")
    assert store.all_presets() == []


def test_roundtrip_preserva_os_passos(tmp_path: Path) -> None:
    path = tmp_path / "presets.yaml"
    store = PresetStore(by_service={})
    store.put(preset("Dublado"))

    save_presets(path, store)
    reloaded = load_presets(path)

    saved = reloaded.get("max", "dublado")
    assert saved is not None
    assert saved.label == "Dublado"
    assert saved.steps == (ButtonStep("DOWN", 2), WaitStep(1.5), ButtonStep("ENTER"))
    assert saved.recorded_at == "2026-08-10 21:00"


def test_arquivo_avisa_que_e_reescrito(tmp_path: Path) -> None:
    """Quem abrir o arquivo precisa saber que comentários se perdem."""
    path = tmp_path / "presets.yaml"
    store = PresetStore(by_service={})
    store.put(preset("Dublado"))
    save_presets(path, store)

    assert "REESCRITO" in path.read_text(encoding="utf-8")


def test_regravar_com_o_mesmo_nome_substitui() -> None:
    store = PresetStore(by_service={})
    store.put(preset("Dublado"))
    novo = Preset("dublado", "Dublado", "max", (ButtonStep("UP"),))
    store.put(novo)

    assert len(store.for_service("max")) == 1
    assert store.get("max", "dublado").steps == (ButtonStep("UP"),)


def test_presets_ficam_separados_por_servico() -> None:
    """O menu do Max não serve no Disney+ — misturar seria oferecer o que não funciona."""
    store = PresetStore(by_service={})
    store.put(preset("Dublado", "max"))
    store.put(preset("Dublado", "disney"))

    assert len(store.for_service("max")) == 1
    assert len(store.for_service("disney")) == 1
    assert len(store.all_presets()) == 2


def test_limite_por_servico() -> None:
    store = PresetStore(by_service={})
    for index in range(MAX_PRESETS_PER_SERVICE):
        store.put(preset(f"Preset {index}"))
    with pytest.raises(PresetError, match="Limite"):
        store.put(preset("Mais um"))


def test_remover_devolve_se_existia() -> None:
    store = PresetStore(by_service={})
    store.put(preset("Dublado"))

    assert store.remove("max", "dublado") is True
    assert store.remove("max", "dublado") is False
    assert store.remove("inexistente", "x") is False


def test_yaml_malformado_e_erro_claro(tmp_path: Path) -> None:
    path = tmp_path / "presets.yaml"
    path.write_text("max: nao-e-lista\n", encoding="utf-8")
    with pytest.raises(PresetError, match="lista"):
        load_presets(path)


def test_servico_vazio_nao_e_gravado(tmp_path: Path) -> None:
    """Apagar o último preset não deve deixar uma chave órfã no arquivo."""
    path = tmp_path / "presets.yaml"
    store = PresetStore(by_service={})
    store.put(preset("Dublado"))
    store.remove("max", "dublado")
    save_presets(path, store)

    assert load_presets(path).all_presets() == []
    assert "max:" not in path.read_text(encoding="utf-8")
