import type { PluginListenerHandle } from "@capacitor/core";

export interface ConnectOptions {
  /** `wss://192.168.2.107:3001`. O host desta URL é o único que ganha a exceção. */
  url: string;
  timeoutMs?: number;
}

export interface ConnectResult {
  /** Identificador para mandar e fechar. Vive só enquanto a conexão vive. */
  id: string;
}

export interface SocketMessageEvent {
  id: string;
  data: string;
}

export interface SocketCloseEvent {
  id: string;
  reason: string;
}

export interface ScanCandidate {
  host: string;
  port: number;
}

export interface LgSsapPlugin {
  connect: (options: ConnectOptions) => Promise<ConnectResult>;
  send: (options: { id: string; data: string }) => Promise<void>;
  close: (options: { id: string }) => Promise<void>;

  /** Guarda no Keychain — é o que sobrevive à re-assinatura do SideStore. */
  keychainSet: (options: { key: string; value: string }) => Promise<void>;
  keychainGet: (options: { key: string }) => Promise<{ value: string | null }>;
  keychainDelete: (options: { key: string }) => Promise<void>;

  /** Pacote mágico UDP nas portas 9 e 7. */
  wake: (options: { mac: string }) => Promise<void>;
  /** Varre a /24 procurando quem responde nas portas do SSAP. */
  scan: (options?: { ports?: number[] }) => Promise<{ candidates: ScanCandidate[] }>;
  localAddress: () => Promise<{ address: string | null }>;

  addListener: ((
    event: "message",
    handler: (data: SocketMessageEvent) => void,
  ) => Promise<PluginListenerHandle>) &
    ((
      event: "close",
      handler: (data: SocketCloseEvent) => void,
    ) => Promise<PluginListenerHandle>);
}
