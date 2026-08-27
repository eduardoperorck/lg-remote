"""Assistente de instalação: da rede vazia até o servidor no ar, num comando só.

Existe porque a configuração tem quatro passos que dependem um do outro (achar a TV,
parear, ler os apps, escrever o .env) e errar a ordem dá mensagens confusas.
"""

from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass

from lgremote import autostart
from lgremote.catalog.tmdb import CatalogError, TmdbClient
from lgremote.config import (
    APPS_FILE,
    ENV_FILE,
    LOG_FILE,
    Settings,
    load_settings,
    write_env_value,
)
from lgremote.tv import pairing
from lgremote.tv.apps import apply_discoveries, load_catalog, match_installed_apps
from lgremote.tv.discovery import TvCandidate, arp_mac, find_tvs, identify_any, local_ipv4
from lgremote.tv.session import TvPairError, TvSession, TvUnreachableError
from lgremote.tv.wol import InvalidMacError, parse_mac

TMDB_TOKEN_URL = "https://www.themoviedb.org/settings/api"


class Abort(RuntimeError):
    """O usuário desistiu ou o terminal não é interativo."""


@dataclass(frozen=True)
class ChosenTv:
    """A TV com que vamos trabalhar, e o que sabemos dela."""

    host: str
    # None quando o IP veio digitado: aí a porta se descobre na hora de parear.
    port: int | None = None
    uuid: str = ""

    @classmethod
    def from_candidate(cls, found: TvCandidate) -> ChosenTv:
        return cls(found.host, found.port, found.uuid or "")


