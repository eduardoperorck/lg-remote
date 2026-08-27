/**
 * Onde o app guarda as coisas — a interface, para os testes não precisarem do iOS.
 *
 * A chave da TV fica separada do resto de propósito: ela vai para o Keychain, que
 * sobrevive à reinstalação e à re-assinatura de 7 dias do SideStore. É esse detalhe
 * que faz o pareamento não se perder. O resto é redescobrível (o IP da TV) ou
 * redigitável (o token do TMDb), e pode morrer junto com o app.
 */

export const KEYS = {
  tvHost: "tv.host",
  tvPort: "tv.port",
  tvMac: "tv.mac",
  tmdbToken: "tmdb.token",
  presets: "presets.json",
  catalogOverride: "apps.yaml",
} as const;

export type PrefKey = (typeof KEYS)[keyof typeof KEYS];

export interface Store {
  get: (key: PrefKey) => Promise<string | null>;
  set: (key: PrefKey, value: string) => Promise<void>;
  remove: (key: PrefKey) => Promise<void>;

  /** A chave que a TV concedeu. Keychain no iOS, não Preferences. */
  getClientKey: () => Promise<string | null>;
  setClientKey: (value: string) => Promise<void>;
  /** Só para "esquecer esta TV" — apagar por engano custa um pareamento novo. */
  forgetClientKey: () => Promise<void>;
}

/** Store de memória, para teste e para o modo de desenvolvimento no navegador. */
export class MemoryStore implements Store {
  private readonly values = new Map<string, string>();
  private clientKey: string | null = null;

  constructor(initial: Partial<Record<PrefKey, string>> = {}, clientKey: string | null = null) {
    for (const [key, value] of Object.entries(initial)) {
      if (value !== undefined) this.values.set(key, value);
    }
    this.clientKey = clientKey;
  }

  async get(key: PrefKey): Promise<string | null> {
    return this.values.get(key) ?? null;
  }

  async set(key: PrefKey, value: string): Promise<void> {
    this.values.set(key, value);
  }

  async remove(key: PrefKey): Promise<void> {
    this.values.delete(key);
  }

  async getClientKey(): Promise<string | null> {
    return this.clientKey;
  }

  async setClientKey(value: string): Promise<void> {
    this.clientKey = value;
  }

  async forgetClientKey(): Promise<void> {
    this.clientKey = null;
  }
}
