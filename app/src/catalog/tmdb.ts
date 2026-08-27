/**
 * Cliente TMDb: busca um título e descobre em qual serviço ele está no Brasil.
 *
 * O dado de disponibilidade vem do JustWatch via TMDb — por isso a atribuição no
 * rodapé da tela de busca, que os termos de uso exigem.
 *
 * Só interessa o que está incluso na assinatura (`flatrate`, `free`, `ads`): aluguel
 * e compra não ajudam a decidir qual app abrir.
 */

export const API_BASE = "https://api.themoviedb.org/3";
export const IMAGE_BASE = "https://image.tmdb.org/t/p/w185";
/** Frames de episódio são deitados, então pedem um recorte mais largo. */
export const STILL_BASE = "https://image.tmdb.org/t/p/w300";
export const REGION = "BR";
export const LANGUAGE = "pt-BR";
export const INCLUDED_OFFER_TYPES = ["flatrate", "free", "ads"] as const;
export const CACHE_TTL_SECONDS = 6 * 60 * 60;
export const REQUEST_TIMEOUT_MS = 10_000;

/**
 * A página de API do TMDb mostra as duas credenciais lado a lado e é fácil copiar a
 * errada. Em vez de recusar, detectamos qual é: a v3 é 32 hex, a v4 é um JWT longo.
 */
const V3_KEY = /^[0-9a-fA-F]{32}$/;

export function isV3Key(token: string): boolean {
  return V3_KEY.test(token.trim());
}

/** Sem token — a busca de títulos fica desligada, o resto do controle não. */
export class CatalogDisabledError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CatalogDisabledError";
  }
}

export class CatalogError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CatalogError";
  }
}

export interface Availability {
  providerId: number;
  providerName: string;
  logoUrl: string | null;
}

export interface Title {
  tmdbId: number;
  mediaType: string;
  name: string;
  year: string | null;
  overview: string;
  posterUrl: string | null;
  providers: Availability[];
}

export interface Season {
  seasonNumber: number;
  name: string;
  episodeCount: number;
  posterUrl: string | null;
}

export interface Episode {
  seasonNumber: number;
  episodeNumber: number;
  name: string;
  overview: string;
  stillUrl: string | null;
  airDate: string | null;
  runtime: number | null;
}

/** `fetch` isolado num tipo para o teste responder sem rede. */
export type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

export interface TmdbClientOptions {
  fetcher?: Fetcher;
  /** Relógio em segundos, para o teste envelhecer o cache sem esperar 6 horas. */
  clock?: () => number;
}

interface CacheEntry {
  at: number;
  providers: Availability[];
}

export class TmdbClient {
  private readonly token: string;
  private readonly usesV3: boolean;
  private readonly fetcher: Fetcher;
  private readonly clock: () => number;
  private readonly providerCache = new Map<string, CacheEntry>();

  constructor(token: string, options: TmdbClientOptions = {}) {
    if (!token) {
      throw new CatalogDisabledError(
        "Token do TMDb não configurado. Pegue um token gratuito em " +
          "themoviedb.org > Settings > API > 'API Read Access Token'.",
      );
    }
    this.token = token.trim();
    this.usesV3 = isV3Key(this.token);
    this.fetcher = options.fetcher ?? ((url, init) => fetch(url, init));
    this.clock = options.clock ?? (() => Date.now() / 1000);
  }

  get authScheme(): string {
    return this.usesV3 ? "chave v3" : "token v4";
  }