def _ask(prompt: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        raise Abort(
            "Este assistente precisa de um terminal interativo. "
            "Rode os comandos separados: `lgremote pair --host <ip>`, depois `lgremote discover`."
        )
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise Abort("Cancelado.") from exc
    return answer or default


def _confirm(prompt: str, *, default: bool = True) -> bool:
    hint = "S/n" if default else "s/N"
    answer = _ask(f"{prompt} ({hint})").lower()
    if not answer:
        return default
    return answer.startswith("s")


def _step(number: int, total: int, title: str) -> None:
    # Sem códigos ANSI: o console clássico do Windows os imprime como lixo quando
    # o processamento de terminal virtual não está ligado.
    print(f"\n[{number}/{total}] {title}")
    print("-" * (len(title) + 8))


# --- passos --------------------------------------------------------------


async def _known_tv(settings: Settings) -> ChosenTv | None:
    """Confere o IP já salvo antes de sair varrendo a rede.

    É isto que faz rodar o assistente de novo custar um segundo em vez de meio minuto:
    o `identify` é anterior ao registro, então nem aparece nada na tela da TV.
    """
    if not settings.tv_host:
        return None

    print(f"Conferindo a TV já configurada ({settings.tv_host})…")
    found = await identify_any(settings.tv_host)
    if found is None:
        print("  Ela não respondeu nesse IP — vou procurar na rede.")
        return None
    if settings.tv_uuid and found.uuid and found.uuid != settings.tv_uuid:
        print("  Quem respondeu nesse IP é outra TV — vou procurar na rede.")
        return None

    print(f"  ✓ {found.label}")
    return ChosenTv.from_candidate(found)


async def _choose_host(preset: str | None, settings: Settings) -> ChosenTv:
    if preset:
        print(f"Usando o IP informado: {preset}")
        return ChosenTv(preset)

    known = await _known_tv(settings)
    if known is not None:
        return known

    print("Procurando a TV na rede (leva alguns segundos)…")
    candidates = await find_tvs()

    # O UUID não muda quando o DHCP troca o IP: dá para reconhecer a TV sozinho,
    # sem transformar uma troca de endereço numa pergunta.
    if settings.tv_uuid:
        same = next((found for found in candidates if found.uuid == settings.tv_uuid), None)
        if same is not None:
            print(f"É a mesma TV, com IP novo: {settings.tv_host} → {same.host}")
            return ChosenTv.from_candidate(same)

    if not candidates:
        print("Não achei nenhuma TV. Ela precisa estar LIGADA e na mesma rede que este PC.")
        print("O IP está na TV em: Configurações → Rede → Conexão Wi-Fi/Cabo → Avançado.")
        host = _ask("IP da TV")
        if not host:
            raise Abort("Sem IP não dá para continuar.")
        return ChosenTv(host)

    if len(candidates) == 1:
        found = candidates[0]
        print(f"Achei uma TV LG: {found.label}")
        if _confirm("É essa?"):
            return ChosenTv.from_candidate(found)
        typed = _ask("IP da TV")
        return ChosenTv(typed) if typed else ChosenTv.from_candidate(found)

    print("Achei mais de uma TV LG:")
    for index, found in enumerate(candidates, start=1):
        print(f"  {index}) {found.label}")
    choice = _ask("Qual é a TV? (número)", "1")
    try:
        return ChosenTv.from_candidate(candidates[int(choice) - 1])
    except (ValueError, IndexError) as exc:
        raise Abort("Escolha inválida.") from exc


async def _reuse_key(chosen: ChosenTv, client_key: str) -> str | None:
    """Confere se a chave salva ainda vale. Devolve a chave boa, ou None.

    Vale mais que perguntar "reaproveitar o pareamento?": só a TV sabe se a chave
    continua na lista de dispositivos dela. Quando vale, nada aparece na tela.
    """
    saved: list[str] = []
    session = TvSession(chosen.host, client_key, on_client_key=saved.append)
    try:
        await session.connect()
    except TvPairError:
        return None
    except TvUnreachableError:
        # Chave morta faz a TV exibir o pedido de autorização; ninguém respondendo a
        # tempo chega aqui como "TV inalcançável", igualzinho a TV desligada. Quem
        # desempata é o identify, que responde sem depender de chave nenhuma.
        if await identify_any(chosen.host) is None:
            raise
        return None
    finally:
        await session.close()
    return saved[-1] if saved else client_key


async def _pair(chosen: ChosenTv) -> str:
    print("\n→ Olhe a TV agora: ela vai pedir para autorizar este aparelho.")
    print("  Aceite com o controle físico — há cerca de um minuto para isso.\n")

    for attempt in (1, 2, 3):
        try:
            return await pairing.pair(chosen.host, chosen.port)
        except pairing.PairError as exc:
            print(f"✗ {exc}")
            if attempt == 3 or not _confirm("Tentar de novo?"):
                raise Abort("Não consegui parear com a TV.") from exc

    raise Abort("Não consegui parear com a TV.")


async def _discover_apps(host: str, client_key: str) -> None:
    session = TvSession(host, client_key)
    try:
        installed = await session.list_apps()
    except TvUnreachableError as exc:
        print(f"✗ Não consegui ler os apps: {exc}")
        return
    finally:
        await session.close()

    catalog = load_catalog(APPS_FILE)
    found, missing = match_installed_apps(catalog, installed)

    for discovery in found:
        print(f"  ✓ {discovery.app_title or discovery.service_id} → {discovery.app_id}")
    for service in missing:
        print(f"  · {service.label}: não instalado na TV (o atalho não vai funcionar)")

    changed = [d for d in found if d.changed]
    if changed:
        applied = apply_discoveries(APPS_FILE, changed)
        print(f"\n  {len(applied)} app_id corrigidos em config/apps.yaml")


def _ask_pin(current: str = "") -> str:
    print("O PIN protege a TV de qualquer outro aparelho na rede (inclusive visitas no Wi-Fi).")
    # Numa reconfiguração, o padrão é o PIN atual: Enter mantém o que já funciona.
    suggestion = current or f"{secrets.randbelow(9000) + 1000}"
    while True:
        pin = _ask("PIN de 4 a 6 dígitos", suggestion)
        if pin.isdigit() and 4 <= len(pin) <= 6:
            return pin
        print("  Precisa ser só números, de 4 a 6 dígitos.")


async def _ask_token(current: str = "") -> str:
    print("Opcional: liga a busca de séries e filmes por nome.")
    print(f"  Credencial gratuita (a chave v3 ou o token v4 servem): {TMDB_TOKEN_URL}")
    if current:
        print("  Enter mantém a credencial já configurada.")
    else:
        print("  Enter para pular — dá para adicionar depois no .env.")

    token = _ask("Credencial do TMDb", current)
    if not token:
        return ""

    # Validar aqui evita a pior falha possível: descobrir no sofá que a busca não
    # funciona, sem pista do motivo.
    client = TmdbClient(token)
    try:
        await client.verify()
    except CatalogError as exc:
        print(f"  ✗ {exc}")
        if _confirm("Salvar assim mesmo?", default=False):
            return token
        return await _ask_token(current)
    finally:
        await client.close()

    print(f"  ✓ Credencial válida ({client.auth_scheme}).")
    return token


def _ask_mac(host: str, current: str = "") -> str:
    detected = arp_mac(host)
    if detected:
        print(f"MAC da TV detectado automaticamente: {detected}")
        return detected

    print("Opcional: o MAC da TV permite LIGAR a TV pelo celular (Wake-on-LAN).")
    print("  Está na TV em Configurações → Rede, junto do IP. Enter para pular.")
    while True:
        # Numa reconfiguração o padrão é o MAC atual: Enter não pode apagar o que
        # já funcionava só porque a tabela ARP estava fria nesta hora.
        mac = _ask("MAC da TV", current)
        if not mac:
            return ""
        try:
            parse_mac(mac)
        except InvalidMacError:
            print("  MAC inválido. Formato: AA:BB:CC:DD:EE:FF")
            continue
        return mac


def _setup_autostart() -> None:
    """Sem isto, o controle morre junto com a janela do serve.bat."""
    current = autostart.status()
    if not current.supported:
        print(current.detail)
        return

    if current.installed:
        print("O controle já sobe junto com o Windows.")
        if _confirm("Manter assim?"):
            # Reescrever é o que conserta uma entrada antiga cujo caminho ou cujo
            # comando mudaram — foi assim que o log do servidor passou anos sem
            # ser gravado. Custa nada e é idempotente.
            try:
                autostart.install()
            except autostart.AutostartError as exc:
                print(f"  ✗ Não consegui revalidar a entrada: {exc}")
            else:
                print(f"  ✓ Entrada revalidada. Log em {LOG_FILE}")
            return
        autostart.remove()
        print("✓ Removido — agora só abrindo o serve.bat.")
        return

    print("Sobe o controle sozinho no login, sem janela — o celular só funciona com ele no ar.")
    if not _confirm("Iniciar junto com o Windows?"):
        print("  Ok. Para ligar depois: lgremote autostart --install")
        return

    try:
        path = autostart.install()
    except autostart.AutostartError as exc:
        print(f"  ✗ {exc}")
        return
    print(f"  ✓ Instalado ({path.name}). Log em {LOG_FILE}")


# --- orquestração --------------------------------------------------------


async def run_setup(preset_host: str | None = None) -> bool:
    """Executa o assistente. Devolve True se o usuário quiser subir o servidor agora."""
    total = 6
    print("\n== Configuração do controle da TV LG ==")

    existing = load_settings()

    _step(1, total, "Encontrar a TV")
    chosen = await _choose_host(preset_host, existing)

    _step(2, total, "Parear (autorize na TV)")
    # A chave é da TV, não do endereço dela: se o UUID bate, ela continua valendo
    # mesmo depois de o DHCP trocar o IP.
    same_tv = bool(existing.tv_client_key) and (
        existing.tv_host == chosen.host
        or (bool(existing.tv_uuid) and existing.tv_uuid == chosen.uuid)
    )
    client_key = ""
    if same_tv:
        print("Conferindo o pareamento salvo…")
        client_key = await _reuse_key(chosen, existing.tv_client_key) or ""
        if client_key:
            print("✓ O pareamento salvo ainda vale — nada a autorizar.")
        else:
            print("A chave salva não vale mais nesta TV.")
    if not client_key:
        client_key = await _pair(chosen)
        print("✓ Pareado. A chave foi salva — isso não se repete.")

    write_env_value("TV_HOST", chosen.host)
    write_env_value("TV_CLIENT_KEY", client_key)
    if chosen.uuid:
        write_env_value("TV_UUID", chosen.uuid)

    _step(3, total, "Ler os apps instalados na TV")
    await _discover_apps(chosen.host, client_key)

    _step(4, total, "Definir o acesso")
    already_set = existing.ui_pin and not _confirm(
        "PIN, TMDb e MAC já estão definidos. Rever?", default=False
    )
    if already_set:
        print("  Mantidos como estão.")
    else:
        write_env_value("UI_PIN", _ask_pin(existing.ui_pin))
        write_env_value("TMDB_TOKEN", await _ask_token(existing.tmdb_token))
        write_env_value("TV_MAC", _ask_mac(chosen.host, existing.tv_mac))

    _step(5, total, "Iniciar junto com o Windows")
    _setup_autostart()

    _step(6, total, "Pronto")
    settings = load_settings()
    address = local_ipv4() or "127.0.0.1"
    print(f"Configuração salva em {ENV_FILE}")
    print("\n  No celular, mesmo Wi-Fi, abra:")
    print(f"      http://{address}:{settings.port}")
    print("  Safari > Compartilhar > Adicionar à Tela de Início\n")
    if not settings.has_catalog:
        print("  (Busca de títulos desligada — sem TMDB_TOKEN.)")
    print("  Para calibrar a busca dentro dos apps, depois:")
    print('    uv run lgremote try-title "The Last of Us" --service max')
    print("\n  Não precisa rodar este assistente de novo. Se algum dia parar de funcionar:")
    print("    uv run lgremote doctor\n")

    return _confirm("Subir o servidor agora?")
