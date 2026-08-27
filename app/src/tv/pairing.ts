/**
 * Registro (pareamento) na TV, com prazo humano e diagnóstico do "não".
 *
 * O `connect()` do cliente também pareia, mas joga fora a única informação que resolve
 * o caso difícil — quanto tempo a TV demorou para negar. Uma recusa que volta na hora
 * prova que a TV nunca chegou a mostrar o pedido na tela, e isso muda completamente o
 * conselho que damos ao usuário.
 */

import * as endpoints from "./endpoints.ts";
import { registrationMessage } from "./handshake.ts";
import { ssapUrl, type SsapMessage } from "./client.ts";
import type { SsapSocket, Transport } from "./transport.ts";
import { WebSocketTransport } from "./transport.ts";

export const PAIR_TIMEOUT = 60_000;
/**
 * Abaixo disto a TV respondeu rápido demais para ter havido gente lendo o pedido na
 * tela: ela negou sozinha.
 */
export const INSTANT_DENIAL = 2_000;
export const HELLO_TIMEOUT = 10_000;

export const REFUSED_WITHOUT_PROMPT = `A TV recusou sozinha, sem chegar a mostrar o pedido na tela.
Quase sempre é um destes três:
  1. A TV está em standby. Com o Quick Start+ ligado ela continua respondendo na
     rede, mas a tela apagada não tem como exibir o pedido. Ligue a TV e deixe na
     tela inicial (fora de qualquer app) antes de tentar de novo.
  2. Uma recusa antiga ficou gravada. Na TV: Configurações → Geral → Dispositivos →
     Dispositivos externos → apague o histórico de conexões.
  3. Configurações → Geral → Mobile TV On desligado.`;

export const REFUSED_BY_USER = `A TV mostrou o pedido e ele foi recusado.
Tente de novo e escolha "Sim" com o controle físico.`;

/** Base do que pode dar errado ao registrar este aparelho na TV. */
export class PairError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PairError";
  }
}

/**
 * A TV disse não.
 *
 * `prompted` distingue os dois "não" que exigem conselhos opostos: se a TV exibiu o
 * pedido, é caso de tentar de novo e aceitar; se não exibiu, tentar de novo não
 * adianta nada até resolver o standby ou o histórico de conexões.
 */
export class PairRefusedError extends PairError {
  constructor(
    readonly detail: string,
    readonly prompted: boolean,
  ) {
    super(prompted ? REFUSED_BY_USER : REFUSED_WITHOUT_PROMPT);
    this.name = "PairRefusedError";
  }
}

/** A TV mostrou o pedido e ninguém aceitou dentro do prazo. */
export class PairTimeoutError extends PairError {
  constructor(message: string) {
    super(message);
    this.name = "PairTimeoutError";
  }
}

/** Não deu nem para falar com a TV. */
export class PairUnreachableError extends PairError {
  constructor(message: string) {
    super(message);
    this.name = "PairUnreachableError";
  }
}

class Conversation {
  private readonly queue: SsapMessage[] = [];
  private waiter: { resolve: (m: SsapMessage) => void; reject: (e: Error) => void } | null = null;
  private failure: Error | null = null;

  push(raw: string): void {
    let message: SsapMessage;
    try {
      message = JSON.parse(raw) as SsapMessage;
    } catch {
      return;
    }
    if (this.waiter) {
      const { resolve } = this.waiter;
      this.waiter = null;
      resolve(message);
      return;
    }
    this.queue.push(message);
  }

  fail(reason: string): void {
    this.failure = new PairUnreachableError(`A TV fechou a conexão: ${reason}`);
    if (this.waiter) {
      const { reject } = this.waiter;
      this.waiter = null;
      reject(this.failure);
    }
  }

  next(timeoutMs: number, onTimeout: () => Error): Promise<SsapMessage> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    if (this.failure) return Promise.reject(this.failure);

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.waiter) {
          this.waiter = null;
          reject(onTimeout());
        }
      }, timeoutMs);
      this.waiter = {
        resolve: (message) => {
          clearTimeout(timer);
          resolve(message);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      };
    });
  }
}

export interface PairOptions {
  transport?: Transport;
  /** Quanto tempo dar para alguém aceitar na TV. */
  promptTimeout?: number;
  /** Relógio em ms, injetável para o teste medir a demora sem esperar de verdade. */
  now?: () => number;
}

/**
 * Registra este aparelho na TV e devolve o client key.
 *
 * Ao contrário do `connect()`, aqui o prazo é humano (um minuto) e a recusa vem
 * classificada: dá para dizer ao usuário se vale a pena tentar de novo.
 */
export async function pair(
  host: string,
  port: number,
  options: PairOptions = {},
): Promise<string> {
  const transport = options.transport ?? new WebSocketTransport(HELLO_TIMEOUT);
  const promptTimeout = options.promptTimeout ?? PAIR_TIMEOUT;
  const now = options.now ?? (() => Date.now());
  const conversation = new Conversation();
  const unreachable = (): PairUnreachableError =>
    new PairUnreachableError(
      `Não consegui falar com a TV em ${host}:${port}. ` +
        "Confira se ela está ligada, na mesma rede, e se o IP ainda é esse.",
    );

  let socket: SsapSocket;
  try {
    socket = await transport.connect(ssapUrl(host, port), {
      onMessage: (raw) => conversation.push(raw),
      onClose: (reason) => conversation.fail(reason),
    });
  } catch {
    throw unreachable();
  }

  try {
    // Um pedido por vez, resposta lida antes do próximo: mandar tudo de uma vez
    // embaralharia as respostas e a leitura pegaria a errada.
    socket.send(JSON.stringify({ id: "hello", type: "hello", payload: {} }));
    await conversation.next(HELLO_TIMEOUT, unreachable);

    // Não é enfeite: o firmware novo exige o getSystemInfo ANTES do registro.
    socket.send(
      JSON.stringify({
        id: "sysinfo",
        type: "request",
        uri: `ssap://${endpoints.GET_SYSTEM_INFO}`,
        payload: {},
      }),
    );
    await conversation.next(HELLO_TIMEOUT, unreachable);

    socket.send(JSON.stringify(registrationMessage(null)));
    const first = await conversation.next(HELLO_TIMEOUT, unreachable);

    // Chave já aceita de cara: acontece quando a TV lembra deste aparelho.
    if (first.type === "registered") {
      const key = first.payload?.["client-key"];
      if (typeof key === "string" && key) return key;
    }
    if (first.type === "error") {
      throw new PairRefusedError(String(first.error ?? "sem detalhe"), false);
    }

    // A TV pôs o pedido na tela. Daqui em diante o relógio é o do usuário.
    const started = now();
    let response: SsapMessage;
    try {
      response = await conversation.next(
        promptTimeout,
        () =>
          new PairTimeoutError(
            `A TV mostrou o pedido, mas ninguém aceitou em ${Math.round(promptTimeout / 1000)} segundos.`,
          ),
      );
    } catch (error) {
      throw error instanceof PairError ? error : unreachable();
    }

    if (response.type === "error") {
      throw new PairRefusedError(
        String(response.error ?? "sem detalhe"),
        now() - started >= INSTANT_DENIAL,
      );
    }
    if (response.type === "registered") {
      const key = response.payload?.["client-key"];
      if (typeof key === "string" && key) return key;
    }

    throw new PairError(
      `A TV respondeu algo que não sei ler no pareamento: ${JSON.stringify(response)}`,
    );
  } finally {
    socket.close();
  }
}
