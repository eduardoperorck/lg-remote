/**
 * Camada de socket, trocável.
 *
 * No iPhone o WebSocket precisa aceitar o certificado autoassinado da TV, e isso só
 * existe em código nativo — por isso a implementação real é o plugin Swift. Aqui a
 * interface fica mínima de propósito: é o único ponto do app que muda entre rodar no
 * iOS de verdade e rodar contra a TV falsa no Windows.
 */

export interface TransportHandlers {
  onMessage: (data: string) => void;
  /** Chamado uma vez, com o motivo, quando a conexão morre por qualquer razão. */
  onClose: (reason: string) => void;
}

export interface SsapSocket {
  send: (data: string) => void;
  close: () => void;
}

export interface Transport {
  connect: (url: string, handlers: TransportHandlers) => Promise<SsapSocket>;
}

export class TransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TransportError";
  }
}

/**
 * Transporte via WebSocket padrão.
 *
 * Serve o navegador (dev) e o Node (testes contra a TV falsa). Em `wss://` com
 * certificado autoassinado ele falha — é exatamente essa limitação que o plugin
 * nativo existe para contornar.
 */
export class WebSocketTransport implements Transport {
  constructor(private readonly openTimeoutMs = 10_000) {}

  connect(url: string, handlers: TransportHandlers): Promise<SsapSocket> {
    return new Promise((resolve, reject) => {
      let socket: WebSocket;
      try {
        socket = new WebSocket(url);
      } catch (cause) {
        reject(new TransportError(`URL inválida para WebSocket: ${url}`));
        return;
      }

      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try {
          socket.close();
        } catch {
          // Fechar um socket que nunca abriu é ruído, não erro.
        }
        reject(new TransportError(`A TV não respondeu em ${this.openTimeoutMs}ms (${url})`));
      }, this.openTimeoutMs);

      socket.onopen = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({
          send: (data) => socket.send(data),
          close: () => socket.close(),
        });
      };

      socket.onmessage = (event: MessageEvent) => {
        handlers.onMessage(typeof event.data === "string" ? event.data : String(event.data));
      };

      socket.onerror = () => {
        // O evento de erro do WebSocket não carrega detalhe nenhum por design (é uma
        // proteção do navegador). Quem tem a informação útil é o onclose.
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new TransportError(`Não consegui abrir ${url}`));
      };

      socket.onclose = (event: CloseEvent) => {
        clearTimeout(timer);
        const reason = event.reason || `código ${event.code}`;
        if (!settled) {
          settled = true;
          reject(new TransportError(`A conexão com ${url} fechou antes de abrir: ${reason}`));
          return;
        }
        handlers.onClose(reason);
      };
    });
  }
}
