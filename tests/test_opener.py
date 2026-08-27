"""Camadas de abertura de título: deep link -> macro -> só abrir."""

from __future__ import annotations

from lgremote.tv.apps import parse_catalog
from lgremote.tv.opener import TitleOpener
from lgremote.tv.session import TvSession
from tests.conftest import FakeWebOsClient


def build_catalog(**overrides: object):  # type: ignore[no-untyped-def]
    service: dict[str, object] = {
        "id": "max",
        "label": "Max",
        "app_id": "com.wbd.stream",
        "wait_after_launch": 12,
        "search": [{"wait": 2}, {"button": "LEFT", "times": 2}, {"text": "{title}"}],
    }
    service.update(overrides)
    return parse_catalog({"services": [service]})


async def test_macro_e_a_camada_padrao(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog().by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "The Last of Us")

    assert result.strategy == "macro"
    assert fake_tv.named("launch") == ["com.wbd.stream"]
    assert fake_tv.buttons == ["LEFT", "LEFT"]
    assert fake_tv.named("com.webos.service.ime/insertText") == [
        {"text": "The Last of Us", "replace": 1}
    ]


async def test_espera_o_app_carregar_antes_de_digitar(
    session: TvSession, instant_sleep
) -> None:
    """Teclas mandadas antes do app abrir se perdem — é a falha nº 1 dessas macros."""
    service = build_catalog().by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout")

    assert instant_sleep.waits[0] == 12  # wait_after_launch vem antes de qualquer passo


