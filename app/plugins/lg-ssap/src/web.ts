import { WebPlugin } from "@capacitor/core";

import type {
  ConnectOptions,
  ConnectResult,
  LgSsapPlugin,
  ScanCandidate,
} from "./definitions.ts";

/**
 * Implementação de navegador, para `npm run dev` no Windows.
 *
 * Faz o que dá sem código nativo: abre `ws://` contra a TV falsa e guarda a chave no
 * localStorage. O que ela NÃO faz é justamente o motivo do plugin existir — `wss://`
 * com certificado autoassinado, broadcast UDP e varredura de portas. Essas falham com
 * uma mensagem explícita em vez de fingir sucesso.
 */
export class LgSsapWeb extends WebPlugin implements LgSsapPlugin {
  private readonly sockets = new Map<string, WebSocket>();
  private nextId = 0;

  async connect(options: ConnectOptions): Promise<ConnectResult> {
    const id = `web-socket-${(this.nextId += 1)}`;
    const socket = new WebSocket(options.url);

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`A TV não respondeu em ${options.url}`)),
        options.timeoutMs ?? 10_000,
      );
      socket.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
      socket.onerror = () => {
        clearTimeout(timer);
        reject(new Error(`Não consegui abrir ${options.url}`));
      };
    });

    socket.onmessage = (event: MessageEvent) => {
      this.notifyListeners("message", { id, data: String(event.data) });
    };
    socket.onclose = (event: CloseEvent) => {
      this.sockets.delete(id);
      this.notifyListeners("close", { id, reason: event.reason || `código ${event.code}` });
    };

    this.sockets.set(id, socket);
    return { id };
  }

  async send(options: { id: string; data: string }): Promise<void> {
    const socket = this.sockets.get(options.id);
    if (!socket) throw new Error(`Socket ${options.id} não existe.`);
    socket.send(options.data);
  }

  async close(options: { id: string }): Promise<void> {
    this.sockets.get(options.id)?.close();
    this.sockets.delete(options.id);
  }

  async keychainSet(options: { key: string; value: string }): Promise<void> {
    localStorage.setItem(`lgssap.${options.key}`, options.value);
  }

  async keychainGet(options: { key: string }): Promise<{ value: string | null }> {
    return { value: localStorage.getItem(`lgssap.${options.key}`) };
  }

  async keychainDelete(options: { key: string }): Promise<void> {
    localStorage.removeItem(`lgssap.${options.key}`);
  }

  async wake(): Promise<void> {
    throw this.unavailable("Wake-on-LAN precisa do app nativo — o navegador não manda UDP.");
  }

  async scan(): Promise<{ candidates: ScanCandidate[] }> {
    throw this.unavailable("A varredura da rede precisa do app nativo.");
  }

  async localAddress(): Promise<{ address: string | null }> {
    return { address: null };
  }
}
