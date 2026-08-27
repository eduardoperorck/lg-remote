"""Iniciar o controle junto com o Windows.

Usa a pasta **Inicializar** do usuário em vez do Agendador de Tarefas por um motivo
prático: não exige administrador. E como o Windows executa `.vbs` direto dessa pasta,
nem um atalho `.lnk` é preciso — o que evita depender de COM só para criar um arquivo.

O VBS sobe o `serve.ps1` com janela oculta (o `0` no `Run`). Como não há janela para
mostrar erro, a saída vai para um arquivo de log.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from lgremote.config import LOG_FILE, PROJECT_ROOT

ENTRY_NAME = "lg-remote.vbs"

# O log vai por parâmetro, não por `>>`: o `Run` do WScript.Shell chama CreateProcess
# direto, sem passar por um shell. Redirecionamento ali não é interpretado — vira
# argumento solto do powershell.exe, que o `-File` descarta. Era por isso que o
# serve.log nunca aparecia, mesmo com o início automático rodando.
_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy Bypass -File ""{script}"" -LogFile ""{log}""'
)

_TEMPLATE = """\
' Sobe o controle da TV LG junto com o Windows, sem janela.
' Gerado por: lgremote autostart --install
' Para remover: lgremote autostart --remove (ou apague este arquivo)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "{project}"
shell.Run "{command}", 0, False
"""


class AutostartError(RuntimeError):
    """Não deu para instalar ou remover o início automático."""


@dataclass(frozen=True)
class AutostartStatus:
    supported: bool
    installed: bool
    path: Path | None
    detail: str


def is_windows() -> bool:
    return sys.platform == "win32"


def startup_dir() -> Path | None:
    """Pasta Inicializar do usuário atual, se estivermos no Windows."""
    if not is_windows():
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def entry_path(directory: Path | None = None) -> Path | None:
    target = directory or startup_dir()
    return target / ENTRY_NAME if target else None


def render_script(project_root: Path = PROJECT_ROOT, log_file: Path = LOG_FILE) -> str:
    command = _COMMAND.format(script=project_root / "serve.ps1", log=log_file)
    return _TEMPLATE.format(project=project_root, command=command)


def status(directory: Path | None = None) -> AutostartStatus:
    path = entry_path(directory)
    if path is None:
        return AutostartStatus(
            supported=False,
            installed=False,
            path=None,
            detail=(
                "Início automático só está implementado para Windows. "
                "Em Linux, use systemd --user; em macOS, um launchd agent."
            ),
        )
    if path.exists():
        return AutostartStatus(True, True, path, f"Instalado em {path}")
    return AutostartStatus(True, False, path, "Não instalado")


def install(
    directory: Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    log_file: Path = LOG_FILE,
) -> Path:
    """Cria (ou atualiza) a entrada. Idempotente: reinstalar corrige caminhos antigos."""
    path = entry_path(directory)
    if path is None:
        raise AutostartError(status(directory).detail)

    script = project_root / "serve.ps1"
    if not script.exists():
        raise AutostartError(f"Não achei {script} — o início automático precisa dele.")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # cp1252, não UTF-8: o Windows Script Host lê .vbs no code page ANSI e
        # mostraria caracteres estranhos (ou falharia) com BOM UTF-8.
        path.write_text(render_script(project_root, log_file), encoding="cp1252")
    except OSError as exc:
        raise AutostartError(f"Não consegui escrever {path}: {exc}") from exc
    return path


def remove(directory: Path | None = None) -> bool:
    """Devolve True se removeu, False se já não existia."""
    path = entry_path(directory)
    if path is None:
        raise AutostartError(status(directory).detail)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise AutostartError(f"Não consegui remover {path}: {exc}") from exc
    return True
