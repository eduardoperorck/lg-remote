"""A camada que nunca pode deixar um traceback chegar à tela do usuário."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from lgremote import cli
from lgremote.config import Settings
from lgremote.setup_wizard import Abort
from lgremote.tv import pairing
from lgremote.tv.session import TvUnreachableError


@pytest.fixture
def crash_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Desvia o log de erro para o tmp — o do projeto não pode ser sujado por teste."""
    target = tmp_path / "local-data" / "last-error.log"
    monkeypatch.setattr(cli, "CRASH_FILE", target)
    return target


def _run_raising(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> int:
    async def explode(_: argparse.Namespace) -> int:
        raise error

    monkeypatch.setitem(cli._ASYNC_COMMANDS, "apps", explode)
    return cli.main(["apps"])


# --- rede de segurança ---------------------------------------------------


def test_erro_inesperado_vira_frase_curta_e_arquivo_de_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], crash_file: Path
) -> None:
    """Era exatamente isto que faltava: o 403 saía como stack trace do Python."""
    code = _run_raising(monkeypatch, RuntimeError("algo bem estranho"))

    erro = capsys.readouterr().err
    assert code == 1
    assert "Traceback" not in erro
    assert "algo bem estranho" in erro
    assert "algo bem estranho" in crash_file.read_text(encoding="utf-8")
    assert "Traceback" in crash_file.read_text(encoding="utf-8")


def test_erro_de_tv_sai_limpo_sem_log_de_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], crash_file: Path
) -> None:
    """Erro previsto não é bug: não merece arquivo de traceback nem susto."""
    code = _run_raising(monkeypatch, TvUnreachableError("a TV não respondeu"))

    assert code == 1
    assert "a TV não respondeu" in capsys.readouterr().err
    assert not crash_file.exists()


def test_erro_de_pareamento_sai_limpo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], crash_file: Path
) -> None:
    code = _run_raising(monkeypatch, pairing.PairRefusedError("403", prompted=False))

    assert code == 1
    assert "standby" in capsys.readouterr().err
    assert not crash_file.exists()


def test_desistencia_no_assistente_sai_com_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], crash_file: Path
) -> None:
    code = _run_raising(monkeypatch, Abort("Cancelado."))

    assert code == 1
    assert "Cancelado." in capsys.readouterr().err


def test_ctrl_c_sai_com_130(monkeypatch: pytest.MonkeyPatch, crash_file: Path) -> None:
    assert _run_raising(monkeypatch, KeyboardInterrupt()) == 130


def test_falha_ao_gravar_o_log_nao_piora_o_erro(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Falhar ao registrar a falha não pode virar uma segunda falha."""
    # Um arquivo no lugar da pasta: criar o diretório vai estourar OSError.
    blocked = tmp_path / "arquivo"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "CRASH_FILE", blocked / "last-error.log")

    code = _run_raising(monkeypatch, RuntimeError("boom"))

    assert code == 1
    assert "Traceback" not in capsys.readouterr().err


# --- fiação do parser ----------------------------------------------------


def test_todo_subcomando_tem_para_onde_ir() -> None:
    """Registrar no parser e esquecer do dispatch dá KeyError na cara do usuário."""
    subparsers = next(
        action
        for action in cli.build_parser()._subparsers._group_actions  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == set(cli._ASYNC_COMMANDS) | {"serve", "autostart"}


# --- doctor --------------------------------------------------------------


async def test_doctor_com_env_vazio_lista_o_que_falta(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sem TV configurada ele não pode tentar a rede — e não pode estourar."""
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(tv_host="", tv_client_key=""))

    code = await cli.cmd_doctor(argparse.Namespace(host=None))

    saida = capsys.readouterr().out
    assert code == 1
    assert "Falta parear" in saida
    assert "TV_UUID" in saida


async def test_doctor_avisa_quando_a_tv_mudou_de_ip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lgremote.tv.discovery import TvCandidate

    async def nao_responde(host: str, **_: object) -> None:
        return None

    async def achou(**_: object) -> TvCandidate:
        return TvCandidate("192.168.2.117", 3001, uuid="tv-uuid")

    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: Settings(tv_host="192.168.2.104", tv_uuid="tv-uuid", tv_client_key=""),
    )
    monkeypatch.setattr(cli, "identify_any", nao_responde)
    monkeypatch.setattr(cli, "locate_tv", achou)

    await cli.cmd_doctor(argparse.Namespace(host=None))

    assert "192.168.2.117" in capsys.readouterr().out
