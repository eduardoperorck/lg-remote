"""Configuração lida do .env na raiz do projeto."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
APPS_FILE = PROJECT_ROOT / "config" / "apps.yaml"
# Escrito pelo gravador, não à mão: fica separado do apps.yaml para que uma regravação
# nunca apague os comentários que explicam como calibrar as macros de busca.
PRESETS_FILE = PROJECT_ROOT / "config" / "presets.yaml"
LOG_FILE = PROJECT_ROOT / "local-data" / "serve.log"
# Só o traceback vai para cá. Na tela fica uma frase; no autostart não há tela nenhuma,
# e sem este arquivo uma falha inesperada sumiria sem deixar rastro.
CRASH_FILE = PROJECT_ROOT / "local-data" / "last-error.log"


class Settings(BaseSettings):
    """Valores do .env. Tudo opcional para o CLI conseguir orientar quando faltar algo."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    tv_host: str = ""
    tv_mac: str = ""
    tv_uuid: str = ""
    tv_client_key: str = ""
    tmdb_token: str = ""
    ui_pin: str = ""
    port: int = 8765
    bind_host: str = "0.0.0.0"

    @property
    def is_paired(self) -> bool:
        return bool(self.tv_host and self.tv_client_key)

    @property
    def has_catalog(self) -> bool:
        return bool(self.tmdb_token)


def load_settings() -> Settings:
    return Settings()


def write_env_value(key: str, value: str) -> None:
    """Grava/atualiza uma chave no .env preservando o resto do arquivo.

    Usado pelo `pair` para persistir o client key sem o usuário copiar e colar, e pelo
    servidor quando a TV troca de IP. Escreve num temporário e troca de nome no fim:
    como agora há dois processos que podem gravar, uma escrita interrompida no meio
    truncaria o arquivo e levaria junto o pareamento.
    """
    if not ENV_FILE.exists():
        example = PROJECT_ROOT / ".env.example"
        ENV_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")

    scratch = ENV_FILE.with_name(f"{ENV_FILE.name}.tmp")
    scratch.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(scratch, ENV_FILE)
