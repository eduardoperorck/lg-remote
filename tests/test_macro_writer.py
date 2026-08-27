"""Gravar uma macro de volta no apps.yaml.

O arquivo é do usuário e é cheio de comentários que explicam como calibrar. Perdê-los
numa regravação seria pior que não ter a funcionalidade — daí o peso destes testes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lgremote.tv.apps import MacroWriteError, load_catalog, write_macro
from lgremote.tv.macros import ButtonStep, TextStep, WaitStep

YAML = """\
# Cabeçalho que explica como calibrar as macros.
services:
  - id: max
    label: Max
    app_id: com.wbd.stream
    wait_after_launch: 12
    # Tela "Quem está assistindo?" — este comentário não pode sumir.
    profile:
      - {button: ENTER}
    wait_before_profile: 14
    search:
      - {wait: 3}
      - {button: LEFT, times: 4, delay: 0.25}
      - {text: "{title}"}
    episode: []
    wait_before_episode: 6

  - id: netflix
    label: Netflix
    app_id: netflix
    search:
      - {button: UP}

shortcuts: [max, netflix]
"""


@pytest.fixture
def apps_file(tmp_path: Path) -> Path:
    path = tmp_path / "apps.yaml"
    path.write_text(YAML, encoding="utf-8")
    return path


def steps_of(path: Path, service_id: str, key: str):  # type: ignore[no-untyped-def]
    service = load_catalog(path).by_id(service_id)
    assert service is not None
    return getattr(service, key)


def test_grava_a_macro_e_ela_volta_pelo_parser(apps_file: Path) -> None:
    write_macro(apps_file, "max", "search", [ButtonStep("DOWN", 3), TextStep("{title}")])

    assert steps_of(apps_file, "max", "search") == (ButtonStep("DOWN", 3), TextStep("{title}"))


def test_comentarios_sobrevivem(apps_file: Path) -> None:
    """É a razão de existir deste código: o PyYAML devolveria o arquivo sem eles."""
    write_macro(apps_file, "max", "search", [ButtonStep("DOWN")])

    texto = apps_file.read_text(encoding="utf-8")
    assert "# Cabeçalho que explica como calibrar as macros." in texto
    assert '# Tela "Quem está assistindo?" — este comentário não pode sumir.' in texto


def test_nao_engole_as_chaves_seguintes(apps_file: Path) -> None:
    """Parar na próxima chave do mesmo nível é o que impede comer o resto do serviço."""
    write_macro(apps_file, "max", "profile", [ButtonStep("RIGHT"), ButtonStep("ENTER")])

    service = load_catalog(apps_file).by_id("max")
    assert service is not None
    assert service.wait_before_profile == 14
    assert service.wait_after_launch == 12
    assert service.search  # a busca continua lá


def test_lista_vazia_em_linha_e_substituida(apps_file: Path) -> None:
    """`episode: []` é uma linha só; sem tratar isso, a lista nova ficaria órfã embaixo."""
    write_macro(apps_file, "max", "episode", [ButtonStep("DOWN", 2)])

    assert steps_of(apps_file, "max", "episode") == (ButtonStep("DOWN", 2),)
    assert "episode: []" not in apps_file.read_text(encoding="utf-8")


def test_nao_vaza_para_o_proximo_servico(apps_file: Path) -> None:
    write_macro(apps_file, "netflix", "search", [ButtonStep("LEFT")])

    assert steps_of(apps_file, "netflix", "search") == (ButtonStep("LEFT"),)
    assert steps_of(apps_file, "max", "search")  # o Max ficou intacto


def test_ultimo_servico_do_arquivo(apps_file: Path) -> None:
    """Sem outra chave depois, o bloco termina no fim do serviço — não do arquivo."""
    write_macro(apps_file, "netflix", "search", [ButtonStep("DOWN"), WaitStep(1.0)])

    texto = apps_file.read_text(encoding="utf-8")
    assert "shortcuts: [max, netflix]" in texto


def test_indentacao_e_preservada(apps_file: Path) -> None:
    write_macro(apps_file, "max", "search", [ButtonStep("DOWN")])

    linhas = apps_file.read_text(encoding="utf-8").splitlines()
    inicio = linhas.index("    search:")
    assert linhas[inicio + 1] == "      - {button: DOWN}"


def test_faz_backup_antes_de_escrever(apps_file: Path) -> None:
    """O arquivo é do usuário e pode estar aberto num editor — precisa ter volta."""
    backup = write_macro(apps_file, "max", "search", [ButtonStep("DOWN")])

    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == YAML


def test_servico_inexistente_recusa(apps_file: Path) -> None:
    with pytest.raises(MacroWriteError, match="não existe"):
        write_macro(apps_file, "hulu", "search", [ButtonStep("DOWN")])


def test_chave_ausente_recusa(tmp_path: Path) -> None:
    """Melhor recusar que inventar a chave num lugar qualquer do bloco."""
    path = tmp_path / "apps.yaml"
    path.write_text("services:\n  - id: max\n    app_id: x\n", encoding="utf-8")

    with pytest.raises(MacroWriteError, match="Chave"):
        write_macro(path, "max", "search", [ButtonStep("DOWN")])


def test_chave_fora_da_lista_recusa(apps_file: Path) -> None:
    with pytest.raises(MacroWriteError):
        write_macro(apps_file, "max", "wait_after_launch", [ButtonStep("DOWN")])


def test_gravacao_vazia_recusa(apps_file: Path) -> None:
    with pytest.raises(MacroWriteError, match="nenhum passo"):
        write_macro(apps_file, "max", "search", [])


def test_template_de_times_sobrevive_a_ida_e_volta(apps_file: Path) -> None:
    """Sem aspas, `times: {episode_index}` viraria um mapa YAML em vez de texto."""
    write_macro(apps_file, "max", "episode", [ButtonStep("RIGHT", "{episode_index}")])

    assert steps_of(apps_file, "max", "episode") == (ButtonStep("RIGHT", "{episode_index}"),)