async def test_deep_link_ganha_da_macro_quando_calibrado(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog(content_target="https://play.max.com/t/{tmdb_id}").by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout", tmdb_id="106379")

    assert result.strategy == "deep_link"
    assert fake_tv.named("launch_params") == [
        ("com.wbd.stream", {"contentTarget": "https://play.max.com/t/106379"})
    ]
    assert fake_tv.buttons == []  # não navegou: não precisou


async def test_sem_macro_ao_menos_abre_o_app(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Falhar em silêncio seria pior: o usuário fica olhando a TV parada."""
    service = build_catalog(search=[]).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout")

    assert result.strategy == "launch"
    assert result.searched is False
    assert fake_tv.named("launch") == ["com.wbd.stream"]


async def test_open_service_nao_busca_nada(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog().by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_service(service)

    assert result.strategy == "launch"
    assert fake_tv.buttons == []


# --- tela de perfil ("Quem está assistindo?") ----------------------------
#
# Max e Disney+ mostram essa tela em TODA abertura fria (a ajuda do Max diz que em TV é
# por design para perfil adulto). Sem tratá-la, a macro de busca digita no vazio.

PROFILE_MACRO = [{"button": "ENTER"}]


async def test_abertura_fria_passa_pela_tela_de_perfil(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    fake_tv.current = "com.webos.app.livetv"  # Max fechado
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout")

    # ENTER confirma o perfil ANTES da navegação da busca
    assert fake_tv.buttons == ["ENTER", "LEFT", "LEFT"]


async def test_app_em_primeiro_plano_ainda_passa_pelo_perfil(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Este teste travava o bug ao contrário: antes, exigia que o perfil fosse PULADO.

    A premissa antiga era "app em primeiro plano = tela de perfil já resolvida". Ela é
    falsa justamente no caso que importa: parado NO seletor de perfis, o app em primeiro
    plano já é o Max. O tratamento era então desligado exatamente quando era necessário,
    e a macro de busca rodava em cima do seletor — bastava falhar uma vez para nunca
    mais sair de lá. Agora o app é fechado antes de abrir, e o perfil sempre é tratado.
    """
    fake_tv.current = "com.wbd.stream"  # parado na tela de perfil do Max
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout")

    assert fake_tv.buttons == ["ENTER", "LEFT", "LEFT"]
    assert f"close {service.app_id}" in result.trace


async def test_app_com_perfil_e_fechado_antes_de_abrir(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Fechar é o que dá controle sobre a tela em que a TV está, em vez de adivinhar."""
    fake_tv.current = "com.wbd.stream"
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_service(service)

    ordem = [nome for nome, _ in fake_tv.calls if nome in {"close", "launch"}]
    assert ordem == ["close", "launch"]


async def test_espera_a_tela_de_perfil_aparecer(session: TvSession, instant_sleep) -> None:
    """O ENTER antes da tela desenhar se perde — mesma causa das macros instáveis."""
    service = build_catalog(profile=PROFILE_MACRO, wait_before_profile=14).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout")

    # A espera do perfil vem depois da pausa de fechar (close_settle).
    assert 14 in instant_sleep.waits


async def test_perfil_nunca_espera_menos_que_a_carga_do_app(instant_sleep) -> None:
    """Configurar 8s de perfil num app que precisa de 12s para carregar manda o ENTER
    num app que ainda está subindo — e o toque some sem erro nenhum. Era o caso do Max."""
    service = build_catalog(
        profile=PROFILE_MACRO, wait_after_launch=12, wait_before_profile=8
    ).by_id("max")

    assert service.wait_before_profile == 12


async def test_foreground_para_de_perguntar_assim_que_o_app_aparece(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Medir em vez de chutar: o `launch` do fake já coloca o app em primeiro plano."""
    service = build_catalog(profile=PROFILE_MACRO, foreground_timeout=20).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_service(service)

    assert "app em primeiro plano" in result.trace
    # Uma consulta só: sem parar cedo, seriam 40 (20s / 0,5s) esperas à toa.
    assert len(fake_tv.named("get_current_app")) == 1


async def test_atalho_do_app_tambem_trata_o_perfil(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Tocar em 'Max' na tela inicial não pode deixar você parado no seletor de perfil."""
    fake_tv.current = "com.webos.app.livetv"
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_service(service)

    assert fake_tv.buttons == ["ENTER"]


async def test_sem_macro_de_perfil_nada_muda(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    fake_tv.current = "com.webos.app.livetv"
    service = build_catalog().by_id("max")  # sem `profile`
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout")

    assert fake_tv.buttons == ["LEFT", "LEFT"]


async def test_tv_muda_para_o_perfil_com_varios_botoes(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Perfil que não é o primeiro da fila: RIGHT até ele, depois ENTER."""
    fake_tv.current = "com.webos.app.livetv"
    service = build_catalog(
        profile=[{"button": "RIGHT", "times": 2}, {"button": "ENTER"}]
    ).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_service(service)

    assert fake_tv.buttons == ["RIGHT", "RIGHT", "ENTER"]


async def test_falha_ao_ler_o_app_atual_nao_aborta_a_abertura(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Não saber em que app a TV está é motivo para cair no tempo fixo, não para desistir."""

    class Unknown(FakeWebOsClient):
        async def get_current_app(self) -> str:
            raise OSError("sem resposta")

    unknown = Unknown("10.0.0.5")
    tv = TvSession("10.0.0.5", "k", client_factory=lambda h, k: unknown)
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(tv, sleep=instant_sleep)

    await opener.open_service(service)

    assert unknown.buttons == ["ENTER"]


async def test_app_que_recusa_fechar_ainda_e_aberto(
    fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """Fechar é preparação: um app que não estava aberto recusa, e isso não é problema."""

    class WontClose(FakeWebOsClient):
        async def close_app(self, app: str) -> dict[str, object]:
            raise OSError("o app não estava aberto")

    stubborn = WontClose("10.0.0.5")
    tv = TvSession("10.0.0.5", "k", client_factory=lambda h, k: stubborn)
    service = build_catalog(profile=PROFILE_MACRO).by_id("max")
    opener = TitleOpener(tv, sleep=instant_sleep)

    await opener.open_service(service)

    assert stubborn.named("launch") == ["com.wbd.stream"]
    assert stubborn.buttons == ["ENTER"]


# --- episódio ------------------------------------------------------------

EPISODE_MACRO = [
    {"button": "DOWN", "times": 2},
    {"button": "RIGHT", "times": "{episode_index}"},
    {"button": "ENTER"},
]


async def test_episodio_roda_depois_da_busca(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog(episode=EPISODE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout", season=1, episode=4)

    assert result.strategy == "episode"
    # 2x LEFT da busca, depois DOWN DOWN + 3x RIGHT (episódio 4 = índice 3) + ENTER
    assert fake_tv.buttons == [
        "LEFT", "LEFT",
        "DOWN", "DOWN",
        "RIGHT", "RIGHT", "RIGHT",
        "ENTER",
    ]


async def test_episodio_1_nao_desloca_o_foco(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog(episode=EPISODE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout", season=1, episode=1)

    assert fake_tv.buttons == ["LEFT", "LEFT", "DOWN", "DOWN", "ENTER"]


async def test_sem_macro_de_episodio_para_na_pagina_da_serie(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    """O fallback do fallback: nunca pior que o comportamento atual."""
    service = build_catalog().by_id("max")  # sem `episode`
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout", season=1, episode=4)

    assert result.strategy == "macro"
    assert fake_tv.buttons == ["LEFT", "LEFT"]  # só a busca


async def test_buscar_sem_episodio_nao_dispara_a_macro_de_episodio(
    session: TvSession, fake_tv: FakeWebOsClient, instant_sleep
) -> None:
    service = build_catalog(episode=EPISODE_MACRO).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    result = await opener.open_title(service, "Fallout")

    assert result.strategy == "macro"
    assert "ENTER" not in fake_tv.buttons


async def test_espera_a_pagina_da_serie_carregar(session: TvSession, instant_sleep) -> None:
    """Navegar antes da página abrir joga os toques no vazio."""
    service = build_catalog(episode=EPISODE_MACRO, wait_before_episode=6).by_id("max")
    opener = TitleOpener(session, sleep=instant_sleep)

    await opener.open_title(service, "Fallout", season=1, episode=2)

    assert 6 in instant_sleep.waits
