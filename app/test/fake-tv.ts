/**
 * TV LG falsa: fala SSAP o suficiente para o app inteiro ser testado sem TV.
 *
 * Existe porque o desenvolvimento acontece no Windows e a TV mora na sala. Cobre os
 * dois sockets (o principal, JSON com id, e o de input, texto) e sabe encenar os
 * casos que importam: chave aceita, chave recusada na hora, e o pedido que aparece
 * na tela e demora para ser respondido.
 */

import { WebSocketServer, type WebSocket } from "ws";
import type { AddressInfo } from "node:net";

export type PromptAnswer = "accept" | "refuse" | "silence";

export interface FakeTvOptions {
  /** Chave que a TV reconhece. Registrar com ela pula o pedido na tela. */
  acceptedKey?: string;
  /** Chave devolvida quando o pareamento é aceito. */
  grantedKey?: string;
  /** Recusa sem exibir nada na tela — a TV em standby faz isso. */
  instantRefusal?: boolean;
  /**
   * Aceita de cara, sem pedido na tela, mesmo com chave nula: é o que a TV faz
   * quando ainda lembra deste aparelho na lista de dispositivos.
   */
  remembersDevice?: boolean;
  /** O que acontece depois que o pedido aparece na tela. */
  promptAnswer?: PromptAnswer;
  /** Quanto a "pessoa na frente da TV" demora para decidir. */
  promptDelayMs?: number;
  /** Respostas por URI (sem o prefixo ssap://). O padrão é `{returnValue: true}`. */
  responses?: Record<string, Record<string, unknown>>;
}

export interface RecordedRequest {
  uri: string;
  payload: Record<string, unknown>;
}

export class FakeTv {
  /** Requisições JSON que chegaram no socket principal, em ordem. */
  readonly requests: RecordedRequest[] = [];
  /** Mensagens cruas do socket de input (`type:button\nname:UP\n\n`). */
  readonly inputMessages: string[] = [];

  private server: WebSocketServer | null = null;
  private port = 0;
  private readonly sockets = new Set<WebSocket>();
  private readonly timers = new Set<NodeJS.Timeout>();

  private readonly options: FakeTvOptions;

  // Campo explícito e não parâmetro-propriedade: o `node --experimental-strip-types`
  // do `npm run faketv` não entende a forma curta.
  constructor(options: FakeTvOptions = {}) {
    this.options = options;
  }

  /** Só os nomes dos botões, que é o que quase todo teste quer conferir. */
  get buttons(): string[] {
    return this.inputMessages
      .map((raw) => /^type:button\nname:(.+)\n\n$/.exec(raw)?.[1])
      .filter((name): name is string => name !== undefined);
  }

  get url(): string {
    return `ws://127.0.0.1:${this.port}`;
  }

  /** Porta zero: o sistema escolhe uma livre, e testes paralelos não colidem. */
  async start(): Promise<string> {
    return this.startOn(0);
  }

  async startOn(port: number): Promise<string> {
    const server = new WebSocketServer({ host: "127.0.0.1", port });
    this.server = server;
    await new Promise<void>((resolve) => server.once("listening", resolve));
    this.port = (server.address() as AddressInfo).port;

    server.on("connection", (socket, request) => {
      this.sockets.add(socket);
      socket.on("close", () => this.sockets.delete(socket));
      if (request.url === "/input") {
        socket.on("message", (data) => this.inputMessages.push(data.toString()));
        return;
      }
      socket.on("message", (data) => this.handleMain(socket, data.toString()));
    });

    return this.url;
  }

  async stop(): Promise<void> {
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    for (const socket of this.sockets) socket.terminate();
    this.sockets.clear();
    const server = this.server;
    this.server = null;
    if (server) {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  }

  /** Derruba as conexões sem fechar o servidor — simula a TV sumindo da rede. */
  dropConnections(): void {
    for (const socket of this.sockets) socket.terminate();
    this.sockets.clear();
  }

  private later(ms: number, action: () => void): void {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      action();
    }, ms);
    this.timers.add(timer);
  }

  private send(socket: WebSocket, message: Record<string, unknown>): void {
    if (socket.readyState === socket.OPEN) {
      socket.send(JSON.stringify(message));
    }
  }

  private handleMain(socket: WebSocket, raw: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }

    const id = message["id"];
    const type = message["type"];

    if (type === "hello") {
      this.send(socket, { id, type: "hello", payload: { protocolVersion: 1 } });
      return;
    }

    if (type === "register") {
      this.handleRegister(socket, message);
      return;
    }

    const uri = String(message["uri"] ?? "").replace(/^ssap:\/\//, "");
    const payload = (message["payload"] ?? {}) as Record<string, unknown>;
    this.requests.push({ uri, payload });

    if (uri === "com.webos.service.networkinput/getPointerInputSocket") {
      this.send(socket, {
        id,
        type: "response",
        payload: { returnValue: true, socketPath: `ws://127.0.0.1:${this.port}/input` },
      });
      return;
    }

    const canned = this.options.responses?.[uri];
    this.send(socket, {
      id,
      type: "response",
      payload: canned ?? { returnValue: true },
    });
  }

  private handleRegister(socket: WebSocket, message: Record<string, unknown>): void {
    const id = message["id"] ?? "register_0";
    const payload = (message["payload"] ?? {}) as Record<string, unknown>;
    const offered = payload["client-key"];
    const {
      acceptedKey,
      grantedKey = "chave-nova-da-tv",
      instantRefusal = false,
      promptAnswer = "accept",
      promptDelayMs = 5,
    } = this.options;

    // Chave que a TV já conhece: entra direto, sem pedido na tela.
    if (acceptedKey && offered === acceptedKey) {
      this.send(socket, { id, type: "registered", payload: { "client-key": acceptedKey } });
      return;
    }

    if (instantRefusal) {
      this.send(socket, { id, type: "error", error: "403 access denied" });
      return;
    }

    if (this.options.remembersDevice) {
      this.send(socket, { id, type: "registered", payload: { "client-key": grantedKey } });
      return;
    }

    // A TV avisa que pôs o pedido na tela, e só depois manda a decisão.
    this.send(socket, { id, type: "response", payload: { pairingType: "PROMPT", returnValue: true } });

    if (promptAnswer === "silence") return;
    this.later(promptDelayMs, () => {
      if (promptAnswer === "refuse") {
        this.send(socket, { id, type: "error", error: "rejected pairing" });
      } else {
        this.send(socket, { id, type: "registered", payload: { "client-key": grantedKey } });
      }
    });
  }
}
