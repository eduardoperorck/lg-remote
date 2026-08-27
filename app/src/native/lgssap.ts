/**
 * O transporte de verdade: sockets abertos pelo plugin nativo.
 *
 * A `Transport` do app é a mesma interface usada nos testes; aqui ela é implementada
 * por cima do plugin, que é quem sabe aceitar o certificado autoassinado da TV.
 */

import { LgSsap, type ScanCandidate } from "../../plugins/lg-ssap/src/index.ts";
import { TransportError, type SsapSocket, type Transport, type TransportHandlers } from "../tv/transport.ts";

export class NativeTransport implements Transport {
  private readonly handlers = new Map<string, TransportHandlers>();
  /**
   * O plugin começa a ler o socket antes de a promessa de `connect` resolver, então
   * uma mensagem pode chegar antes de haver quem a receba. Guardar em vez de
   * descartar é o que evita perder a primeira resposta da TV.
   */
  private readonly pending = new Map<string, string[]>();
  private listening: Promise<void> | null = null;

  constructor(private readonly timeoutMs = 10_000) {}

  private ensureListening(): Promise<void> {
    this.listening ??= (async () => {
      await LgSsap.addListener("message", ({ id, data }) => {
        const handler = this.handlers.get(id);
        if (handler) {
          handler.onMessage(data);
          return;
        }
        const queue = this.pending.get(id) ?? [];
        queue.push(data);
        this.pending.set(id, queue);
      });
      await LgSsap.addListener("close", ({ id, reason }) => {
        const handler = this.handlers.get(id);
        this.handlers.delete(id);
        this.pending.delete(id);
        handler?.onClose(reason);
      });
    })();
    return this.listening;
  }

  async connect(url: string, handlers: TransportHandlers): Promise<SsapSocket> {
    await this.ensureListening();

    let id: string;
    try {
      ({ id } = await LgSsap.connect({ url, timeoutMs: this.timeoutMs }));
    } catch (cause) {
      throw new TransportError(cause instanceof Error ? cause.message : String(cause));
    }

    this.handlers.set(id, handlers);
    for (const buffered of this.pending.get(id) ?? []) handlers.onMessage(buffered);
    this.pending.delete(id);

    return {
      send: (data) => {
        // O envio é assíncrono do lado nativo, mas quem chama trata isso como
        // disparo — igual ao `send()` de um WebSocket comum.
        void LgSsap.send({ id, data }).catch((error: unknown) => {
          handlers.onClose(error instanceof Error ? error.message : String(error));
        });
      },
      close: () => {
        this.handlers.delete(id);
        this.pending.delete(id);
        void LgSsap.close({ id });
      },
    };
  }
}

/** Liga a TV pela rede. Só funciona com a TV em standby, não desligada da tomada. */
export async function wake(mac: string): Promise<void> {
  await LgSsap.wake({ mac });
}

/** Varre a /24 procurando a TV. Devolve os candidatos em ordem de descoberta. */
export async function scanForTv(ports?: number[]): Promise<ScanCandidate[]> {
  const { candidates } = await LgSsap.scan(ports ? { ports } : {});
  return candidates;
}

export async function localAddress(): Promise<string | null> {
  const { address } = await LgSsap.localAddress();
  return address;
}
