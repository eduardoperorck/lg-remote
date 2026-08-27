/**
 * Sessão: reconexão, redescoberta de IP e serialização dos comandos.
 *
 * Estes são os comportamentos que fazem o controle continuar funcionando quando a
 * rede muda embaixo dele — e são invisíveis quando funcionam.
 */

import { describe, expect, it, vi } from "vitest";

import type { SsapClient } from "../src/tv/client.ts";
import { TvPairError, TvUnreachableError } from "../src/tv/errors.ts";
import { REDISCOVER_COOLDOWN, TvSession } from "../src/tv/session.ts";

/** Client de mentira: decide por host quem responde e quem falha. */
class StubClient {
  connected = false;
  readonly requests: { uri: string; payload: Record<string, unknown> }[] = [];
  readonly buttons: string[] = [];
  connectAttempts = 0;
  /** Erro a lançar no connect. Zerado depois de usado quando `failOnce`. */
  connectError: Error | null = null;
  failOnce = false;
  /** Erro a lançar no próximo comando, uma vez só. */
  commandError: Error | null = null;

  constructor(
    readonly host: string,
    readonly port: number,
    public clientKey: string | null,
    readonly grantsKey: string | null = null,
  ) {}

  async connect(): Promise<void> {
    this.connectAttempts += 1;
    if (this.connectError) {
      const error = this.connectError;
      if (this.failOnce) this.connectError = null;
      throw error;
    }
    if (this.grantsKey) this.clientKey = this.grantsKey;
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  async request(uri: string, payload: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (this.commandError) {
      const error = this.commandError;
      this.commandError = null;
      throw error;
    }
    this.requests.push({ uri, payload });
    return { returnValue: true };
  }

  command(): void {}

  async button(name: string): Promise<void> {
    if (this.commandError) {
      const error = this.commandError;
      this.commandError = null;
      throw error;
    }
    this.buttons.push(name);
  }

  async click(): Promise<void> {}
  async move(): Promise<void> {}
}

const asClient = (stub: StubClient): SsapClient => stub as unknown as SsapClient;

describe("chave da TV", () => {
  it("avisa quando a TV concede uma chave nova", async () => {
    const onClientKey = vi.fn();
    const stub = new StubClient("10.0.0.5", 3001, null, "chave-fresca");
    const session = new TvSession("10.0.0.5", null, {
      clientFactory: () => asClient(stub),
      onClientKey,
    });

    await session.connect();

    expect(session.clientKey).toBe("chave-fresca");
    expect(onClientKey).toHaveBeenCalledWith("chave-fresca");
  });

  it("não avisa de novo quando a chave é a mesma", async () => {
    const onClientKey = vi.fn();
    const stub = new StubClient("10.0.0.5", 3001, "k", "k");
    const session = new TvSession("10.0.0.5", "k", {
      clientFactory: () => asClient(stub),
      onClientKey,
    });

    await session.connect();

    expect(onClientKey).not.toHaveBeenCalled();
  });
});

describe("redescoberta de IP", () => {
  const stubsByHost = (working: string) => {
    const made = new Map<string, StubClient>();
    const factory = (host: string, port: number, key: string | null): SsapClient => {
      let stub = made.get(host);
      if (!stub) {
        stub = new StubClient(host, port, key);
        if (host !== working) {
          stub.connectError = new TvUnreachableError("sem resposta");
        }
        made.set(host, stub);
      }
      return asClient(stub);
    };
    return { factory, made };
  };

  it("adota o IP novo quando a TV muda de lugar", async () => {
    const onHostChange = vi.fn();
    const { factory } = stubsByHost("192.168.2.200");
    const session = new TvSession("192.168.2.107", "k", {
      clientFactory: factory,
      rediscover: async () => "192.168.2.200",
      onHostChange,
      now: () => 10_000,
    });

    await session.connect();

    expect(session.host).toBe("192.168.2.200");
    expect(onHostChange).toHaveBeenCalledWith("192.168.2.200");
  });

  it("respeita o intervalo mínimo entre varreduras", async () => {
    // Uma varredura /24 custa segundos; sem a folga, uma TV desligada viraria uma
    // varredura atrás da outra.
    const rediscover = vi.fn(async () => "192.168.2.200");
    const { factory } = stubsByHost("192.168.2.200");
    let clock = 1000;
    const session = new TvSession("192.168.2.107", "k", {
      clientFactory: factory,
      rediscover,
      now: () => clock,
    });

    await session.connect();
    expect(rediscover).toHaveBeenCalledTimes(1);

    // Volta pro host quebrado e tenta de novo antes do intervalo passar.
    session.host = "192.168.2.107";
    await session.close();
    clock += REDISCOVER_COOLDOWN / 2;
    await expect(session.connect()).rejects.toBeInstanceOf(TvUnreachableError);
    expect(rediscover).toHaveBeenCalledTimes(1);
  });

  it("deixa o erro original passar quando a varredura quebra", async () => {
    // Procurar a TV é socorro, não comando: o usuário precisa ver "não falei com a
    // TV", não um erro secundário do resgate.
    const { factory } = stubsByHost("nunca");
    const session = new TvSession("192.168.2.107", "k", {
      clientFactory: factory,
      rediscover: async () => {
        throw new Error("varredura explodiu");
      },
      now: () => 10_000,
    });

    await expect(session.connect()).rejects.toBeInstanceOf(TvUnreachableError);
  });

  it("não procura a TV quando a chave é que foi recusada", async () => {
    // Trocar de IP não conserta chave inválida — varrer a rede seria perder tempo.
    const rediscover = vi.fn(async () => "192.168.2.200");
    const stub = new StubClient("192.168.2.107", 3001, "k");
    stub.connectError = new TvPairError("A TV recusou a chave salva");
    const session = new TvSession("192.168.2.107", "k", {
      clientFactory: () => asClient(stub),
      rediscover,
      now: () => 10_000,
    });

    await expect(session.connect()).rejects.toBeInstanceOf(TvPairError);
    expect(rediscover).not.toHaveBeenCalled();
  });
});

describe("execução de comandos", () => {
  it("reconecta uma vez e repete o comando", async () => {
    const stub = new StubClient("10.0.0.5", 3001, "k");
    const session = new TvSession("10.0.0.5", "k", { clientFactory: () => asClient(stub) });

    await session.connect();
    stub.commandError = new TvUnreachableError("socket morreu");
    await session.button("UP");

    expect(stub.buttons).toEqual(["UP"]);
    expect(stub.connectAttempts).toBe(2);
  });

  it("desiste quando a segunda tentativa também falha", async () => {
    // Insistir só faz a tela travar esperando: se falhou duas vezes, é a TV.
    const stub = new StubClient("10.0.0.5", 3001, "k");
    const session = new TvSession("10.0.0.5", "k", { clientFactory: () => asClient(stub) });
    await session.connect();

    stub.button = async () => {
      throw new TvUnreachableError("socket morreu");
    };

    await expect(session.button("UP")).rejects.toBeInstanceOf(TvUnreachableError);
  });

  it("não repete comando quando a TV recusou a chave", async () => {
    const stub = new StubClient("10.0.0.5", 3001, "k");
    const session = new TvSession("10.0.0.5", "k", { clientFactory: () => asClient(stub) });
    await session.connect();

    stub.commandError = new TvPairError("chave recusada");

    await expect(session.button("UP")).rejects.toBeInstanceOf(TvPairError);
    expect(stub.connectAttempts).toBe(1);
  });

  it("serializa comandos disparados juntos", async () => {
    // O socket de input do webOS embaralha as respostas se dois comandos saem juntos.
    const order: string[] = [];
    const stub = new StubClient("10.0.0.5", 3001, "k");
    stub.button = async (name: string) => {
      order.push(`inicio ${name}`);
      await new Promise((resolve) => setTimeout(resolve, 5));
      order.push(`fim ${name}`);
    };
    const session = new TvSession("10.0.0.5", "k", { clientFactory: () => asClient(stub) });

    await Promise.all([session.button("UP"), session.button("DOWN"), session.button("LEFT")]);

    expect(order).toEqual([
      "inicio UP",
      "fim UP",
      "inicio DOWN",
      "fim DOWN",
      "inicio LEFT",
      "fim LEFT",
    ]);
  });

  it("recusa botão fora da whitelist antes de tocar na rede", async () => {
    const stub = new StubClient("10.0.0.5", 3001, "k");
    const session = new TvSession("10.0.0.5", "k", { clientFactory: () => asClient(stub) });

    await expect(session.button("FORMATAR_TUDO")).rejects.toThrow(/desconhecido/);
    expect(stub.connectAttempts).toBe(0);
  });
});
