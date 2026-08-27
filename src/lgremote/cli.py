"""CLI: parear, descobrir apps, calibrar macros e subir o servidor."""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import traceback
from pathlib import Path

from lgremote import autostart
from lgremote.config import (
    APPS_FILE,
    CRASH_FILE,
    ENV_FILE,
    LOG_FILE,
    Settings,
    load_settings,
    write_env_value,
)
from lgremote.setup_wizard import Abort, run_setup
from lgremote.tv import pairing
from lgremote.tv.apps import (
    ServiceConfigError,
    apply_discoveries,
    load_catalog,
    match_installed_apps,
)
from lgremote.tv.discovery import identify_any, locate_tv
from lgremote.tv.opener import TitleOpener
from lgremote.tv.session import TvError, TvPairError, TvSession, TvUnreachableError


def _resolve_host(settings: Settings, override: str | None) -> str:
    host = override or settings.tv_host
    if not host:
        raise SystemExit(
            "Falta o IP da TV. Rode `lgremote pair --host 192.168.0.x` "
            "(o IP está em Configurações > Rede na TV) ou preencha TV_HOST no .env."
        )
    return host


def _lan_ip() -> str:
    """IP desta máquina na LAN — é o endereço que você digita no celular."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address: str = sock.getsockname()[0]
            return address
    except OSError:
        return "127.0.0.1"


# --- comandos ------------------------------------------------------------


async def cmd_pair(args: argparse.Namespace) -> int:
    settings = load_settings()
    host = _resolve_host(settings, args.host)

    print(f"Conectando em {host}…")
    # Identificar antes de mandar olhar a TV: sem isto, quem digitou o IP errado ficava
    # encarando a TV esperando um pedido que nunca ia chegar.
    found = await identify_any(host)
    if found is None:
        print(
            f"✗ Não achei nenhuma TV webOS em {host}. "
            "Confira se ela está ligada e na mesma rede que este PC.",
            file=sys.stderr,
        )
        return 1
    print(f"  {found.label}")

    print("→ Olhe a TV: ela vai pedir para autorizar este aparelho. Aceite com o controle.")
    print("  Há cerca de um minuto para isso.\n")
    try:
        client_key = await pairing.pair(host, found.port)
    except pairing.PairError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    write_env_value("TV_HOST", host)
    write_env_value("TV_CLIENT_KEY", client_key)
    if found is not None and found.uuid:
        write_env_value("TV_UUID", found.uuid)
    print(f"✓ Pareado. TV_HOST e TV_CLIENT_KEY gravados em {ENV_FILE}")
    print("Próximo passo: `lgremote discover`")
    return 0


async def cmd_apps(args: argparse.Namespace) -> int:
    settings = load_settings()
    session = TvSession(_resolve_host(settings, args.host), settings.tv_client_key or None)
    try:
        installed = await session.list_apps()
    except TvError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    finally:
        await session.close()

    print(f"{len(installed)} apps instalados:\n")
    for app in sorted(installed, key=lambda item: str(item.get("title", "")).casefold()):
        print(f"  {app.get('title', '')!s:<32} {app.get('id', '')}")
    return 0


async def cmd_discover(args: argparse.Namespace) -> int:
    settings = load_settings()
    catalog = load_catalog(APPS_FILE)
    session = TvSession(_resolve_host(settings, args.host), settings.tv_client_key or None)
    try:
        installed = await session.list_apps()
    except TvError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    finally:
        await session.close()

    found, missing = match_installed_apps(catalog, installed)

    for discovery in found:
        mark = "~" if discovery.changed else "="
        print(f"  {mark} {discovery.service_id:<12} {discovery.app_id:<32} {discovery.app_title}")
    for service in missing:
        print(f"  ! {service.id:<12} não encontrado na TV (app não instalado?)")

    changed = [d for d in found if d.changed]
    if not changed:
        print("\nNada a mudar — os app_id do apps.yaml já batem com a TV.")
        return 0

    if args.dry_run:
        print(f"\n{len(changed)} app_id divergem. Rode sem --dry-run para gravar.")
        return 0

    applied = apply_discoveries(APPS_FILE, changed)
    print(f"\n✓ {len(applied)} atualizados em {APPS_FILE}:")
    for line in applied:
        print(f"    {line}")
    return 0


async def cmd_try_title(args: argparse.Namespace) -> int:
    settings = load_settings()
    catalog = load_catalog(APPS_FILE)
    service = catalog.by_id(args.service)
    if service is None:
        known = ", ".join(s.id for s in catalog.services)
        print(f"✗ Serviço {args.service!r} não existe. Conhecidos: {known}", file=sys.stderr)
        return 1

    session = TvSession(_resolve_host(settings, args.host), settings.tv_client_key or None)
    opener = TitleOpener(session, on_step=lambda entry: print(f"  → {entry}", flush=True))

    print(f"Abrindo {service.label} ({service.app_id}) e buscando {args.title!r}.")
    print("Olhe a TV e anote onde a sequência erra — depois ajuste 'search' no apps.yaml.\n")
    try:
        if args.cold:
            # Sem fechar antes, o app já aberto pula a tela de perfil e a calibração
            # não reproduz o caso real (que é justamente a abertura fria).
            print("  → fechando o app para forçar abertura fria")
            await session.close_app(service.app_id)
            await asyncio.sleep(3)

        result = await opener.open_title(
            service,
            args.title,
            year=args.year,
            season=args.season,
            episode=args.episode,
        )
    except TvError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    finally:
        await session.close()

    print(f"\nEstratégia usada: {result.strategy}")
    if result.strategy == "launch":
        print("Sem macro configurada — o app abriu, mas ninguém buscou nada.")
    return 0


async def cmd_setup(args: argparse.Namespace) -> int:
    """Assistente: acha a TV, pareia, lê os apps e escreve o .env.

    Sem try/except aqui de propósito: o `main` já traduz `Abort` e os erros de TV, e
    ter os dois imprimiria a mesma coisa em dois lugares.
    """
    args.start_server = await run_setup(args.host)
    return 0


def _mark(ok: bool | None) -> str:
    """`None` é informação, não reprovação — um `✗` em algo opcional assusta à toa."""
    marks: dict[bool | None, str] = {True: "✓", False: "✗", None: "·"}
    return marks[ok]


def _port_in_use(port: int) -> bool:
    """O servidor já está no ar? É a resposta mais útil de todas: não faça nada."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _report_env(settings: Settings) -> list[str]:
    print("== Configuração ==")
    print(f"  {_mark(ENV_FILE.exists())} {ENV_FILE}")
    # `required` separa o que quebra o controle do que é conforto: marcar TV_UUID
    # vazio com ✗ mandaria consertar algo que se preenche sozinho no próximo pareamento.
    fields = (
        ("TV_HOST", settings.tv_host, True),
        ("TV_CLIENT_KEY", settings.tv_client_key, True),
        ("UI_PIN", settings.ui_pin, True),
        ("TV_UUID", settings.tv_uuid, False),
        ("TV_MAC", settings.tv_mac, False),
        ("TMDB_TOKEN", settings.tmdb_token, False),
    )
    for label, value, required in fields:
        # Os valores não são impressos: a chave e o PIN entregam a TV a quem ler a tela.
        print(f"  {_mark(True if value else (False if required else None))} {label}")

    problems: list[str] = []
    if not settings.tv_host or not settings.tv_client_key:
        problems.append("Falta parear — rode `lgremote setup`.")
    if not settings.ui_pin:
        problems.append("UI_PIN vazio — o servidor se recusa a subir sem PIN.")
    return problems


