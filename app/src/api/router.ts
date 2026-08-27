/**
 * O roteador local: as mesmas rotas do servidor Python, rodando dentro do telefone.
 *
 * Existe para que a UI não precisasse ser reescrita. `app.js` continua chamando
 * `api("/api/status")`; a diferença é que agora ninguém sai pela rede — a chamada cai
 * direto nas funções portadas. Foi o que permitiu trocar o servidor por nada e mexer
 * numa função só do front.
 *
 * Os formatos de resposta são os do servidor, campo por campo, incluindo o snake_case:
 * qualquer divergência apareceria como um pedaço da tela em branco.
 */

import { UnknownButtonError } from "../tv/buttons.ts";
import { MACRO_KEYS, writeMacro, canPickEpisode, canSearch, type Service } from "../tv/apps.ts";
import { CatalogDisabledError, CatalogError, type Title } from "../catalog/tmdb.ts";
import { runSteps, stepToYaml } from "../tv/macros.ts";
import { PresetError, nowStamp, slugify, type Preset } from "../tv/presets.ts";
import { type RecordTarget } from "../tv/recorder.ts";
import { PairError, PairRefusedError, pair } from "../tv/pairing.ts";
import { SSAP_SECURE_PORT } from "../tv/client.ts";
import type { AppState } from "./state.ts";

/** Mesmo respiro que o `delay` padrão das macros usa entre repetições. */
export const BUTTON_REPEAT_DELAY = 0.2;
export const MAX_BUTTON_REPEAT = 30;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions {
  method?: string;
  body?: string;
}

function serializePreset(preset: Preset): Record<string, unknown> {
  return {
    id: preset.id,
    label: preset.label,
    service_id: preset.serviceId,
    steps: preset.steps.length,
    recorded_at: preset.recordedAt ?? null,
  };
}

/**
 * Traduz provedores do TMDb em serviços que ESTA TV sabe abrir.
 *
 * A tela só deve mostrar botão para o que realmente dá para abrir; oferecer um
 * serviço sem app instalado é prometer o que não se cumpre.
 */
function serializeTitle(title: Title, state: AppState): Record<string, unknown> {
  const services: { id: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const provider of title.providers) {
    const service = state.catalog.byTmdbProvider(provider.providerName);
    if (!service || seen.has(service.id)) continue;
    seen.add(service.id);
    services.push({ id: service.id, label: service.label });
  }

  return {
    tmdb_id: title.tmdbId,
    media_type: title.mediaType,
    name: title.name,
    year: title.year,
    poster_url: title.posterUrl,
    providers: title.providers.map((p) => p.providerName),
    services,
  };
}

