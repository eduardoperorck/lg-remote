/**
 * Cliente SSAP: o que a `aiowebostv` fazia pelo projeto Python.
 *
 * São dois sockets, não um. O principal leva requisições JSON com id e resposta; os
 * botões do direcional viajam por um segundo socket, de texto, cujo endereço a própria
 * TV informa via `com.webos.service.networkinput/getPointerInputSocket`. Mandar botão
 * pelo socket principal simplesmente não funciona.
 */

import * as endpoints from "./endpoints.ts";
import { registrationMessage } from "./handshake.ts";
import { TvPairError, TvUnreachableError } from "./errors.ts";
import type { SsapSocket, Transport, TransportHandlers } from "./transport.ts";
import { WebSocketTransport } from "./transport.ts";

/** Mesmos prazos da aiowebostv — foram calibrados contra TVs reais. */
export const CONNECT_TIMEOUT = 2_000;
export const RECEIVE_TIMEOUT = 10_000;
export const REQUEST_TIMEOUT = 20_000;

export const SSAP_SECURE_PORT = 3001;
export const SSAP_LEGACY_PORT = 3000;

export type SsapMessage = {
  id?: string | number;
  type?: string;
  uri?: string;
  error?: string;
  payload?: Record<string, unknown>;
};

export function ssapUrl(host: string, port: number): string {
  const scheme = port === SSAP_SECURE_PORT ? "wss" : "ws";
  return `${scheme}://${host}:${port}`;
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: Error) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Fila de mensagens para o handshake.
 *
 * O registro é uma conversa em ordem — manda, lê, manda, lê — enquanto o resto da
 * sessão é assíncrono e roteado por id. Esta fila cobre a primeira fase; depois dela
 * as mensagens passam a ser despachadas pelos ids.
 */
class Inbox {
  private readonly queue: SsapMessage[] = [];
  private waiter: Deferred<SsapMessage> | null = null;
  private failure: Error | null = null;

  push(message: SsapMessage): void {
    if (this.waiter) {
      const waiter = this.waiter;
      this.waiter = null;
      waiter.resolve(message);
      return;
    }
    this.queue.push(message);
  }

  fail(error: Error): void {
    this.failure = error;
    if (this.waiter) {
      const waiter = this.waiter;
      this.waiter = null;
      waiter.reject(error);
    }
  }

  async next(timeoutMs: number, what: string): Promise<SsapMessage> {
    const queued = this.queue.shift();
    if (queued) return queued;
    if (this.failure) throw this.failure;

    const waiter = deferred<SsapMessage>();
    this.waiter = waiter;
    const timer = setTimeout(() => {
      if (this.waiter === waiter) {
        this.waiter = null;
        waiter.reject(new TvUnreachableError(`A TV não respondeu ${what} em ${timeoutMs}ms.`));
      }
    }, timeoutMs);
    try {
      return await waiter.promise;
    } finally {
      clearTimeout(timer);
    }
  }
}

export interface SsapClientOptions {
  transport?: Transport;
  /** Prazo de espera por resposta de requisição. Injetável para os testes. */
  requestTimeout?: number;
}

export class SsapClient {
  clientKey: string | null;
  readonly host: string;
  readonly port: number;

  private readonly transport: Transport;
  private readonly requestTimeout: number;
  private socket: SsapSocket | null = null;
  private inputSocket: SsapSocket | null = null;
  private readonly futures = new Map<string, Deferred<SsapMessage>>();
  private inbox: Inbox | null = null;
  private commandCount = 0;
  private closeReason: string | null = null;

  constructor(
    host: string,
    port: number = SSAP_SECURE_PORT,
    clientKey: string | null = null,
    options: SsapClientOptions = {},
  ) {
    this.host = host;
    this.port = port;
    this.clientKey = clientKey;
    this.transport = options.transport ?? new WebSocketTransport(CONNECT_TIMEOUT);
    this.requestTimeout = options.requestTimeout ?? REQUEST_TIMEOUT;
  }

  get connected(): boolean {
    return this.socket !== null;
  }

  // --- ciclo de vida ---------------------------------------------------

  /** Abre o socket, registra e deixa a sessão pronta. Guarda a chave nova, se houver. */
  async connect(): Promise<void> {
    if (this.socket) return;

    const inbox = new Inbox();
    this.inbox = inbox;
    this.closeReason = null;

    const handlers: TransportHandlers = {
      onMessage: (raw) => this.receive(raw),
      onClose: (reason) => this.handleClose(reason),
    };

    let socket: SsapSocket;
    try {
      socket = await this.transport.connect(ssapUrl(this.host, this.port), handlers);
    } catch (cause) {
      this.inbox = null;
      throw new TvUnreachableError(
        `Não consegui falar com a TV em ${this.host}. ` +
          "Confira se ela está ligada, na mesma rede, e se o IP ainda é esse.",
      );
    }
    this.socket = socket;

    try {
      // Firmware novo exige o getSystemInfo ANTES do registro. Não é enfeite: sem
      // ele a TV recusa o register sem dizer por quê.
      socket.send(
        JSON.stringify({
          id: "get_sys_info",
          type: "request",
          uri: `ssap://${endpoints.GET_SYSTEM_INFO}`,
          payload: {},
        }),
      );
      await inbox.next(RECEIVE_TIMEOUT, "o getSystemInfo");

      socket.send(JSON.stringify(registrationMessage(this.clientKey)));
      let response = await inbox.next(RECEIVE_TIMEOUT, "o registro");

      // `pairingType: PROMPT` significa que a TV pôs o pedido na tela e a decisão
      // ainda vai chegar numa segunda mensagem.
      if (response.type === "response" && response.payload?.["pairingType"] === "PROMPT") {
        response = await inbox.next(RECEIVE_TIMEOUT, "a resposta do pareamento");
      }

      if (response.type === "error") {
        throw new TvPairError(
          this.clientKey
            ? "A TV recusou a chave salva — ela vale até alguém apagar este aparelho " +
              "da lista de dispositivos da TV. Pareie de novo."
            : "A TV recusou a autorização.",
        );
      }

      if (response.type === "registered") {
        const key = response.payload?.["client-key"];
        if (typeof key === "string" && key) {
          this.clientKey = key;
        }
      }

      if (!this.clientKey) {
        throw new TvPairError("A TV não devolveu chave nenhuma — o pareamento falhou.");
      }
    } catch (error) {
      await this.disconnect();
      throw error;
    } finally {
      this.inbox = null;
    }
  }