async def _report_tv(settings: Settings, host: str) -> tuple[str, list[str]]:
    """Diz se a TV responde e, se o IP mudou, qual é o novo. Devolve o IP bom."""
    problems: list[str] = []
    print("\n== TV ==")
    print(f"  este PC na LAN: {_lan_ip()}")

    found = await identify_any(host)
    if found is not None:
        print(f"  ✓ {found.label}")
        if settings.tv_uuid and found.uuid and found.uuid != settings.tv_uuid:
            problems.append(f"Quem responde em {host} é outra TV — rode `lgremote setup`.")
        return host, problems

    print(f"  ✗ nada respondeu em {host}")
    if not (settings.tv_uuid or settings.tv_mac):
        problems.append("TV fora do ar ou em outra rede. Ligue a TV e rode `lgremote setup`.")
        return host, problems

    print("  procurando a TV na rede pela identidade…")
    moved = await locate_tv(uuid=settings.tv_uuid, mac=settings.tv_mac)
    if moved is None:
        problems.append("Não achei a TV na rede. Confira se ela está ligada e no mesmo Wi-Fi.")
        return host, problems

    print(f"  ✓ ela mudou de IP: {host} → {moved.host}")
    print("    o servidor corrige isso sozinho no primeiro comando; para gravar agora:")
    print(f"    uv run lgremote pair --host {moved.host}")
    return moved.host, problems


