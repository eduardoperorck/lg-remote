/**
 * Amarra o app: monta o estado, cria o roteador e entrega para a UI.
 *
 * `app.js` continua sendo o mesmo arquivo do PWA — ele só pergunta por
 * `window.lgremote.api` em vez de sair pela rede. Trocar o servidor por nada custou
 * esta ponte e uma função lá dentro.
 */

import bundledCatalog from "../../config/apps.yaml?raw";

import { createRouter, type RequestOptions } from "../src/api/router.ts";
import { AppState } from "../src/api/state.ts";
import { NativeTransport, scanForTv, wake } from "../src/native/lgssap.ts";
import { capacitorStore } from "../src/storage/prefs.ts";

declare global {
  interface Window {
    lgremote: {
      api: (path: string, options?: RequestOptions) => Promise<unknown>;
      paired: boolean;
    };
  }
}

async function main(): Promise<void> {
  const transport = new NativeTransport();
  const state = await AppState.create({
    store: capacitorStore,
    bundledCatalog,
    transport,
    wake,
    scan: scanForTv,
    // Reencontrar a TV depois de o DHCP trocar o IP dela: o primeiro candidato que
    // responde nas portas do SSAP é a TV.
    rediscover: async () => (await scanForTv())[0]?.host ?? null,
  });

  window.lgremote = { api: createRouter(state), paired: state.paired };

  // O `app.js` roda depois desta ponte existir — daí o `defer` na ordem dos scripts.
  await import("./app.js");
}

void main();