  async disconnect(): Promise<void> {
    const { socket, inputSocket } = this;
    this.socket = null;
    this.inputSocket = null;
    this.inbox = null;
    for (const [, future] of this.futures) {
      future.reject(new TvUnreachableError("A conexão com a TV foi fechada."));
    }
    this.futures.clear();
    inputSocket?.close();
    socket?.close();
  }

  private handleClose(reason: string): void {
    this.closeReason = reason;
    const error = new TvUnreachableError(`A TV fechou a conexão: ${reason}`);
    this.inbox?.fail(error);
    for (const [, future] of this.futures) {
      future.reject(error);
    }
    this.futures.clear();
    this.socket = null;
    this.inputSocket = null;
  }

  private receive(raw: string): void {
    let message: SsapMessage;
    try {
      message = JSON.parse(raw) as SsapMessage;
    } catch {
      // Lixo no socket não derruba a sessão: a TV às vezes emite quadros vazios.
      return;
    }

    if (this.inbox) {
      this.inbox.push(message);
      return;
    }

    const id = message.id === undefined ? "" : String(message.id);
    const future = this.futures.get(id);
    if (!future) return; // Evento de assinatura que ninguém pediu.
    this.futures.delete(id);
    future.resolve(message);
  }

  // --- requisições -----------------------------------------------------

  /** Manda um comando e não espera resposta. É o que o desligar precisa. */
  command(requestType: string, uri: string, payload: Record<string, unknown> = {}): void {
    const socket = this.socket;
    if (!socket) {
      throw new TvUnreachableError("Não estou conectado na TV.");
    }
    socket.send(
      JSON.stringify({
        id: String(this.commandCount++),
        type: requestType,
        uri: `ssap://${uri}`,
        payload,
      }),
    );
  }

  /** Manda uma requisição e devolve o payload da resposta. */
  async request(
    uri: string,
    payload: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    const socket = this.socket;
    if (!socket) {
      throw new TvUnreachableError("Não estou conectado na TV.");
    }

    const id = String(this.commandCount++);
    const future = deferred<SsapMessage>();
    this.futures.set(id, future);

    const timer = setTimeout(() => {
      if (this.futures.delete(id)) {
        future.reject(new TvUnreachableError(`A TV não respondeu ${uri} a tempo.`));
      }
    }, this.requestTimeout);

    let response: SsapMessage;
    try {
      socket.send(JSON.stringify({ id, type: "request", uri: `ssap://${uri}`, payload }));
      response = await future.promise;
    } finally {
      clearTimeout(timer);
      this.futures.delete(id);
    }

    return parseResponse(response, uri);
  }

  // --- socket de input -------------------------------------------------

  private async inputCommand(message: string): Promise<void> {
    if (!this.inputSocket) {
      const result = await this.request(endpoints.INPUT_SOCKET);
      const path = result["socketPath"];
      if (typeof path !== "string" || !path) {
        throw new TvUnreachableError("A TV não informou o endereço do socket de botões.");
      }
      this.inputSocket = await this.transport.connect(path, {
        onMessage: () => {
          // O socket de input é mão única: a TV não responde nada por ele.
        },
        onClose: () => {
          this.inputSocket = null;
        },
      });
    }
    this.inputSocket.send(message);
  }

  async button(name: string): Promise<void> {
    await this.inputCommand(`type:button\nname:${name}\n\n`);
  }

  async click(): Promise<void> {
    await this.inputCommand("type:click\n\n");
  }

  async move(dX: number, dY: number, down = 0): Promise<void> {
    await this.inputCommand(`type:move\ndx:${dX}\ndy:${dY}\ndown:${down}\n\n`);
  }
}

/**
 * Extrai o payload e transforma `returnValue: false` em erro.
 *
 * A TV responde HTTP-200-equivalente mesmo quando recusa: sem esta checagem, um
 * "app não encontrado" viraria sucesso silencioso.
 */
export function parseResponse(response: SsapMessage, uri: string): Record<string, unknown> {
  if (response.type === "error") {
    throw new TvUnreachableError(`A TV recusou ${uri}: ${response.error ?? "sem detalhe"}`);
  }
  const payload = response.payload;
  if (payload === undefined || payload === null) {
    throw new TvUnreachableError(`Resposta sem payload para ${uri}.`);
  }
  if (payload["returnValue"] === false || payload["subscribed"] === false) {
    const detail = payload["errorText"] ?? payload["errorCode"] ?? "sem detalhe";
    if (payload["returnValue"] === false) {
      throw new TvUnreachableError(`A TV recusou ${uri}: ${String(detail)}`);
    }
  }
  return payload;
}