async def _report_key(settings: Settings, host: str) -> list[str]:
    if not settings.tv_client_key:
        return []
    session = TvSession(host, settings.tv_client_key)
    try:
        await session.connect()
    except TvPairError:
        print("  ✗ a chave salva não vale mais")
        return ["A TV recusou a chave — rode `lgremote pair`."]
    except TvUnreachableError as exc:
        print(f"  ✗ {exc}")
        return []
    finally:
        await session.close()
    print("  ✓ a chave salva ainda autentica (sem pedir nada na TV)")
    return []


def _report_server(settings: Settings) -> list[str]:
    problems: list[str] = []
    print("\n== Servidor ==")
    if _port_in_use(settings.port):
        print(f"  ✓ já está no ar na porta {settings.port} — não precisa rodar mais nada")
        print(f"    no celular: http://{_lan_ip()}:{settings.port}")
    else:
        print(f"  ✗ nada escutando na porta {settings.port}")
        problems.append(
            "Servidor fora do ar — abra o serve.bat, ou `lgremote autostart --install`."
        )

    state = autostart.status()
    auto = _mark(state.installed if state.supported else None)
    print(f"  {auto} início automático: {state.detail}")

    if LOG_FILE.exists():
        print(f"  log: {LOG_FILE}")
        tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        for line in tail:
            print(f"      {line}")
    else:
        print(f"  log ainda não escrito ({LOG_FILE})")

    if CRASH_FILE.exists():
        print(f"  ! houve um erro inesperado alguma vez: {CRASH_FILE}")
    return problems


async def cmd_doctor(args: argparse.Namespace) -> int:
    """Diz, de uma vez, por que o controle não está funcionando."""
    settings = load_settings()
    problems = _report_env(settings)

    host = args.host or settings.tv_host
    if host:
        host, tv_problems = await _report_tv(settings, host)
        problems += tv_problems
        problems += await _report_key(settings, host)

    problems += _report_server(settings)

    print("\n== Lembrete ==")
    print("  O firewall do Windows precisa liberar o Python para rede PRIVADA,")
    print("  senão o celular não alcança este PC mesmo com tudo certo aqui.")

    if not problems:
        print("\n✓ Nada aparente para consertar.")
        return 0

    print("\nO que resolver:")
    for problem in problems:
        print(f"  · {problem}")
    return 1


def cmd_autostart(args: argparse.Namespace) -> int:
    """Instala/remove o início junto com o Windows."""
    current = autostart.status()

    if args.remove:
        if not current.supported:
            print(f"✗ {current.detail}", file=sys.stderr)
            return 1
        removed = autostart.remove()
        print("✓ Início automático removido." if removed else "Já não estava instalado.")
        return 0

    if args.status or not args.install:
        print(current.detail)
        if current.supported and current.installed:
            print(f"Log do servidor: {LOG_FILE}")
        return 0

    try:
        path = autostart.install()
    except autostart.AutostartError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    print(f"✓ Instalado em {path}")
    print("  O controle vai subir sozinho no próximo login, sem janela.")
    print(f"  Log: {LOG_FILE}")
    print("  Para desfazer: lgremote autostart --remove")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from lgremote.api.app import create_app

    settings = load_settings()
    problems: list[str] = []
    if not settings.tv_host:
        problems.append("TV_HOST vazio — rode `lgremote pair --host <ip-da-tv>`.")
    if not settings.tv_client_key:
        problems.append("TV_CLIENT_KEY vazio — rode `lgremote pair`.")
    if not settings.ui_pin:
        problems.append("UI_PIN vazio — defina um PIN no .env, o servidor não roda sem ele.")
    if problems:
        for problem in problems:
            print(f"✗ {problem}", file=sys.stderr)
        return 1

    port = args.port or settings.port
    print(f"TV:      {settings.tv_host}")
    print(f"Celular: http://{_lan_ip()}:{port}")
    print("         (Safari > Compartilhar > Adicionar à Tela de Início)\n")

    uvicorn.run(create_app(settings), host=settings.bind_host, port=port, log_level="info")
    return 0


