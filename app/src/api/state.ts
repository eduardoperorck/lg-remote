/**
 * Tudo que vive enquanto o app está aberto.
 *
 * É o equivalente do `AppState` do servidor Python, com uma diferença: aqui não há
 * `.env` nem arquivos. O catálogo vem embutido no app (com uma cópia editável no
 * armazenamento, para o gravador poder escrever macros), e a chave da TV vem do
 * Keychain.
 */

import { TmdbClient, type Fetcher } from "../catalog/tmdb.ts";
import { ServiceCatalog, loadCatalog } from "../tv/apps.ts";
import { SSAP_SECURE_PORT } from "../tv/client.ts";
import { TvError } from "../tv/errors.ts";
import type { Sleeper } from "../tv/macros.ts";
import { TitleOpener } from "../tv/opener.ts";
import { PresetStore, parseStore, storeToData } from "../tv/presets.ts";
import { Recorder } from "../tv/recorder.ts";
import { TvSession } from "../tv/session.ts";
import type { Transport } from "../tv/transport.ts";
import { KEYS, type Store } from "../storage/store.ts";
import { ActionRunner } from "./runner.ts";

export interface AppStateOptions {
  store: Store;
  /** O apps.yaml que veio embutido no app — a versão de fábrica. */
  bundledCatalog: string;
  transport?: Transport;
  /** Procura a TV na rede. No iOS é o plugin; no teste, uma função qualquer. */
  rediscover?: () => Promise<string | null>;
  /** Liga a TV. No iOS é o Wake-on-LAN do plugin. */
  wake?: (mac: string) => Promise<void>;
  /** Varre a /24 atrás da TV. No iOS é o plugin; no navegador, indisponível. */
  scan?: (ports?: number[]) => Promise<{ host: string; port: number }[]>;
  tmdbFetcher?: Fetcher;
  sleep?: Sleeper;
}

export class AppState {
  catalog: ServiceCatalog;
  readonly session: TvSession;
  readonly runner = new ActionRunner();
  readonly opener: TitleOpener;
  readonly recorder = new Recorder();
  presets: PresetStore;
  tmdb: TmdbClient | null;
  tvMac: string | null;
  /** Como abrir sockets. O pareamento precisa dela antes de existir uma sessão. */
  readonly transport: Transport | undefined;
  readonly scan: ((ports?: number[]) => Promise<{ host: string; port: number }[]>) | undefined;
  /** O mesmo relógio que as macros usam — os testes o substituem por um instantâneo. */
  readonly sleep: Sleeper;

  private constructor(
    private readonly store: Store,
    private readonly bundledCatalog: string,
    private readonly wakeFn: ((mac: string) => Promise<void>) | undefined,
    private readonly tmdbOptions: { fetcher?: Fetcher },
    transport: Transport | undefined,
    scan: ((ports?: number[]) => Promise<{ host: string; port: number }[]>) | undefined,
    catalog: ServiceCatalog,
    session: TvSession,
    presets: PresetStore,
    tmdb: TmdbClient | null,
    tvMac: string | null,
    sleep: Sleeper | undefined,
  ) {
    this.catalog = catalog;
    this.session = session;
    this.presets = presets;
    this.tmdb = tmdb;
    this.tvMac = tvMac;
    this.transport = transport;
    this.scan = scan;
    this.sleep = sleep ?? ((seconds) => new Promise((resolve) => setTimeout(resolve, seconds * 1000)));
    // O `onStep` é o que faz a tela mostrar em que ponto a abertura está. Sem ele, a
    // UI só recebia o resultado no fim — e uma falha no meio dos ~25s era
    // indistinguível de nada ter acontecido.
    this.opener = new TitleOpener(this.session, {
      onStep: this.runner.note,
      ...(sleep ? { sleep } : {}),
    });
  }

