"""Busca no TMDb e filtro de disponibilidade no Brasil."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from lgremote.catalog.tmdb import CatalogDisabledError, CatalogError, TmdbClient, is_v3_key

SEARCH_PAYLOAD = {
    "results": [
        {
            "id": 100088,
            "media_type": "tv",
            "name": "The Last of Us",
            "first_air_date": "2023-01-15",
            "poster_path": "/abc.jpg",
            "overview": "…",
        },
        {"id": 999, "media_type": "person", "name": "Pedro Pascal"},
        {
            "id": 42,
            "media_type": "movie",
            "title": "Filme Sem Streaming",
            "release_date": "2019-03-01",
            "poster_path": None,
            "overview": "",
        },
    ]
}

PROVIDERS_TV = {
    "results": {
        "BR": {
            "flatrate": [{"provider_id": 1899, "provider_name": "Max", "logo_path": "/m.jpg"}],
            "rent": [{"provider_id": 2, "provider_name": "Apple TV", "logo_path": "/a.jpg"}],
        },
        "US": {"flatrate": [{"provider_id": 15, "provider_name": "Hulu"}]},
    }
}

PROVIDERS_MOVIE: dict[str, Any] = {"results": {}}


def build_client(handler) -> TmdbClient:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.themoviedb.org/3")
    return TmdbClient("token-falso", client=http)


def default_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/search/multi"):
        return httpx.Response(200, json=SEARCH_PAYLOAD)
    if path == "/3/tv/100088/watch/providers":
        return httpx.Response(200, json=PROVIDERS_TV)
    if path == "/3/movie/42/watch/providers":
        return httpx.Response(200, json=PROVIDERS_MOVIE)
    return httpx.Response(404, json={})


def test_sem_token_o_erro_explica_onde_pegar_um() -> None:
    with pytest.raises(CatalogDisabledError, match=r"themoviedb\.org"):
        TmdbClient("")


# --- as duas credenciais do TMDb -----------------------------------------
#
# A página de API mostra a chave v3 e o token v4 lado a lado. Aceitar as duas
# custa pouco e evita um 401 silencioso na primeira busca.


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0123456789abcdef0123456789abcdef", True),  # v3: 32 hex
        ("0123456789ABCDEF0123456789ABCDEF", True),  # maiúsculas também
        ("  0123456789abcdef0123456789abcdef  ", True),  # colado com espaço
        ("eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJhYmMifQ.xxxxx", False),  # v4: JWT
        ("0123456789abcdef0123456789abcde", False),  # 31 chars
        ("z123456789abcdef0123456789abcdef", False),  # não é hex
    ],
)
def test_detecta_qual_credencial_foi_colada(token: str, expected: bool) -> None:
    assert is_v3_key(token) is expected


async def test_chave_v3_vai_na_query_string() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    transport = httpx.MockTransport(capture)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.themoviedb.org/3")
    client = TmdbClient("0123456789abcdef0123456789abcdef", client=http)

    assert client.auth_scheme == "chave v3"
    await client.verify()

    assert seen[0].url.params["api_key"] == "0123456789abcdef0123456789abcdef"
    assert "Authorization" not in seen[0].headers


async def test_token_v4_vai_no_header() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(capture),
        base_url="https://api.themoviedb.org/3",
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.abc"},
    )
    client = TmdbClient("eyJhbGciOiJIUzI1NiJ9.abc", client=http)

    assert client.auth_scheme == "token v4"
    await client.verify()

    assert "api_key" not in seen[0].url.params


async def test_verify_reporta_credencial_recusada() -> None:
    client = build_client(lambda request: httpx.Response(401, json={}))
    with pytest.raises(CatalogError, match="recusado"):
        await client.verify()


async def test_busca_descarta_pessoas() -> None:
    """search/multi devolve atores junto; eles não abrem em lugar nenhum."""
    client = build_client(default_handler)
    results = await client.search("the last of us")
    assert [title.media_type for title in results] == ["tv", "movie"]


async def test_usa_so_o_que_esta_incluso_na_assinatura_no_brasil() -> None:
    """Aluguel e compra não ajudam a decidir qual app abrir; região US não interessa."""
    client = build_client(default_handler)
    results = await client.search("the last of us")
    assert [p.provider_name for p in results[0].providers] == ["Max"]


async def test_titulo_sem_disponibilidade_ainda_aparece() -> None:
    client = build_client(default_handler)
    results = await client.search("the last of us")
    assert results[1].name == "Filme Sem Streaming"
    assert results[1].providers == ()


async def test_ano_e_poster_normalizados() -> None:
    client = build_client(default_handler)
    results = await client.search("x")
    assert results[0].year == "2023"
    assert results[0].poster_url == "https://image.tmdb.org/t/p/w185/abc.jpg"
    assert results[1].poster_url is None


async def test_falha_de_providers_nao_derruba_a_busca() -> None:
    """Um provedor fora do ar não pode transformar a busca inteira em erro."""

    def flaky(request: httpx.Request) -> httpx.Response:
        if "watch/providers" in request.url.path:
            return httpx.Response(500, json={})
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    client = build_client(flaky)
    results = await client.search("x")
    assert len(results) == 2
    assert all(title.providers == () for title in results)


async def test_token_invalido_da_mensagem_acionavel() -> None:
    client = build_client(lambda request: httpx.Response(401, json={}))
    with pytest.raises(CatalogError, match="themoviedb"):
        await client.search("x")


async def test_providers_ficam_em_cache() -> None:
    calls: list[str] = []

    def counting(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return default_handler(request)

    client = build_client(counting)
    await client.search("x")
    await client.search("x")

    provider_calls = [path for path in calls if "watch/providers" in path]
    assert len(provider_calls) == 2  # 2 títulos, buscados uma vez cada


# --- temporadas e episódios ----------------------------------------------

SERIES_PAYLOAD = {
    "seasons": [
        {"season_number": 0, "name": "Especiais", "episode_count": 3},
        {"season_number": 1, "name": "Temporada 1", "episode_count": 9, "poster_path": "/s1.jpg"},
        {"season_number": 2, "name": "Temporada 2", "episode_count": 7},
    ]
}

SEASON_PAYLOAD = {
    "episodes": [
        {
            "season_number": 1,
            "episode_number": 1,
            "name": "Quando você está perdido na escuridão",
            "still_path": "/e1.jpg",
            "air_date": "2023-01-15",
            "runtime": 81,
        },
        {
            "season_number": 1,
            "episode_number": 2,
            "name": "Infectados",
            "still_path": None,
            "runtime": None,
        },
    ]
}


def series_handler(request: httpx.Request) -> httpx.Response:
    if "/season/" in request.url.path:
        return httpx.Response(200, json=SEASON_PAYLOAD)
    return httpx.Response(200, json=SERIES_PAYLOAD)


async def test_especiais_nao_entram_na_lista() -> None:
    """A temporada 0 estragaria a contagem de índice usada pela macro."""
    client = build_client(series_handler)
    seasons = await client.get_seasons(100088)

    assert [season.season_number for season in seasons] == [1, 2]


async def test_episodios_trazem_o_que_a_ui_precisa() -> None:
    client = build_client(series_handler)
    episodes = await client.get_episodes(100088, 1)

    assert [episode.episode_number for episode in episodes] == [1, 2]
    assert episodes[0].name == "Quando você está perdido na escuridão"
    assert episodes[0].still_url == "https://image.tmdb.org/t/p/w300/e1.jpg"
    assert episodes[0].runtime == 81


async def test_episodio_sem_imagem_ou_duracao_nao_quebra() -> None:
    client = build_client(series_handler)
    episodes = await client.get_episodes(100088, 1)

    assert episodes[1].still_url is None
    assert episodes[1].runtime is None


async def test_serie_sem_temporadas() -> None:
    client = build_client(lambda request: httpx.Response(200, json={}))
    assert await client.get_seasons(1) == []


async def test_query_vazia_nao_bate_na_api() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a API")

    client = build_client(explode)
    assert await client.search("   ") == []