# --- entrada -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lgremote", description="Controle remoto da TV LG")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def with_host(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument("--host", help="IP da TV (sobrescreve TV_HOST do .env)")
        return sub

    with_host(
        subparsers.add_parser("setup", help="assistente: faz tudo, do zero ao servidor no ar")
    )
    with_host(subparsers.add_parser("pair", help="parear com a TV e gravar a chave no .env"))
    with_host(subparsers.add_parser("apps", help="listar os apps instalados na TV"))

    discover = with_host(
        subparsers.add_parser("discover", help="casar apps.yaml com os apps reais da TV")
    )
    discover.add_argument("--dry-run", action="store_true", help="só mostra, não grava")

    try_title = with_host(
        subparsers.add_parser("try-title", help="executar a macro de busca passo a passo")
    )
    try_title.add_argument("title", help="título a buscar")
    try_title.add_argument("--service", required=True, help="id do serviço (ex.: max, netflix)")
    try_title.add_argument("--year", help="ano, se a macro usar {year}")
    try_title.add_argument(
        "--cold",
        action="store_true",
        help="fechar o app antes, para testar a tela de perfil (abertura fria)",
    )
    try_title.add_argument("--season", type=int, help="temporada, para testar a macro de episódio")
    try_title.add_argument("--episode", type=int, help="episódio, junto com --season")

    with_host(
        subparsers.add_parser("doctor", help="diagnóstico: o que está certo e o que falta")
    )

    serve = subparsers.add_parser("serve", help="subir o servidor e o PWA")
    serve.add_argument("--port", type=int, help="porta (padrão: PORT do .env)")

    auto = subparsers.add_parser("autostart", help="iniciar o controle junto com o Windows")
    auto_mode = auto.add_mutually_exclusive_group()
    auto_mode.add_argument("--install", action="store_true", help="instalar")
    auto_mode.add_argument("--remove", action="store_true", help="remover")
    auto_mode.add_argument("--status", action="store_true", help="só mostrar o estado (padrão)")

    return parser


_ASYNC_COMMANDS = {
    "setup": cmd_setup,
    "pair": cmd_pair,
    "apps": cmd_apps,
    "discover": cmd_discover,
    "try-title": cmd_try_title,
    "doctor": cmd_doctor,
}


def _write_crash_log(command: str, exc: BaseException) -> Path | None:
    """Guarda o traceback num arquivo, fora da tela.

    Rede doméstica com firmware imprevisível sempre sobra um caso que ninguém previu.
    O traceback não ajuda quem só quer ver TV; o arquivo ajuda quem for consertar. E no
    início automático não existe console nenhum — sem isto a falha sumiria inteira.
    """
    try:
        CRASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_FILE.open("a", encoding="utf-8") as log:
            log.write(f"\n=== lgremote {command} ===\n")
            log.write("".join(traceback.format_exception(exc)))
    except OSError:
        # Falhar ao registrar a falha não pode piorar a falha.
        return None
    return CRASH_FILE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
        if args.command == "autostart":
            return cmd_autostart(args)

        code = asyncio.run(_ASYNC_COMMANDS[args.command](args))

        # O servidor sobe fora do asyncio.run: uvicorn.run cria o próprio event loop
        # e explodiria se chamado de dentro do assistente.
        if code == 0 and getattr(args, "start_server", False):
            args.port = None
            return cmd_serve(args)
        return code
    except ServiceConfigError as exc:
        print(f"✗ apps.yaml: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except (Abort, TvError, pairing.PairError) as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Rede de segurança: aqui não pode passar traceback cru para a tela.
        saved = _write_crash_log(args.command, exc)
        print(f"\n✗ Deu um erro que eu não previ: {exc}", file=sys.stderr)
        if saved is not None:
            print(f"  O relato completo ficou em {saved}", file=sys.stderr)
        print("  Um diagnóstico rápido: uv run lgremote doctor", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