  static async create(options: AppStateOptions): Promise<AppState> {
    const { store, bundledCatalog } = options;

    // A cópia editável tem precedência: é onde o gravador escreve as macros novas.
    const override = await store.get(KEYS.catalogOverride);
    let catalog: ServiceCatalog;
    try {
      catalog = loadCatalog(override ?? bundledCatalog);
    } catch {
      // Override quebrado não pode deixar o app sem catálogo — a versão de fábrica
      // sempre carrega, porque foi ela que passou nos testes.
      catalog = loadCatalog(bundledCatalog);
    }

    const host = (await store.get(KEYS.tvHost)) ?? "";
    const port = Number(await store.get(KEYS.tvPort)) || SSAP_SECURE_PORT;
    const clientKey = await store.getClientKey();

    const session = new TvSession(host, clientKey, {
      port,
      onClientKey: (key) => void store.setClientKey(key),
      onHostChange: (newHost) => void store.set(KEYS.tvHost, newHost),
      ...(options.transport ? { clientOptions: { transport: options.transport } } : {}),
      ...(options.rediscover ? { rediscover: options.rediscover } : {}),
    });

    const presetsRaw = await store.get(KEYS.presets);
    const presets = parseStore(presetsRaw ? (JSON.parse(presetsRaw) as Record<string, unknown>) : null);

    const token = await store.get(KEYS.tmdbToken);
    const tmdb = token
      ? new TmdbClient(token, options.tmdbFetcher ? { fetcher: options.tmdbFetcher } : {})
      : null;

    return new AppState(
      store,
      bundledCatalog,
      options.wake,
      options.tmdbFetcher ? { fetcher: options.tmdbFetcher } : {},
      options.transport,
      options.scan,
      catalog,
      session,
      presets,
      tmdb,
      await store.get(KEYS.tvMac),
      options.sleep,
    );
  }

  get paired(): boolean {
    return Boolean(this.session.host && this.session.clientKey);
  }

  get canWake(): boolean {
    return Boolean(this.tvMac && this.wakeFn);
  }

  async wake(): Promise<void> {
    if (!this.tvMac) {
      throw new Error("Sem o MAC da TV não dá para ligá-la. Pareie de novo para descobri-lo.");
    }
    if (!this.wakeFn) {
      throw new Error("Ligar a TV precisa do app nativo.");
    }
    await this.wakeFn(this.tvMac);
  }

  /** Qual serviço está em foco na TV agora — define o contexto do gravador. */
  async currentServiceId(): Promise<string | null> {
    let appId: string | null;
    try {
      appId = await this.session.currentApp();
    } catch (error) {
      if (error instanceof TvError) return null;
      throw error;
    }
    return this.catalog.byAppId(appId ?? "")?.id ?? null;
  }

  /**
   * Adota a TV recém-pareada.
   *
   * A chave vai para o Keychain e o endereço para as Preferences — é a divisão que
   * faz o pareamento sobreviver à re-assinatura de 7 dias do SideStore.
   */
  async adoptTv(host: string, port: number, clientKey: string): Promise<void> {
    await this.store.set(KEYS.tvHost, host);
    await this.store.set(KEYS.tvPort, String(port));
    await this.store.setClientKey(clientKey);
    await this.session.close();
    this.session.host = host;
    this.session.port = port;
    this.session.clientKey = clientKey;
  }

  /** Esquece esta TV. A chave sai do Keychain — o próximo uso pede pareamento novo. */
  async forgetTv(): Promise<void> {
    await this.session.close();
    await this.store.forgetClientKey();
    await this.store.remove(KEYS.tvHost);
    this.session.clientKey = null;
  }

  async setToken(token: string): Promise<void> {
    const trimmed = token.trim();
    if (!trimmed) {
      await this.store.remove(KEYS.tmdbToken);
      this.tmdb = null;
      return;
    }
    await this.store.set(KEYS.tmdbToken, trimmed);
    this.tmdb = new TmdbClient(trimmed, this.tmdbOptions);
  }

  async setMac(mac: string): Promise<void> {
    const trimmed = mac.trim();
    this.tvMac = trimmed || null;
    if (trimmed) {
      await this.store.set(KEYS.tvMac, trimmed);
    } else {
      await this.store.remove(KEYS.tvMac);
    }
  }

  async savePresets(): Promise<void> {
    await this.store.set(KEYS.presets, JSON.stringify(storeToData(this.presets)));
  }

  /**
   * Grava o catálogo editado e recarrega na hora.
   *
   * Sem recarregar, a macro nova só valeria no próximo boot do app — e você testaria
   * a antiga achando que testou a que gravou.
   */
  async saveCatalog(yamlText: string): Promise<void> {
    this.catalog = loadCatalog(yamlText);
    await this.store.set(KEYS.catalogOverride, yamlText);
  }

  /** O texto do catálogo em vigor, que é o que o gravador edita. */
  async catalogText(): Promise<string> {
    return (await this.store.get(KEYS.catalogOverride)) ?? this.bundledCatalog;
  }

  async close(): Promise<void> {
    await this.session.close();
  }
}
