/** Catálogo de serviços: liga provedor do TMDb -> app da TV -> macro de busca. */

import { parse as parseYaml } from "yaml";

import { parseSteps, stepToYaml, type Step } from "./macros.ts";

export class ServiceConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ServiceConfigError";
  }
}

export class MacroWriteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MacroWriteError";
  }
}

/** Um serviço de streaming e como abri-lo nesta TV. */
export interface Service {
  id: string;
  label: string;
  appId: string;
  tmdbNames: string[];
  match: string[];
  waitAfterLaunch: number;
  contentTarget: string | null;
  search: Step[];
  /**
   * Tela "Quem está assistindo?". Ter esta macro preenchida é o que faz o app ser
   * SEMPRE fechado antes de abrir: é a única forma de garantir em que tela ele está.
   * Deduzir isso do app em primeiro plano não funciona — parado no próprio seletor de
   * perfis o app já é o de sempre, e o tratamento era desligado justo quando precisava.
   */
  profile: Step[];
  /** Contado a partir do momento em que o app aparece em primeiro plano, não do launch. */
  waitBeforeProfile: number;
  /** Pausa entre fechar e reabrir: o webOS precisa de um instante para soltar o app. */
  closeSettle: number;
  /** Teto para esperar o app chegar em primeiro plano antes de cair no tempo fixo. */
  foregroundTimeout: number;
  /** Da página da série até tocar um episódio. Usa {episode_index} para o deslocamento. */
  episode: Step[];
  /** Tempo para a página da série carregar depois que a busca a abre. */
  waitBeforeEpisode: number;
}

export function canSearch(service: Service): boolean {
  return service.search.length > 0;
}

export function canPickEpisode(service: Service): boolean {
  return service.episode.length > 0;
}

export class ServiceCatalog {
  constructor(
    readonly services: Service[] = [],
    readonly shortcuts: string[] = [],
  ) {}

  byId(serviceId: string): Service | null {
    return this.services.find((s) => s.id === serviceId) ?? null;
  }

  byAppId(appId: string): Service | null {
    return this.services.find((s) => s.appId === appId) ?? null;
  }

  /**
   * Casa o nome que o TMDb devolve com um serviço.
   *
   * O TMDb varia o rótulo ('Max', 'Max Amazon Channel'), então cai para comparação
   * por termo antes de desistir.
   */
  byTmdbProvider(providerName: string): Service | null {
    const wanted = providerName.trim().toLowerCase();
    for (const service of this.services) {
      if (service.tmdbNames.some((name) => name.toLowerCase() === wanted)) return service;
    }
    for (const service of this.services) {
      if (service.match.some((term) => wanted.includes(term.toLowerCase()))) return service;
    }
    return null;
  }

  shortcutServices(): Service[] {
    return this.shortcuts
      .map((sid) => this.byId(sid))
      .filter((service): service is Service => service !== null);
  }
}

function asList(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.map((item) => String(item));
  return [];
}

function asNumber(value: unknown, fallback: number): number {
  if (value === null || value === undefined) return fallback;
  const number = Number(value);
  return Number.isNaN(number) ? fallback : number;
}

/**
 * Espera até a tela de perfil, nunca menor que o tempo de carga do app.
 *
 * A tela de perfil aparece DEPOIS de o app carregar. Configurar menos que isso manda o
 * ENTER num app que ainda está subindo — e como botão no webOS vai sem confirmação, o
 * toque some em silêncio e a macro de busca continua rodando em cima do seletor de
 * perfis. Era exatamente o caso do Max (8s de perfil contra 12s de carga).
 */
function profileWait(raw: Record<string, unknown>, waitAfterLaunch: number): number {
  const configured = asNumber(raw["wait_before_profile"], 5);
  const profile = raw["profile"];
  const hasProfile = Array.isArray(profile) && profile.length > 0;
  if (!hasProfile || configured >= waitAfterLaunch) return configured;
  return waitAfterLaunch;
}

