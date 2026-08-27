"""Checagens estáticas do PWA.

Não há navegador neste ambiente, então erros de CSS/HTML só apareceriam no iPhone —
tarde demais. Estes testes cobrem os poucos casos que quebram a tela inteira.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lgremote.api.app import WEB_DIR

INDEX = WEB_DIR / "index.html"
STYLE = WEB_DIR / "style.css"
APP_JS = WEB_DIR / "app.js"

# Elementos que o JS mostra/esconde: `id="x" class="y" hidden`
_HIDDEN_ELEMENT = re.compile(r'id="([\w-]+)"[^>]*class="([^"]+)"[^>]*\shidden')


def css_classes_with_display() -> set[str]:
    """Classes que declaram `display` — as que sobrescreveriam o atributo `hidden`."""
    text = STYLE.read_text(encoding="utf-8")
    found: set[str] = set()
    for match in re.finditer(r"\.([\w-]+)\s*(?:,[^{]*)?\{([^}]*)\}", text):
        if "display:" in match.group(2):
            found.add(match.group(1))
    return found


def test_hidden_e_protegido_globalmente() -> None:
    """Sem isto, um `display` do autor mantém na tela o que o JS mandou esconder.

    Com `.modal` (position: fixed; inset: 0) o resultado é um overlay invisível que
    engole todos os toques — a tela parece travada.
    """
    assert "[hidden] { display: none !important; }" in STYLE.read_text(encoding="utf-8")


def test_elementos_escondiveis_nao_dependem_da_ordem_do_css() -> None:
    """Documenta quais elementos dependem da regra acima para sumir de verdade."""
    hidden_elements = _HIDDEN_ELEMENT.findall(INDEX.read_text(encoding="utf-8"))
    assert hidden_elements, "nenhum elemento com hidden — o parser quebrou?"

    with_display = css_classes_with_display()
    at_risk = {
        element_id
        for element_id, classes in hidden_elements
        if with_display & set(classes.split())
    }
    # Se algum novo aparecer aqui, a regra global continua cobrindo — o teste existe
    # para deixar visível que a proteção é necessária, não acidental.
    assert {"gate", "quick-presets", "name-modal", "ep-modal"} <= at_risk


def test_todo_modal_tem_como_fechar() -> None:
    """Um modal sem saída é indistinguível de tela travada."""
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")

    for modal_id in re.findall(r'id="([\w-]+)"[^>]*class="modal"', html):
        closed = (
            f'el("{modal_id}").hidden = true' in js  # fechado direto
            or f'closeModal("{modal_id}")' in js  # ou pelo helper
        )
        assert closed, f"{modal_id} nunca é fechado"


def test_modais_fecham_pelo_fundo_e_pelo_esc() -> None:
    """Se o ✕ não responder, tem de haver outra saída — senão parece travado."""
    js = APP_JS.read_text(encoding="utf-8")

    assert "Escape" in js
    assert "event.target === el(id)" in js  # toque no fundo escuro


def test_ids_usados_pelo_js_existem_no_html() -> None:
    """Um id errado no JS vira TypeError silencioso e a tela para de responder."""
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")

    declared = set(re.findall(r'id="([\w-]+)"', html))
    used = set(re.findall(r'el\("([\w-]+)"\)', js))

    assert used - declared == set()


@pytest.mark.parametrize("asset", ["index.html", "style.css", "app.js", "manifest.webmanifest"])
def test_arquivos_do_pwa_existem(asset: str) -> None:
    path: Path = WEB_DIR / asset
    assert path.exists() and path.stat().st_size > 0


def test_area_de_deslize_nao_rola_a_pagina() -> None:
    """Sem `touch-action: none`, o navegador rola a página e o gesto nunca chega ao JS."""
    css = STYLE.read_text(encoding="utf-8")
    bloco = css[css.index(".pad {") : css.index(".pad.is-touched")]

    assert "touch-action: none" in bloco


def test_a_aba_controle_cabe_na_tela() -> None:
    """O problema de origem: as alturas somadas passavam da tela e o direcional só era
    alcançável rolando. `dvh` (não `vh`) porque no Safari o `vh` ignora a barra."""
    css = STYLE.read_text(encoding="utf-8")

    assert "100dvh" in css
    assert "aspect-ratio" not in css  # o D-pad antigo dimensionava pela largura


def test_banner_e_presets_rapidos_ficam_fora_do_fluxo() -> None:
    """Ambos aparecem/somem sozinhos; no fluxo, empurravam os botões sob o seu dedo."""
    html = INDEX.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    assert "position: fixed" in css[css.index(".banner {") : css.index(".banner.is-error")]
    # O banner sai do <main>, senão herdaria a coluna de altura fixa.
    assert html.index('id="banner"') > html.index("</main>")


def test_toque_no_direcional_nao_e_cancelado() -> None:
    """`preventDefault` no pointerdown suprimia os eventos de compatibilidade do WebKit
    e levava junto o `:active` — o controle mais usado não dava retorno nenhum."""
    js = APP_JS.read_text(encoding="utf-8")
    bloco = js[js.index('for (const button of document.querySelectorAll("[data-button]"))') :]

    assert "event.preventDefault()" not in bloco[: bloco.index("/* ---")]


def test_zoom_nao_e_bloqueado() -> None:
    """`user-scalable=no` viola a WCAG 1.4.4; o que evita o zoom acidental é o CSS.

    Olha a tag em si, não o arquivo todo: o comentário ao lado dela cita o termo para
    registrar a decisão, e procurar no texto inteiro daria falso positivo.
    """
    html = INDEX.read_text(encoding="utf-8")
    viewport = re.search(r'<meta name="viewport"[^>]*>', html)

    assert viewport is not None
    assert "user-scalable" not in viewport.group(0)
    assert "viewport-fit=cover" in viewport.group(0)  # safe-area do iPhone
    assert "touch-action: manipulation" in STYLE.read_text(encoding="utf-8")


def test_alvos_de_toque_tem_pelo_menos_44px() -> None:
    """Abaixo disso o dedo erra — e os menores estavam justo nos botões de ação."""
    css = STYLE.read_text(encoding="utf-8")

    alturas = [int(value) for value in re.findall(r"min-height:\s*(\d+)px", css)]
    assert alturas and min(alturas) >= 44
