"""O .env: template em dia e gravação que não perde o pareamento."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lgremote import config
from lgremote.config import PROJECT_ROOT, Settings, write_env_value

_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def test_env_example_tem_todas_as_chaves_do_settings() -> None:
    """O .env.example é copiado quando falta o .env — uma chave a menos ali some.

    Sem esta trava, adicionar um campo em Settings e esquecer do template só apareceria
    na máquina de alguém instalando do zero.
    """
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(_KEY.findall(example))
    expected = {name.upper() for name in Settings.model_fields}

    assert expected - documented == set()


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", target)
    return target


def test_gravar_troca_so_a_linha_pedida(env_file: Path) -> None:
    env_file.write_text("# comentário\nTV_HOST=1.1.1.1\nUI_PIN=1234\n", encoding="utf-8")

    write_env_value("TV_HOST", "2.2.2.2")

    conteudo = env_file.read_text(encoding="utf-8")
    assert "TV_HOST=2.2.2.2" in conteudo
    assert "UI_PIN=1234" in conteudo
    assert "# comentário" in conteudo


def test_chave_nova_e_acrescentada(env_file: Path) -> None:
    env_file.write_text("TV_HOST=1.1.1.1\n", encoding="utf-8")

    write_env_value("TV_UUID", "abc")

    assert "TV_UUID=abc" in env_file.read_text(encoding="utf-8")


def test_gravacao_nao_deixa_temporario_para_tras(env_file: Path) -> None:
    """A troca é atômica porque agora o servidor também grava aqui: uma escrita
    interrompida no meio truncaria o arquivo e levaria junto o client key."""
    env_file.write_text("TV_HOST=1.1.1.1\n", encoding="utf-8")

    write_env_value("TV_HOST", "2.2.2.2")

    assert list(env_file.parent.iterdir()) == [env_file]