export function parseCatalog(data: Record<string, unknown>): ServiceCatalog {
  const rawServices = data["services"] ?? [];
  if (!Array.isArray(rawServices)) {
    throw new ServiceConfigError("'services' precisa ser uma lista");
  }

  const services: Service[] = [];
  for (const entry of rawServices) {
    if (typeof entry !== "object" || entry === null || !("id" in entry)) {
      throw new ServiceConfigError(`serviço sem 'id': ${JSON.stringify(entry)}`);
    }
    const raw = entry as Record<string, unknown>;
    const serviceId = String(raw["id"]);
    const appId = String(raw["app_id"] ?? "").trim();
    if (!appId) {
      throw new ServiceConfigError(
        `serviço "${serviceId}" sem app_id — use "Procurar apps na TV" nos ajustes`,
      );
    }
    const waitAfterLaunch = asNumber(raw["wait_after_launch"], 8);
    services.push({
      id: serviceId,
      label: String(raw["label"] ?? serviceId),
      appId,
      tmdbNames: asList(raw["tmdb_names"]),
      match: asList(raw["match"]),
      waitAfterLaunch,
      contentTarget: (raw["content_target"] as string | undefined) || null,
      search: parseSteps(raw["search"] as Record<string, unknown>[] | undefined),
      profile: parseSteps(raw["profile"] as Record<string, unknown>[] | undefined),
      waitBeforeProfile: profileWait(raw, waitAfterLaunch),
      closeSettle: asNumber(raw["close_settle"], 1.5),
      foregroundTimeout: asNumber(raw["foreground_timeout"], 20),
      episode: parseSteps(raw["episode"] as Record<string, unknown>[] | undefined),
      waitBeforeEpisode: asNumber(raw["wait_before_episode"], 4),
    });
  }

  const known = new Set(services.map((s) => s.id));
  const shortcuts = asList(data["shortcuts"]).filter((sid) => known.has(sid));
  return new ServiceCatalog(services, shortcuts.length ? shortcuts : services.map((s) => s.id));
}

export function loadCatalog(yamlText: string): ServiceCatalog {
  const data = (parseYaml(yamlText) ?? {}) as Record<string, unknown>;
  return parseCatalog(data);
}

// --- descoberta contra a TV real -----------------------------------------

export interface Discovery {
  serviceId: string;
  appId: string;
  appTitle: string;
  changed: boolean;
}

export interface InstalledApp {
  id?: string;
  title?: string;
  [key: string]: unknown;
}

/**
 * Confere cada serviço contra os apps instalados na TV.
 *
 * IDs de app mudam por região e por ano do modelo, então ler da TV é sempre mais
 * confiável que qualquer lista publicada na internet.
 */
export function matchInstalledApps(
  catalog: ServiceCatalog,
  installed: InstalledApp[],
): { found: Discovery[]; missing: Service[] } {
  const byId = new Map(installed.map((app) => [String(app.id ?? ""), app]));
  const found: Discovery[] = [];
  const missing: Service[] = [];

  for (const service of catalog.services) {
    const exact = byId.get(service.appId);
    if (exact) {
      found.push({
        serviceId: service.id,
        appId: service.appId,
        appTitle: String(exact.title ?? ""),
        changed: false,
      });
      continue;
    }

    const candidate = findByTitle(service, installed);
    if (!candidate) {
      missing.push(service);
      continue;
    }

    found.push({
      serviceId: service.id,
      appId: String(candidate.id ?? ""),
      appTitle: String(candidate.title ?? ""),
      changed: true,
    });
  }

  return { found, missing };
}

function findByTitle(service: Service, installed: InstalledApp[]): InstalledApp | null {
  const terms = (service.match.length ? service.match : [service.label]).map((t) =>
    t.toLowerCase(),
  );
  return (
    installed.find((app) => {
      const title = String(app.title ?? "").toLowerCase();
      return terms.some((term) => title.includes(term));
    }) ?? null
  );
}

// --- edição cirúrgica do apps.yaml ---------------------------------------
//
// Um dump de YAML devolveria o arquivo sem nenhum dos comentários que explicam como
// calibrar as macros — e são eles que tornam o arquivo editável à mão. Por isso a
// edição é por linha: só as linhas da chave mudam, o resto fica intacto.

const SERVICE_LINE = /^\s*-\s+id:\s*(\S+)\s*$/;
const APP_ID_LINE = /^(\s*)app_id:\s*.*$/;
const KEY_LINE = /^(\s*)([A-Za-z_][\w-]*):\s*(.*)$/;
const ITEM_LINE = /^\s*-\s/;

export const MACRO_KEYS = ["profile", "search", "episode"] as const;
export type MacroKey = (typeof MACRO_KEYS)[number];

