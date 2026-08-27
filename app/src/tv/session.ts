/**
 * Conexão SSAP persistente com a TV.
 *
 * Um WebSocket aberto e reutilizado: reconectar a cada toque deixaria o direcional
 * lento demais para navegar. Comandos são serializados por uma fila — o socket de
 * input do webOS embaralha as respostas se dois comandos saem juntos.
 */

import { normalize } from "./buttons.ts";
import { SsapClient, SSAP_SECURE_PORT, type SsapClientOptions } from "./client.ts";
import * as endpoints from "./endpoints.ts";
import { TvPairError, TvUnreachableError } from "./errors.ts";
import type { MacroTarget } from "./macros.ts";

export { TvError, TvPairError, TvUnreachableError } from "./errors.ts";

/**
 * Uma varredura /24 custa segundos e a tela pergunta o estado a cada 5. Sem esta
 * folga, uma TV simplesmente desligada viraria uma varredura atrás da outra.
 */
export const REDISCOVER_COOLDOWN = 120;

export type ClientFactory = (
  host: string,
  port: number,
  clientKey: string | null,
) => SsapClient;

/** Devolve o IP novo da TV, ou null se não achou. */
export type Rediscover = () => Promise<string | null>;

export interface TvSessionOptions {
  port?: number;
  clientFactory?: ClientFactory;
  onClientKey?: (key: string) => void;
  rediscover?: Rediscover;
  onHostChange?: (host: string) => void;
  /** Relógio monotônico em segundos, injetável para o teste. */
  now?: () => number;
  clientOptions?: SsapClientOptions;
}

/** Dona da conexão com a TV. Uma instância por app. */
export class TvSession implements MacroTarget {
  host: string;
  clientKey: string | null;
  port: number;

  private readonly factory: ClientFactory;
  private readonly onClientKey: ((key: string) => void) | undefined;
  private readonly rediscoverFn: Rediscover | undefined;
  private readonly onHostChange: ((host: string) => void) | undefined;
  private readonly now: () => number;
  private lastRediscover = 0;
  private rediscovering = false;
  private client: SsapClient | null = null;
  private tail: Promise<void> = Promise.resolve();

  constructor(host: string, clientKey: string | null = null, options: TvSessionOptions = {}) {
    this.host = host;
    this.clientKey = clientKey;
    this.port = options.port ?? SSAP_SECURE_PORT;
    this.onClientKey = options.onClientKey;
    this.rediscoverFn = options.rediscover;
    this.onHostChange = options.onHostChange;
    this.now = options.now ?? (() => Date.now() / 1000);
    this.factory =
      options.clientFactory ??
      ((host, port, key) => new SsapClient(host, port, key, options.clientOptions ?? {}));
  }

  // --- ciclo de vida ---------------------------------------------------

  /** Garante uma conexão viva e devolve o client. Idempotente. */
  async connect(): Promise<SsapClient> {
    if (this.client?.connected) return this.client;

    try {
      return await this.open();
    } catch (error) {
      // O IP pode ter mudado no DHCP. Se conseguirmos reencontrar a TV pela
      // identidade, o usuário nem fica sabendo que algo aconteceu.
      if (!(error instanceof TvUnreachableError)) throw error;
      if ((await this.relocate()) === null) throw error;
      return await this.open();
    }
  }

  private async open(): Promise<SsapClient> {
    const client = this.factory(this.host, this.port, this.clientKey);
    try {
      await client.connect();
    } catch (error) {
      this.client = null;
      throw error;
    }

    // No primeiro pareamento a TV devolve a chave; guardamos para não pedir de novo.
    if (client.clientKey && client.clientKey !== this.clientKey) {
      this.clientKey = client.clientKey;
      this.onClientKey?.(client.clientKey);
    }

    this.client = client;
    return client;
  }

  /** Procura a TV na rede e adota o IP novo, respeitando o intervalo mínimo. */
  private async relocate(): Promise<string | null> {
    if (!this.rediscoverFn || this.rediscovering) return null;
    if (this.now() - this.lastRediscover < REDISCOVER_COOLDOWN) return null;

    this.rediscovering = true;
    let found: string | null = null;
    try {
      found = await this.rediscoverFn();
    } catch {
      // Procurar a TV é socorro, não comando: se a busca quebrar, quem tem de chegar
      // ao usuário é o erro original ("não falei com a TV"), não um erro secundário.
      return null;
    } finally {
      this.lastRediscover = this.now();
      this.rediscovering = false;
    }

    if (!found || found === this.host) return null;

    this.client = null;
    this.host = found;
    this.onHostChange?.(found);
    return found;
  }

