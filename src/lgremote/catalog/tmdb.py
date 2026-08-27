"""Cliente TMDb: busca um título e descobre em qual serviço ele está no Brasil.

O dado de disponibilidade vem do JustWatch via TMDb — por isso a atribuição no rodapé
do PWA, que os termos de uso exigem.

Só interessa o que está incluso na assinatura (`flatrate`, `free`, `ads`): aluguel e
compra não ajudam a decidir qual app abrir.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w185"
STILL_BASE = "https://image.tmdb.org/t/p/w300"  # frames de episódio são deitados
REGION = "BR"
LANGUAGE = "pt-BR"
INCLUDED_OFFER_TYPES = ("flatrate", "free", "ads")
CACHE_TTL_SECONDS = 6 * 60 * 60

# A página de API do TMDb mostra as duas credenciais lado a lado e é fácil copiar a
# errada. Em vez de recusar, detectamos qual é: a v3 é 32 hex, a v4 é um JWT longo.
_V3_KEY = re.compile(r"[0-9a-fA-F]{32}")


def is_v3_key(token: str) -> bool:
    return bool(_V3_KEY.fullmatch(token.strip()))


class CatalogDisabledError(RuntimeError):
    """Sem TMDB_TOKEN — a busca de títulos fica desligada, o resto do controle não."""


class CatalogError(RuntimeError):
    """O TMDb respondeu erro."""


@dataclass(frozen=True)
class Availability:
    provider_id: int
    provider_name: str
    logo_url: str | None


@dataclass(frozen=True)
class Title:
    tmdb_id: int
    media_type: str
    name: str
    year: str | None
    overview: str
    poster_url: str | None
    providers: tuple[Availability, ...]


@dataclass(frozen=True)
class Season:
    season_number: int
    name: str
    episode_count: int
    poster_url: str | None


@dataclass(frozen=True)
class Episode:
    season_number: int
    episode_number: int
    name: str
    overview: str
    still_url: str | None
    air_date: str | None
    runtime: int | None


class TmdbClient:
    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        if not token:
            raise CatalogDisabledError(
                "TMDB_TOKEN não configurado. Pegue um token gratuito em "
                "themoviedb.org > Settings > API > 'API Read Access Token'."
            )
        self._token = token.strip()
        self._uses_v3 = is_v3_key(self._token)
        self._client = client
        self._owns_client = client is None
        self._provider_cache: dict[tuple[str, int], tuple[float, tuple[Availability, ...]]] = {}

    @property
    def auth_scheme(self) -> str:
        return "chave v3" if self._uses_v3 else "token v4"

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if not self._uses_v3:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=API_BASE,
                timeout=httpx.Timeout(10.0),
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._http()
        query = dict(params or {})
        if self._uses_v3:
            query["api_key"] = self._token
        try:
            response = await client.get(path, params=query)
        except httpx.HTTPError as exc:
            raise CatalogError(f"Falha ao falar com o TMDb: {exc}") from exc
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise CatalogError(
                f"TMDB_TOKEN recusado pelo TMDb (enviado como {self.auth_scheme}). "
                "Confira a credencial em themoviedb.org > Settings > API."
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise CatalogError(f"TMDb respondeu {response.status_code}")
        data: dict[str, Any] = response.json()
        return data

    async def verify(self) -> None:
        """Confere a credencial. Levanta CatalogError se o TMDb recusar.

        Usado na configuração: descobrir que a chave está errada agora é bem melhor
        do que descobrir no sofá, com a busca vazia e sem saber por quê.
        """
        await self._get("/configuration")

    async def search(self, query: str, *, limit: int = 8) -> list[Title]:
        """Busca filmes e séries e anexa a disponibilidade no Brasil."""
        if not query.strip():
            return []

        payload = await self._get(
            "/search/multi",
            {"query": query, "language": LANGUAGE, "include_adult": "false", "page": 1},
        )
        raw_results = [
            item
            for item in payload.get("results", [])
            if item.get("media_type") in {"movie", "tv"}
        ][:limit]

        providers = await asyncio.gather(
            *(
                self._watch_providers(str(item["media_type"]), int(item["id"]))
                for item in raw_results
            ),
            return_exceptions=True,
        )

        titles: list[Title] = []
        for item, provider_result in zip(raw_results, providers, strict=True):
            # Um título sem disponibilidade ainda é útil: dá pra abrir o app na mão.
            available = provider_result if isinstance(provider_result, tuple) else ()
            titles.append(_to_title(item, available))
        return titles

    async def get_seasons(self, tmdb_id: int) -> list[Season]:
        """Temporadas de uma série.

        A temporada 0 ("Especiais") é descartada: não é o que se quer assistir e
        atrapalharia a contagem de índice usada pela macro de episódio.
        """
        payload = await self._get(f"/tv/{tmdb_id}", {"language": LANGUAGE})
        seasons: list[Season] = []
        for raw in payload.get("seasons", []) or []:
            number = int(raw.get("season_number", 0))
            if number < 1:
                continue
            poster = raw.get("poster_path")
            seasons.append(
                Season(
                    season_number=number,
                    name=str(raw.get("name") or f"Temporada {number}"),
                    episode_count=int(raw.get("episode_count", 0)),
                    poster_url=f"{IMAGE_BASE}{poster}" if poster else None,
                )
            )
        return seasons

    async def get_episodes(self, tmdb_id: int, season_number: int) -> list[Episode]:
        payload = await self._get(
            f"/tv/{tmdb_id}/season/{season_number}", {"language": LANGUAGE}
        )
        episodes: list[Episode] = []
        for raw in payload.get("episodes", []) or []:
            still = raw.get("still_path")
            runtime = raw.get("runtime")
            episodes.append(
                Episode(
                    season_number=int(raw.get("season_number", season_number)),
                    episode_number=int(raw.get("episode_number", 0)),
                    name=str(raw.get("name") or ""),
                    overview=str(raw.get("overview") or ""),
                    still_url=f"{STILL_BASE}{still}" if still else None,
                    air_date=str(raw.get("air_date") or "") or None,
                    runtime=int(runtime) if runtime else None,
                )
            )
        return episodes

    async def _watch_providers(self, media_type: str, tmdb_id: int) -> tuple[Availability, ...]:
        key = (media_type, tmdb_id)
        cached = self._provider_cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

        payload = await self._get(f"/{media_type}/{tmdb_id}/watch/providers")
        region = payload.get("results", {}).get(REGION, {})

        seen: dict[int, Availability] = {}
        for offer_type in INCLUDED_OFFER_TYPES:
            for entry in region.get(offer_type, []) or []:
                provider_id = int(entry.get("provider_id", 0))
                if provider_id in seen:
                    continue
                logo = entry.get("logo_path")
                seen[provider_id] = Availability(
                    provider_id=provider_id,
                    provider_name=str(entry.get("provider_name", "")),
                    logo_url=f"{IMAGE_BASE}{logo}" if logo else None,
                )

        result = tuple(seen.values())
        self._provider_cache[key] = (now, result)
        return result


def _to_title(item: dict[str, Any], providers: tuple[Availability, ...]) -> Title:
    media_type = str(item.get("media_type"))
    name = str(item.get("title") or item.get("name") or "")
    date = str(item.get("release_date") or item.get("first_air_date") or "")
    poster = item.get("poster_path")
    return Title(
        tmdb_id=int(item["id"]),
        media_type=media_type,
        name=name,
        year=date[:4] or None,
        overview=str(item.get("overview") or ""),
        poster_url=f"{IMAGE_BASE}{poster}" if poster else None,
        providers=providers,
    )