/** Reescreve só as linhas `app_id:` que mudaram, preservando comentários. */
export function applyDiscoveries(
  yamlText: string,
  discoveries: Discovery[],
): { text: string; applied: string[] } {
  const updates = new Map(
    discoveries.filter((d) => d.changed).map((d) => [d.serviceId, d.appId]),
  );
  if (updates.size === 0) return { text: yamlText, applied: [] };

  const lines = yamlText.split("\n");
  let current: string | null = null;
  const applied: string[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] as string;
    const serviceMatch = SERVICE_LINE.exec(line);
    if (serviceMatch) {
      current = serviceMatch[1] as string;
      continue;
    }
    const appIdMatch = current !== null && updates.has(current) ? APP_ID_LINE.exec(line) : null;
    if (current !== null && appIdMatch) {
      const appId = updates.get(current) as string;
      lines[index] = `${appIdMatch[1]}app_id: ${appId}`;
      applied.push(`${current} -> ${appId}`);
      current = null;
    }
  }

  return { text: lines.join("\n"), applied };
}

/** Onde começa e termina o bloco de um serviço. Fim é exclusivo. */
function serviceBounds(lines: string[], serviceId: string): [number, number] {
  let start = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const match = SERVICE_LINE.exec(lines[index] as string);
    if (match && match[1] === serviceId) {
      start = index;
      break;
    }
  }
  if (start < 0) {
    throw new MacroWriteError(`Serviço "${serviceId}" não existe no apps.yaml.`);
  }

  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index] as string;
    if (SERVICE_LINE.test(line) || (ITEM_LINE.test(line) && !line.startsWith(" ".repeat(6)))) {
      return [start, index];
    }
  }
  return [start, lines.length];
}

/**
 * Onde está a chave dentro do serviço, e com que indentação. Fim é exclusivo.
 *
 * Uma lista pode estar em bloco (`search:` seguido de itens `- {...}`) ou em linha
 * (`episode: []`). Os dois têm de ser reconhecidos, senão regravar um serviço que
 * nunca foi calibrado deixaria o `[]` antigo logo acima da lista nova.
 */
function keyBounds(
  lines: string[],
  start: number,
  end: number,
  key: string,
): [number, number, string] {
  for (let index = start + 1; index < end; index += 1) {
    const match = KEY_LINE.exec(lines[index] as string);
    if (!match || match[2] !== key) continue;

    const indent = match[1] as string;
    let stop = index + 1;
    // Só as linhas MAIS indentadas que a chave pertencem a ela: parar na próxima
    // chave do mesmo nível é o que impede engolir o resto do serviço.
    while (stop < end) {
      const line = lines[stop] as string;
      const belongs = !line.trim() || line.startsWith(indent + " ") || ITEM_LINE.test(line);
      if (!belongs) break;
      if (line.trim() && !line.startsWith(indent + " ")) break;
      stop += 1;
    }
    return [index, stop, indent];
  }

  throw new MacroWriteError(`Chave "${key}" não existe no serviço — acrescente-a no apps.yaml.`);
}

/**
 * Grava uma macro gravada na TV dentro do apps.yaml, sem perder os comentários.
 *
 * Devolve o texto novo. Quem chama guarda o resultado; a versão que veio embutida no
 * app continua sendo o caminho de volta se a gravação sair torta.
 */
export function writeMacro(
  yamlText: string,
  serviceId: string,
  key: string,
  steps: readonly Step[],
): string {
  if (!(MACRO_KEYS as readonly string[]).includes(key)) {
    throw new MacroWriteError(`Só sei gravar ${MACRO_KEYS.join(", ")} — recebi "${key}".`);
  }
  if (steps.length === 0) {
    throw new MacroWriteError("Gravação sem nenhum passo.");
  }

  const lines = yamlText.split("\n");
  const [start, end] = serviceBounds(lines, serviceId);
  const [keyStart, keyEnd, indent] = keyBounds(lines, start, end, key);

  const block = [`${indent}${key}:`, ...steps.map((step) => `${indent}  - ${stepToYaml(step)}`)];
  const rewritten = [...lines.slice(0, keyStart), ...block, ...lines.slice(keyEnd)];
  const text = rewritten.join("\n");

  // Reler é o que garante que não gravamos YAML inválido por cima do catálogo.
  loadCatalog(text);
  return text;
}