  async close(): Promise<void> {
    const client = this.client;
    this.client = null;
    await client?.disconnect();
  }

  get connected(): boolean {
    return this.client?.connected === true;
  }

  // --- execução --------------------------------------------------------

  /**
   * Executa um comando na fila, com uma única tentativa de reconexão.
   *
   * Uma reconexão só: se a segunda também falhar, o problema é a TV, e insistir só
   * faz a tela travar esperando.
   */
  private call<T>(action: (client: SsapClient) => Promise<T>): Promise<T> {
    const result = this.tail.then(async () => {
      let client = await this.connect();
      try {
        return await action(client);
      } catch (error) {
        if (error instanceof TvPairError) throw error;
        this.client = null;
        client = await this.connect();
        return await action(client);
      }
    });
    this.tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  // --- controle --------------------------------------------------------

  async button(name: string): Promise<void> {
    const valid = normalize(name);
    await this.call((client) => client.button(valid));
  }

  async click(): Promise<void> {
    await this.call((client) => client.click());
  }

  async move(dX: number, dY: number): Promise<void> {
    await this.call((client) => client.move(dX, dY));
  }

  /**
   * Digita no campo focado da TV usando o IME (o teclado nativo).
   *
   * É isto que faz a busca dentro do Max/Netflix acontecer sem direcional.
   */
  async insertText(text: string, replace = false): Promise<void> {
    await this.call((client) =>
      client.request(endpoints.INSERT_TEXT, { text, replace: replace ? 1 : 0 }),
    );
  }

  async sendEnter(): Promise<void> {
    await this.call((client) => client.request(endpoints.SEND_ENTER));
  }

  async deleteCharacters(count: number): Promise<void> {
    await this.call((client) => client.request(endpoints.SEND_DELETE, { count }));
  }

  async setVolume(volume: number): Promise<void> {
    await this.call((client) =>
      client.request(endpoints.SET_VOLUME, { volume: Math.max(0, volume) }),
    );
  }

  async getVolume(): Promise<number | null> {
    const payload = await this.call((client) => client.request(endpoints.GET_VOLUME));
    // Firmware novo embrulha o volume em `volumeStatus`; o antigo devolve solto.
    const status = (payload["volumeStatus"] ?? payload) as Record<string, unknown>;
    const volume = status["volume"];
    return typeof volume === "number" ? volume : null;
  }

  async setMute(mute: boolean): Promise<void> {
    await this.call((client) => client.request(endpoints.SET_MUTE, { mute }));
  }

  /**
   * Desliga a TV.
   *
   * `command` e não `request` porque a TV desligando não devolve resposta — esperar
   * por uma travaria até estourar o prazo.
   */
  async powerOff(): Promise<void> {
    await this.call(async (client) => {
      client.command("request", endpoints.POWER_OFF);
    });
  }

  /** Mostra um aviso flutuante na TV. Útil para confirmar que o comando chegou. */
  async toast(message: string): Promise<void> {
    await this.call((client) =>
      client.request(endpoints.SHOW_MESSAGE, {
        message,
        iconData: "",
        iconExtension: "",
      }),
    );
  }

  // --- apps ------------------------------------------------------------

  async listApps(): Promise<Record<string, unknown>[]> {
    const payload = await this.call((client) => client.request(endpoints.GET_APPS));
    const apps = payload["launchPoints"];
    return Array.isArray(apps) ? (apps as Record<string, unknown>[]) : [];
  }

  async currentApp(): Promise<string | null> {
    const payload = await this.call((client) =>
      client.request(endpoints.GET_CURRENT_APP_INFO),
    );
    const appId = payload["appId"];
    return typeof appId === "string" && appId ? appId : null;
  }

  async launch(appId: string): Promise<Record<string, unknown>> {
    return this.call((client) => client.request(endpoints.LAUNCH, { id: appId }));
  }

  /** Fecha o app. Serve para forçar uma abertura fria ao calibrar a tela de perfil. */
  async closeApp(appId: string): Promise<Record<string, unknown>> {
    return this.call((client) => client.request(endpoints.LAUNCHER_CLOSE, { id: appId }));
  }

  async launchWithParams(
    appId: string,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.call((client) => client.request(endpoints.LAUNCH, { id: appId, params }));
  }

  async launchWithContentId(
    appId: string,
    contentId: string,
  ): Promise<Record<string, unknown>> {
    return this.call((client) =>
      client.request(endpoints.LAUNCH, { id: appId, contentId }),
    );
  }
}
