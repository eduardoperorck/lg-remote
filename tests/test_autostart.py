"""Início automático junto com o Windows."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgremote import autostart
from lgremote.autostart import ENTRY_NAME, install, remove, render_script, status


@pytest.fixture
def startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Finge a pasta Inicializar — o teste não escreve na de verdade."""
    folder = tmp_path / "Startup"
    folder.mkdir()
    monkeypatch.setattr(autostart, "startup_dir", lambda: folder)
    return folder


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Projeto de mentira, com o serve.ps1 que o autostart exige."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "serve.ps1").write_text("# fake", encoding="utf-8")
    return root


def install_in(startup: Path, project: Path) -> Path:
    return autostart.install(project_root=project, log_file=project / "local-data" / "serve.log")


def test_script_sobe_oculto_e_registra_log(tmp_path: Path) -> None:
    """O `0` no Run é o que esconde a janela; sem log não haveria como diagnosticar."""
    script = render_script(project_root=tmp_path, log_file=tmp_path / "log.txt")

    assert ", 0, False" in script
    assert "serve.ps1" in script
    assert "log.txt" in script
    assert "ExecutionPolicy Bypass" in script


def test_log_vai_por_parametro_e_nao_por_redirecionamento(tmp_path: Path) -> None:
    """O `Run` do WScript.Shell chama CreateProcess, sem shell no meio.

    Com `>> log 2>&1` o redirecionamento virava argumento solto do powershell.exe e era
    descartado pelo `-File`: o serve.log nunca chegou a existir, mesmo com o início
    automático rodando todo dia. Por isso o caminho vai como parâmetro nomeado.
    """
    script = render_script(project_root=tmp_path, log_file=tmp_path / "log.txt")

    assert "-LogFile" in script
    assert ">>" not in script
    assert "2>&1" not in script


def test_serve_ps1_aceita_o_parametro_de_log() -> None:
    """Contrato entre dois arquivos de linguagens diferentes — nada mais o protege."""
    serve = (autostart.PROJECT_ROOT / "serve.ps1").read_text(encoding="utf-8-sig")

    assert "param(" in serve
    assert "$LogFile" in serve


def test_instalar_cria_a_entrada(startup: Path, project: Path) -> None:
    path = install_in(startup, project)

    assert path == startup / ENTRY_NAME
    assert path.exists()
    assert "serve.ps1" in path.read_text(encoding="cp1252")


def test_instalar_e_idempotente(startup: Path, project: Path) -> None:
    """Reinstalar é como se corrige um caminho que mudou — não pode duplicar."""
    install_in(startup, project)
    install_in(startup, project)

    assert len(list(startup.iterdir())) == 1


def test_status_reflete_a_realidade(startup: Path, project: Path) -> None:
    assert status().installed is False
    install_in(startup, project)
    assert status().installed is True


def test_remover(startup: Path, project: Path) -> None:
    install_in(startup, project)

    assert remove() is True
    assert remove() is False  # já não existia


def test_fora_do_windows_explica_em_vez_de_quebrar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart, "startup_dir", lambda: None)
    current = status()

    assert current.supported is False
    assert "Windows" in current.detail
    with pytest.raises(autostart.AutostartError):
        install()


def test_sem_serve_ps1_o_erro_diz_o_que_falta(startup: Path, tmp_path: Path) -> None:
    """Instalar apontando para um script inexistente daria um autostart morto."""
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    with pytest.raises(autostart.AutostartError, match=r"serve\.ps1"):
        install(project_root=vazio)


def test_o_repositorio_tem_o_serve_ps1_que_o_autostart_precisa() -> None:
    """Renomear o serve.ps1 quebraria o início automático em silêncio."""
    from lgremote.config import PROJECT_ROOT

    assert (PROJECT_ROOT / "serve.ps1").exists()