export function createRouter(state: AppState) {
  const requireCatalog = () => {
    if (!state.tmdb) {
      throw new ApiError(503, "Busca desligada: configure o token do TMDb nos ajustes.");
    }
    return state.tmdb;
  };

  const requireService = (serviceId: unknown): Service => {
    const service = state.catalog.byId(String(serviceId ?? ""));
    if (!service) throw new ApiError(404, "Serviço desconhecido");
    return service;
  };

  const routes: Record<string, (body: Record<string, unknown>, params: string[], query: URLSearchParams) => Promise<unknown>> = {
    // --- estado --------------------------------------------------------

    "GET /api/config": async () => ({
      // Da sessão, não do armazenamento: depois de a TV trocar de IP o valor salvo
      // está velho, e a tela mostraria um endereço que não existe.
      tv_host: state.session.host,
      paired: state.paired,
      catalog_enabled: state.tmdb !== null,
      can_wake: state.canWake,
      shortcuts: state.catalog.shortcutServices().map((service) => ({
        id: service.id,
        label: service.label,
        can_search: canSearch(service),
        can_pick_episode: canPickEpisode(service),
      })),
    }),

    "GET /api/status": async () => {
      let current: string | null;
      let volume: number | null;
      try {
        current = await state.session.currentApp();
        volume = await state.session.getVolume();
      } catch (error) {
        return { online: false, detail: error instanceof Error ? error.message : String(error) };
      }
      const service = state.catalog.byAppId(current ?? "");
      return {
        online: true,
        current_app: current,
        current_service: service?.label ?? null,
        // A tela usa isto para mostrar só os presets do app que está em foco.
        current_service_id: service?.id ?? null,
        volume,
        recording: state.recorder.active,
        recording_presses: state.recorder.count,
      };
    },

    "GET /api/action": async () => ({ ...state.runner.state }),

    // --- controle direto -----------------------------------------------

    "POST /api/button": async (body) => {
      const name = String(body["name"] ?? "");
      const times = Math.min(Math.max(Number(body["times"] ?? 1), 1), MAX_BUTTON_REPEAT);

      for (let index = 0; index < times; index += 1) {
        try {
          await state.session.button(name);
        } catch (error) {
          if (error instanceof UnknownButtonError) throw new ApiError(400, error.message);
          throw error;
        }
        // Depois de chegar na TV: gravar um toque que falhou daria macro mentirosa.
        try {
          state.recorder.observe(name.trim().toUpperCase());
        } catch (error) {
          state.recorder.cancel();
          throw new ApiError(409, error instanceof Error ? error.message : String(error));
        }
        // O mesmo respiro que as macros dão entre repetições: sem ele a TV engole
        // toques mandados em rajada.
        if (index < times - 1) await state.sleep(BUTTON_REPEAT_DELAY);
      }
      return { status: "ok" };
    },

    "POST /api/text": async (body) => {
      const text = String(body["text"] ?? "").slice(0, 200);
      await state.session.insertText(text, true);
      if (body["enter"]) await state.session.sendEnter();
      // Depois de chegar na TV, como no /api/button. É este passo que faz a busca
      // acontecer — sem ele, a macro gravada levaria até o campo e pararia ali.
      state.recorder.observeText(text);
      return { status: "ok" };
    },

    "POST /api/volume": async (body) => {
      await state.session.setVolume(Math.min(Math.max(Number(body["value"] ?? 0), 0), 100));
      return { status: "ok" };
    },

    "POST /api/mute": async (body) => {
      await state.session.setMute(Boolean(body["mute"]));
      return { status: "ok" };
    },

    "POST /api/power/off": async () => {
      await state.session.powerOff();
      return { status: "ok" };
    },

    "POST /api/power/on": async () => {
      try {
        await state.wake();
      } catch (error) {
        throw new ApiError(400, error instanceof Error ? error.message : String(error));
      }
      return { status: "ok", detail: "magic packet enviado" };
    },

    // --- apps e títulos ------------------------------------------------

    "POST /api/launch": async (body) => {
      const service = requireService(body["service_id"]);
      await state.session.launch(service.appId);
      return { status: "ok", app_id: service.appId };
    },

    "GET /api/search": async (_body, _params, query) => {
      const term = (query.get("q") ?? "").slice(0, 100);
      if (!term) throw new ApiError(400, "Busca vazia");
      try {
        const titles = await requireCatalog().search(term);
        return { results: titles.map((title) => serializeTitle(title, state)) };
      } catch (error) {
        if (error instanceof CatalogError || error instanceof CatalogDisabledError) {
          throw new ApiError(502, error.message);
        }
        throw error;
      }
    },

    "POST /api/open": async (body) => {
      const service = requireService(body["service_id"]);
      const title = String(body["title"] ?? "").slice(0, 200);
      if (!title) throw new ApiError(400, "Título vazio");

      const season = body["season"] ? Number(body["season"]) : null;
      const episode = body["episode"] ? Number(body["episode"]) : null;

      let label = `Abrindo ${service.label}: ${title}`;
      if (season && episode) {
        label += ` T${season}E${episode}`;
        if (!canPickEpisode(service)) {
          // Dizer isto agora evita você achar que o app travou esperando o episódio.
          label += " (sem macro de episódio — vai parar na página da série)";
        }
      }

      const started = state.runner.start(label, async () => {
        const result = await state.opener.openTitle(service, title, {
          year: body["year"] ? String(body["year"]) : null,
          tmdbId: body["tmdb_id"] ? String(body["tmdb_id"]) : null,
          season,
          episode,
        });
        return result.trace;
      });
      if (!started) {
        throw new ApiError(409, "Já tem uma ação rodando na TV. Espere terminar.");
      }
      return { status: "started", label };
    },

    "GET /api/seasons": async (_body, params) => {
      const tmdbId = Number(params[0]);
      try {
        const seasons = await requireCatalog().getSeasons(tmdbId);
        return {
          seasons: seasons.map((season) => ({
            season_number: season.seasonNumber,
            name: season.name,
            episode_count: season.episodeCount,
          })),
        };
      } catch (error) {
        if (error instanceof CatalogError || error instanceof CatalogDisabledError) {
          throw new ApiError(502, error.message);
        }
        throw error;
      }
    },

    "GET /api/episodes": async (_body, params) => {
      const tmdbId = Number(params[0]);
      const seasonNumber = Number(params[1]);
      try {
        const episodes = await requireCatalog().getEpisodes(tmdbId, seasonNumber);
        return {
          episodes: episodes.map((episode) => ({
            season_number: episode.seasonNumber,
            episode_number: episode.episodeNumber,
            name: episode.name,
            still_url: episode.stillUrl,
            runtime: episode.runtime,
          })),
        };
      } catch (error) {
        if (error instanceof CatalogError || error instanceof CatalogDisabledError) {
          throw new ApiError(502, error.message);
        }
        throw error;
      }
    },

    // --- gravador ------------------------------------------------------

    "GET /api/record": async () => ({
      recording: state.recorder.active,
      presses: state.recorder.count,
      service_id: state.recorder.serviceId,
      target: state.recorder.target,
    }),

    "POST /api/record/start": async (body) => {
      const target = String(body["target"] ?? "preset");
      if (target !== "preset" && !(MACRO_KEYS as readonly string[]).includes(target)) {
        throw new ApiError(400, `Alvo desconhecido: "${target}"`);
      }

      // Para macro do apps.yaml o serviço pode vir escolhido na tela; para preset ele
      // tem de ser o app em foco, senão o preset nasce preso ao serviço errado.
      const serviceId = (body["service_id"] as string | undefined) || (await state.currentServiceId());
      if (!serviceId || !state.catalog.byId(serviceId)) {
        throw new ApiError(
          409,
          "Abra o app na TV antes de gravar — é ele que define de quem é a gravação.",
        );
      }

      try {
        state.recorder.start({
          serviceId,
          appId: null,
          target: target as RecordTarget,
          sampleTitle: String(body["sample_title"] ?? "").trim().slice(0, 200),
        });
      } catch (error) {
        throw new ApiError(409, error instanceof Error ? error.message : String(error));
      }
      return { status: "recording", service_id: serviceId, target };
    },

    "POST /api/record/cancel": async () => {
      state.recorder.cancel();
      return { status: "ok" };
    },

    "POST /api/record/stop": async (body) => {
      const target = state.recorder.target;
      let steps, recordedService;
      try {
        ({ steps, serviceId: recordedService } = state.recorder.stop());
      } catch (error) {
        throw new ApiError(409, error instanceof Error ? error.message : String(error));
      }

      const serviceId = (body["service_id"] as string | undefined) || recordedService;
      if (!serviceId || !state.catalog.byId(serviceId)) {
        throw new ApiError(400, "Serviço desconhecido");
      }

      if ((MACRO_KEYS as readonly string[]).includes(target)) {
        try {
          const text = writeMacro(await state.catalogText(), serviceId, target, steps);
          await state.saveCatalog(text);
        } catch (error) {
          throw new ApiError(409, error instanceof Error ? error.message : String(error));
        }
        return {
          status: "saved",
          target,
          service_id: serviceId,
          steps: steps.map(stepToYaml),
        };
      }

      const label = String(body["label"] ?? "").trim().slice(0, 40);
      if (!label) throw new ApiError(400, "Dê um nome ao preset.");

      const preset: Preset = {
        id: slugify(label),
        label,
        serviceId,
        steps,
        recordedAt: nowStamp(),
      };
      try {
        state.presets.put(preset);
        await state.savePresets();
      } catch (error) {
        if (error instanceof PresetError) throw new ApiError(409, error.message);
        throw error;
      }
      return { status: "saved", preset: serializePreset(preset) };
    },

    // --- pareamento ----------------------------------------------------
    //
    // Isto não existia no PWA: recuperar um pareamento perdido exigia voltar ao PC e
    // rodar `lgremote pair`. Era uma das três formas de "perder o pareamento" que
    // motivaram o app.

    "POST /api/pair/scan": async () => {
      if (!state.scan) {
        throw new ApiError(503, "A varredura da rede precisa do app nativo.");
      }
      const candidates = await state.scan();
      return { candidates: candidates.map((c) => ({ host: c.host, port: c.port })) };
    },

    "POST /api/pair": async (body) => {
      const host = String(body["host"] ?? "").trim();
      if (!host) throw new ApiError(400, "Informe o endereço da TV.");
      const port = Number(body["port"]) || SSAP_SECURE_PORT;

      try {
        const clientKey = await pair(host, port, state.transport ? { transport: state.transport } : {});
        await state.adoptTv(host, port, clientKey);
        return { status: "ok", host, port };
      } catch (error) {
        if (error instanceof PairRefusedError) {
          // `prompted` é o que separa "tente de novo e aceite" de "resolva o standby
          // primeiro" — dois conselhos opostos para a mesma tela de erro.
          throw new ApiError(409, error.message);
        }
        if (error instanceof PairError) throw new ApiError(502, error.message);
        throw error;
      }
    },

    "POST /api/forget": async () => {
      await state.forgetTv();
      return { status: "ok" };
    },

    // --- ajustes -------------------------------------------------------

    "GET /api/settings": async () => ({
      tv_host: state.session.host,
      tv_port: state.session.port,
      tv_mac: state.tvMac ?? "",
      // Nunca devolvemos o token: a tela só precisa saber se existe um.
      has_token: state.tmdb !== null,
    }),

    "POST /api/settings": async (body) => {
      if ("tmdb_token" in body) await state.setToken(String(body["tmdb_token"] ?? ""));
      if ("tv_mac" in body) await state.setMac(String(body["tv_mac"] ?? ""));
      return { status: "ok" };
    },

    // --- presets -------------------------------------------------------

    "GET /api/presets": async (_body, _params, query) => {
      const serviceId = query.get("service_id");
      const presets = serviceId
        ? state.presets.forService(serviceId)
        : state.presets.allPresets();
      return { presets: presets.map(serializePreset) };
    },

    "POST /api/presets/run": async (_body, params) => {
      const [serviceId, presetId] = params;
      const preset = state.presets.get(String(serviceId), String(presetId));
      if (!preset) throw new ApiError(404, "Preset não encontrado");

      const started = state.runner.start(preset.label, () =>
        runSteps(state.session, preset.steps),
      );
      if (!started) {
        throw new ApiError(409, "Já tem uma ação rodando na TV. Espere terminar.");
      }
      return { status: "started", label: preset.label };
    },

    "DELETE /api/presets": async (_body, params) => {
      const [serviceId, presetId] = params;
      if (!state.presets.remove(String(serviceId), String(presetId))) {
        throw new ApiError(404, "Preset não encontrado");
      }
      await state.savePresets();
      return { status: "deleted" };
    },
  };

  /**
   * Resolve o caminho numa rota e nos pedaços variáveis.
   *
   * Feito à mão porque são nove rotas com no máximo dois parâmetros — uma biblioteca
   * de roteamento aqui seria mais código para ler do que o `switch` que ela evita.
   */
  function resolve(method: string, pathname: string): { key: string; params: string[] } {
    const parts = pathname.split("/").filter(Boolean); // ["api", "presets", "max", "dublado"]

    if (parts[0] === "api" && parts[1] === "seasons" && parts.length === 3) {
      return { key: "GET /api/seasons", params: [parts[2] as string] };
    }
    if (parts[0] === "api" && parts[1] === "episodes" && parts.length === 4) {
      return { key: "GET /api/episodes", params: [parts[2] as string, parts[3] as string] };
    }
    if (parts[0] === "api" && parts[1] === "presets" && parts.length === 5 && parts[4] === "run") {
      return { key: "POST /api/presets/run", params: [parts[2] as string, parts[3] as string] };
    }
    if (parts[0] === "api" && parts[1] === "presets" && parts.length === 4) {
      return { key: "DELETE /api/presets", params: [parts[2] as string, parts[3] as string] };
    }
    return { key: `${method} /${parts.join("/")}`, params: [] };
  }

  /** Mesma assinatura do `api()` que o `app.js` já usava. */
  return async function handle(path: string, options: RequestOptions = {}): Promise<unknown> {
    const method = (options.method ?? "GET").toUpperCase();
    const url = new URL(path, "http://app.local");
    const { key, params } = resolve(method, url.pathname);

    const route = routes[key];
    if (!route) throw new ApiError(404, `Rota desconhecida: ${method} ${url.pathname}`);

    const body = options.body ? (JSON.parse(options.body) as Record<string, unknown>) : {};
    return route(body, params, url.searchParams);
  };
}

export type Router = ReturnType<typeof createRouter>;