  private async get(
    path: string,
    params: Record<string, string> = {},
  ): Promise<Record<string, unknown>> {
    const url = new URL(API_BASE + path);
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, value);
    }
    if (this.usesV3) url.searchParams.set("api_key", this.token);

    const headers: Record<string, string> = { Accept: "application/json" };
    if (!this.usesV3) headers["Authorization"] = `Bearer ${this.token}`;

    let response: Response;
    try {
      response = await this.fetcher(url.toString(), {
        headers,
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (cause) {
      throw new CatalogError(
        `Falha ao falar com o TMDb: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    }

    if (response.status === 401) {
      throw new CatalogError(
        `Token do TMDb recusado (enviado como ${this.authScheme}). ` +
          "Confira a credencial em themoviedb.org > Settings > API.",
      );
    }
    if (response.status >= 400) {
      throw new CatalogError(`TMDb respondeu ${response.status}`);
    }
    return (await response.json()) as Record<string, unknown>;
  }

  /**
   * Confere a credencial. Levanta CatalogError se o TMDb recusar.
   *
   * Usado na configuração: descobrir que a chave está errada agora é bem melhor do
   * que descobrir no sofá, com a busca vazia e sem saber por quê.
   */
  async verify(): Promise<void> {
    await this.get("/configuration");
  }

  /** Busca filmes e séries e anexa a disponibilidade no Brasil. */
  async search(query: string, limit = 8): Promise<Title[]> {
    if (!query.trim()) return [];

    const payload = await this.get("/search/multi", {
      query,
      language: LANGUAGE,
      include_adult: "false",
      page: "1",
    });

    const results = ((payload["results"] as Record<string, unknown>[] | undefined) ?? [])
      .filter((item) => item["media_type"] === "movie" || item["media_type"] === "tv")
      .slice(0, limit);

    const providers = await Promise.all(
      results.map((item) =>
        // Um título sem disponibilidade ainda é útil: dá pra abrir o app na mão.
        this.watchProviders(String(item["media_type"]), Number(item["id"])).catch(() => []),
      ),
    );

    return results.map((item, index) => toTitle(item, providers[index] ?? []));
  }

  /**
   * Temporadas de uma série.
   *
   * A temporada 0 ("Especiais") é descartada: não é o que se quer assistir e
   * atrapalharia a contagem de índice usada pela macro de episódio.
   */
  async getSeasons(tmdbId: number): Promise<Season[]> {
    const payload = await this.get(`/tv/${tmdbId}`, { language: LANGUAGE });
    const raw = (payload["seasons"] as Record<string, unknown>[] | undefined) ?? [];

    return raw
      .filter((entry) => Number(entry["season_number"] ?? 0) >= 1)
      .map((entry) => {
        const number = Number(entry["season_number"] ?? 0);
        const poster = entry["poster_path"];
        return {
          seasonNumber: number,
          name: String(entry["name"] ?? "") || `Temporada ${number}`,
          episodeCount: Number(entry["episode_count"] ?? 0),
          posterUrl: poster ? `${IMAGE_BASE}${String(poster)}` : null,
        };
      });
  }

  async getEpisodes(tmdbId: number, seasonNumber: number): Promise<Episode[]> {
    const payload = await this.get(`/tv/${tmdbId}/season/${seasonNumber}`, {
      language: LANGUAGE,
    });
    const raw = (payload["episodes"] as Record<string, unknown>[] | undefined) ?? [];

    return raw.map((entry) => {
      const still = entry["still_path"];
      const runtime = entry["runtime"];
      return {
        seasonNumber: Number(entry["season_number"] ?? seasonNumber),
        episodeNumber: Number(entry["episode_number"] ?? 0),
        name: String(entry["name"] ?? ""),
        overview: String(entry["overview"] ?? ""),
        stillUrl: still ? `${STILL_BASE}${String(still)}` : null,
        airDate: String(entry["air_date"] ?? "") || null,
        runtime: runtime ? Number(runtime) : null,
      };
    });
  }

  private async watchProviders(mediaType: string, tmdbId: number): Promise<Availability[]> {
    const key = `${mediaType}:${tmdbId}`;
    const now = this.clock();
    const cached = this.providerCache.get(key);
    if (cached && now - cached.at < CACHE_TTL_SECONDS) return cached.providers;

    const payload = await this.get(`/${mediaType}/${tmdbId}/watch/providers`);
    const results = (payload["results"] ?? {}) as Record<string, unknown>;
    const region = (results[REGION] ?? {}) as Record<string, unknown>;

    const seen = new Map<number, Availability>();
    for (const offerType of INCLUDED_OFFER_TYPES) {
      const entries = (region[offerType] as Record<string, unknown>[] | undefined) ?? [];
      for (const entry of entries) {
        const providerId = Number(entry["provider_id"] ?? 0);
        if (seen.has(providerId)) continue;
        const logo = entry["logo_path"];
        seen.set(providerId, {
          providerId,
          providerName: String(entry["provider_name"] ?? ""),
          logoUrl: logo ? `${IMAGE_BASE}${String(logo)}` : null,
        });
      }
    }

    const providers = [...seen.values()];
    this.providerCache.set(key, { at: now, providers });
    return providers;
  }
}

function toTitle(item: Record<string, unknown>, providers: Availability[]): Title {
  const date = String(item["release_date"] ?? item["first_air_date"] ?? "");
  const poster = item["poster_path"];
  return {
    tmdbId: Number(item["id"]),
    mediaType: String(item["media_type"]),
    name: String(item["title"] ?? item["name"] ?? ""),
    year: date.slice(0, 4) || null,
    overview: String(item["overview"] ?? ""),
    posterUrl: poster ? `${IMAGE_BASE}${String(poster)}` : null,
    providers,
  };
}
