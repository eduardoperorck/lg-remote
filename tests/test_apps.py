"""Catálogo de serviços, casamento com os apps da TV e reescrita do apps.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lgremote.config import APPS_FILE
from lgremote.tv.apps import (
    ServiceConfigError,
    apply_discoveries,
    load_catalog,
    match_installed_apps,
    parse_catalog,
)

SAMPLE = {
    "services": [
        {
            "id": "max",
            "label": "Max",
            "tmdb_names": ["Max", "Max Amazon Channel"],
            "match": ["max", "hbo"],
            "app_id": "com.wbd.stream",
            "wait_after_launch": 12,
            "search": [{"button": "LEFT"}, {"text": "{title}"}],
        },
        {
            "id": "netflix",
            "label": "Netflix",
            "tmdb_names": ["Netflix"],
            "match": ["netflix"],
            "app_id": "netflix",
        },
    ],
    "shortcuts": ["netflix", "max", "inexistente"],
}


def test_apps_yaml_do_repo_carrega() -> None:
    """Se o arquivo de configuração real quebrar, nada sobe."""
    catalog = load_catalog(APPS_FILE)
    assert catalog.services
    assert all(service.app_id for service in catalog.services)


def test_shortcuts_ignoram_servico_inexistente() -> None:
    catalog = parse_catalog(SAMPLE)
    assert [s.id for s in catalog.shortcut_services()] == ["netflix", "max"]


def test_servico_sem_app_id_falha_com_dica() -> None:
    with pytest.raises(ServiceConfigError, match="discover"):
        parse_catalog({"services": [{"id": "x", "label": "X"}]})


def test_can_search_diferencia_quem_tem_macro() -> None:
    catalog = parse_catalog(SAMPLE)
    assert catalog.by_id("max").can_search is True
    assert catalog.by_id("netflix").can_search is False


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("Max", "max"),
        ("Max Amazon Channel", "max"),
        ("HBO Max", "max"),  # rótulo antigo, cai no match por termo
        ("Netflix", "netflix"),
        ("Netflix basic with Ads", "netflix"),
        ("Paramount Plus", None),
    ],
)
def test_provedor_do_tmdb_vira_servico(provider: str, expected: str | None) -> None:
    """O TMDb muda o rótulo do provedor com frequência; o casamento precisa aguentar."""
    catalog = parse_catalog(SAMPLE)
    service = catalog.by_tmdb_provider(provider)
    assert (service.id if service else None) == expected


def test_discover_usa_o_id_exato_quando_ele_existe() -> None:
    catalog = parse_catalog(SAMPLE)
    installed = [
        {"id": "com.wbd.stream", "title": "Max"},
        {"id": "netflix", "title": "Netflix"},
    ]
    found, missing = match_installed_apps(catalog, installed)
    assert missing == []
    assert all(not discovery.changed for discovery in found)


def test_discover_corrige_id_divergente_pelo_titulo() -> None:
    """IDs de app variam por região e ano do modelo — por isso lemos da TV."""
    catalog = parse_catalog(SAMPLE)
    installed = [
        {"id": "com.wbd.stream.latam", "title": "Max"},
        {"id": "netflix", "title": "Netflix"},
    ]
    found, missing = match_installed_apps(catalog, installed)
    changed = {d.service_id: d.app_id for d in found if d.changed}
    assert changed == {"max": "com.wbd.stream.latam"}
    assert missing == []


def test_discover_reporta_app_nao_instalado() -> None:
    catalog = parse_catalog(SAMPLE)
    found, missing = match_installed_apps(catalog, [{"id": "netflix", "title": "Netflix"}])
    assert [service.id for service in missing] == ["max"]
    assert [d.service_id for d in found] == ["netflix"]


def test_apply_discoveries_preserva_comentarios(tmp_path: Path) -> None:
    """Os comentários do apps.yaml são a documentação de como calibrar — PyYAML os perderia."""
    path = tmp_path / "apps.yaml"
    path.write_text(
        "# comentário do topo\n"
        "services:\n"
        "  - id: max\n"
        "    label: Max\n"
        "    # este comentário precisa sobreviver\n"
        "    app_id: com.wbd.stream\n"
        "  - id: netflix\n"
        "    label: Netflix\n"
        "    app_id: netflix\n",
        encoding="utf-8",
    )
    catalog = parse_catalog(yaml.safe_load(path.read_text(encoding="utf-8")))
    found, _ = match_installed_apps(
        catalog,
        [{"id": "com.wbd.stream.latam", "title": "Max"}, {"id": "netflix", "title": "Netflix"}],
    )

    applied = apply_discoveries(path, [d for d in found if d.changed])

    content = path.read_text(encoding="utf-8")
    assert applied == ["max -> com.wbd.stream.latam"]
    assert "# comentário do topo" in content
    assert "# este comentário precisa sobreviver" in content
    assert "app_id: com.wbd.stream.latam" in content
    assert "app_id: netflix" in content  # o outro serviço não foi tocado
