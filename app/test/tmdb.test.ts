/** Cliente TMDb: autenticação, disponibilidade no Brasil e cache. */

import { describe, expect, it, vi } from "vitest";

import {
  CatalogDisabledError,
  CatalogError,
  TmdbClient,
  isV3Key,
} from "../src/catalog/tmdb.ts";

const V3 = "0123456789abcdef0123456789abcdef";
const V4 = "eyJhbGciOiJIUzI1NiJ9.um-jwt-bem-mais-longo-que-trinta-e-dois";

/**
 * Responde por caminho da URL, e anota tudo que foi pedido.
 *
 * Uma rota pode devolver um número em vez de corpo: é o código HTTP a simular.
 */
function fakeApi(routes: Record<string, unknown>) {
  const calls: { url: URL; headers: Record<string, string> }[] = [];
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    const parsed = new URL(url);
    calls.push({ url: parsed, headers: (init.headers ?? {}) as Record<string, string> });
    const route = routes[parsed.pathname];
    if (typeof route === "number") {
      return new Response("{}", { status: route });
    }
    return new Response(JSON.stringify(route ?? {}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  return { fetcher, calls };
}

describe("credencial", () => {
  it("reconhece a chave v3 pelos 32 hex", () => {
    // A página do TMDb mostra as duas credenciais lado a lado; é fácil copiar a errada.
    expect(isV3Key(V3)).toBe(true);
    expect(isV3Key(V4)).toBe(false);
  });

  it("manda a v3 na query e a v4 no cabeçalho", async () => {
    const v3Api = fakeApi({ "/3/configuration": { images: {} } });
    await new TmdbClient(V3, { fetcher: v3Api.fetcher }).verify();
    expect(v3Api.calls[0]?.url.searchParams.get("api_key")).toBe(V3);
    expect(v3Api.calls[0]?.headers["Authorization"]).toBeUndefined();

    const v4Api = fakeApi({ "/3/configuration": { images: {} } });
    await new TmdbClient(V4, { fetcher: v4Api.fetcher }).verify();
    expect(v4Api.calls[0]?.url.searchParams.get("api_key")).toBeNull();
    expect(v4Api.calls[0]?.headers["Authorization"]).toBe(`Bearer ${V4}`);
  });

  it("desliga a busca em vez de quebrar quando não há token", () => {
    expect(() => new TmdbClient("")).toThrow(CatalogDisabledError);
  });

  it("explica qual credencial foi recusada", async () => {
    // Sem isso o usuário fica com a busca vazia e sem saber por quê.
    const { fetcher } = fakeApi({ "/3/configuration": 401 });
    const client = new TmdbClient(V3, { fetcher });

    await expect(client.verify()).rejects.toThrow(/chave v3/);
    await expect(client.verify()).rejects.toBeInstanceOf(CatalogError);
  });
});

describe("busca", () => {
  const SEARCH = {
    results: [
      {
        id: 100088,
        media_type: "tv",
        name: "The Last of Us",
        first_air_date: "2023-01-15",
        overview: "Vinte anos depois...",
        poster_path: "/poster.jpg",
      },
      { id: 1, media_type: "person", name: "Alguém" }, // precisa ser filtrado
    ],
  };
  const PROVIDERS = {
    results: {
      BR: {
        flatrate: [{ provider_id: 1899, provider_name: "Max", logo_path: "/max.jpg" }],
        rent: [{ provider_id: 2, provider_name: "Apple TV", logo_path: "/a.jpg" }],
      },
    },
  };

  it("descarta o que não é filme nem série", async () => {
    const { fetcher } = fakeApi({
      "/3/search/multi": SEARCH,
      "/3/tv/100088/watch/providers": PROVIDERS,
    });

    const titles = await new TmdbClient(V4, { fetcher }).search("last of us");

    expect(titles).toHaveLength(1);
    expect(titles[0]?.name).toBe("The Last of Us");
    expect(titles[0]?.year).toBe("2023");
  });

  it("só traz o que está incluso na assinatura", async () => {
    // Aluguel e compra não ajudam a decidir qual app abrir.
    const { fetcher } = fakeApi({
      "/3/search/multi": SEARCH,
      "/3/tv/100088/watch/providers": PROVIDERS,
    });

    const titles = await new TmdbClient(V4, { fetcher }).search("last of us");

    expect(titles[0]?.providers.map((p) => p.providerName)).toEqual(["Max"]);
  });

  it("mantém o título mesmo sem disponibilidade", async () => {
    // Um título sem provedor ainda é útil: dá pra abrir o app na mão.
    const { fetcher } = fakeApi({
      "/3/search/multi": SEARCH,
      "/3/tv/100088/watch/providers": 500,
    });

    const titles = await new TmdbClient(V4, { fetcher }).search("last of us");

    expect(titles).toHaveLength(1);
    expect(titles[0]?.providers).toEqual([]);
  });

  it("não vai à rede com busca vazia", async () => {
    const { fetcher } = fakeApi({});

    expect(await new TmdbClient(V4, { fetcher }).search("   ")).toEqual([]);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reaproveita a disponibilidade em cache", async () => {
    const api = fakeApi({
      "/3/search/multi": SEARCH,
      "/3/tv/100088/watch/providers": PROVIDERS,
    });
    const client = new TmdbClient(V4, { fetcher: api.fetcher, clock: () => 0 });

    await client.search("last of us");
    await client.search("last of us");

    const providerCalls = api.calls.filter((c) => c.url.pathname.includes("watch/providers"));
    expect(providerCalls).toHaveLength(1);
  });

  it("busca de novo quando o cache envelhece", async () => {
    let clock = 0;
    const api = fakeApi({
      "/3/search/multi": SEARCH,
      "/3/tv/100088/watch/providers": PROVIDERS,
    });
    const client = new TmdbClient(V4, { fetcher: api.fetcher, clock: () => clock });

    await client.search("last of us");
    clock += 7 * 60 * 60;
    await client.search("last of us");

    const providerCalls = api.calls.filter((c) => c.url.pathname.includes("watch/providers"));
    expect(providerCalls).toHaveLength(2);
  });
});

describe("temporadas e episódios", () => {
  it("descarta a temporada 0 (Especiais)", async () => {
    // Ela atrapalharia a contagem de índice usada pela macro de episódio.
    const { fetcher } = fakeApi({
      "/3/tv/100088": {
        seasons: [
          { season_number: 0, name: "Especiais", episode_count: 3 },
          { season_number: 1, name: "Temporada 1", episode_count: 9, poster_path: "/p.jpg" },
        ],
      },
    });

    const seasons = await new TmdbClient(V4, { fetcher }).getSeasons(100088);

    expect(seasons.map((s) => s.seasonNumber)).toEqual([1]);
    expect(seasons[0]?.posterUrl).toMatch(/\/p\.jpg$/);
  });

  it("traz os episódios com o frame deitado", async () => {
    const { fetcher } = fakeApi({
      "/3/tv/100088/season/1": {
        episodes: [
          {
            season_number: 1,
            episode_number: 1,
            name: "Quando Você Está Perdido",
            overview: "...",
            still_path: "/still.jpg",
            air_date: "2023-01-15",
            runtime: 81,
          },
        ],
      },
    });

    const episodes = await new TmdbClient(V4, { fetcher }).getEpisodes(100088, 1);

    expect(episodes[0]?.episodeNumber).toBe(1);
    expect(episodes[0]?.runtime).toBe(81);
    expect(episodes[0]?.stillUrl).toContain("/t/p/w300");
  });
});
