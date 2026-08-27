/**
 * Teste de ponta a ponta: a mesma chamada que a tela faz, contra a TV falsa.
 *
 * É o que prova que a pilha inteira fecha — roteador, sessão, protocolo SSAP e
 * socket — sem nenhum PC no meio. Também trava os formatos de resposta que a UI
 * consome: um campo renomeado aqui vira um pedaço da tela em branco lá.
 */

import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ApiError, createRouter, type Router } from "../src/api/router.ts";
import { AppState } from "../src/api/state.ts";
import { KEYS, MemoryStore } from "../src/storage/store.ts";
import { FakeTv } from "./fake-tv.ts";

const CATALOG = readFileSync("../config/apps.yaml", "utf-8");

let tv: FakeTv;
let state: AppState;
let api: Router;
let store: MemoryStore;

async function boot(extra: Partial<Record<string, string>> = {}): Promise<void> {
  tv = new FakeTv({
    acceptedKey: "chave-boa",
    responses: {
      "com.webos.applicationManager/getForegroundAppInfo": {
        returnValue: true,
        appId: "com.wbd.stream",
      },
      "audio/getVolume": { returnValue: true, volumeStatus: { volume: 17 } },
    },
  });
  await tv.start();
  const port = new URL(tv.url).port;

  store = new MemoryStore(
    { [KEYS.tvHost]: "127.0.0.1", [KEYS.tvPort]: port, ...extra },
    "chave-boa",
  );
  state = await AppState.create({
    store,
    bundledCatalog: CATALOG,
    // Sem espera de verdade: as macros deste teste levariam ~25 segundos reais.
    sleep: async () => {},
  });
  api = createRouter(state);
}

beforeEach(() => boot());

afterEach(async () => {
  await state.close();
  await tv.stop();
});

describe("estado", () => {
  it("responde o config com os atalhos do catálogo real", async () => {
    const config = (await api("/api/config")) as Record<string, unknown>;

    expect(config["tv_host"]).toBe("127.0.0.1");
    expect(config["paired"]).toBe(true);
    expect(config["catalog_enabled"]).toBe(false); // sem token do TMDb
    expect(Array.isArray(config["shortcuts"])).toBe(true);
    expect((config["shortcuts"] as unknown[]).length).toBeGreaterThan(0);
  });

  it("fala com a TV de verdade no status", async () => {
    const status = (await api("/api/status")) as Record<string, unknown>;

    expect(status["online"]).toBe(true);
    expect(status["current_app"]).toBe("com.wbd.stream");
    expect(status["volume"]).toBe(17);
    expect(status["recording"]).toBe(false);
  });

  it("devolve o detalhe do erro quando a TV não responde", async () => {
    // Descartar o detalhe deixava toda falha com a mesma cara de "TV desligada",
    // inclusive as que têm conserto conhecido.
    await tv.stop();

    const status = (await api("/api/status")) as Record<string, unknown>;

    expect(status["online"]).toBe(false);
    expect(String(status["detail"])).toMatch(/TV/);
  });
});

describe("controle", () => {
  it("manda o botão pelo socket de input", async () => {
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "UP" }) });

    await expect.poll(() => tv.buttons).toEqual(["UP"]);
  });

  it("repete o botão com respiro entre os toques", async () => {
    await api("/api/button", {
      method: "POST",
      body: JSON.stringify({ name: "RIGHT", times: 3 }),
    });

    await expect.poll(() => tv.buttons).toEqual(["RIGHT", "RIGHT", "RIGHT"]);
  });

  it("recusa botão fora da whitelist com 400", async () => {
    const error = await api("/api/button", {
      method: "POST",
      body: JSON.stringify({ name: "FORMATAR" }),
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
  });

  it("limita a repetição em vez de inundar a TV", async () => {
    await api("/api/button", {
      method: "POST",
      body: JSON.stringify({ name: "UP", times: 9999 }),
    });

    await expect.poll(() => tv.buttons.length).toBe(30);
  });

  it("digita e manda enter", async () => {
    await api("/api/text", {
      method: "POST",
      body: JSON.stringify({ text: "Severance", enter: true }),
    });

    const typed = tv.requests.filter((r) => r.uri === "com.webos.service.ime/insertText");
    expect(typed[0]?.payload).toEqual({ text: "Severance", replace: 1 });
    expect(tv.requests.some((r) => r.uri === "com.webos.service.ime/sendEnterKey")).toBe(true);
  });
});

describe("abrir título", () => {
  it("roda a macro do serviço e reporta o progresso", async () => {
    const started = (await api("/api/open", {
      method: "POST",
      body: JSON.stringify({ service_id: "max", title: "The Last of Us" }),
    })) as Record<string, unknown>;

    expect(started["status"]).toBe("started");
    expect(String(started["label"])).toContain("The Last of Us");

    await state.runner.settle();

    const action = (await api("/api/action")) as Record<string, unknown>;
    expect(action["status"]).toBe("done");
    expect((action["trace"] as string[]).length).toBeGreaterThan(0);
    // O título digitado chegou na TV pelo IME.
    const typed = tv.requests.filter((r) => r.uri === "com.webos.service.ime/insertText");
    expect(typed.at(-1)?.payload["text"]).toBe("The Last of Us");
  });

  it("recusa uma segunda ação enquanto a primeira roda", async () => {
    // Duas macros ao mesmo tempo mandariam teclas concorrentes e as duas falhariam.
    await api("/api/open", {
      method: "POST",
      body: JSON.stringify({ service_id: "max", title: "Fallout" }),
    });

    const error = await api("/api/open", {
      method: "POST",
      body: JSON.stringify({ service_id: "max", title: "Severance" }),
    }).catch((e: unknown) => e);

    expect((error as ApiError).status).toBe(409);
    await state.runner.settle();
  });

  it("avisa no rótulo quando o serviço não sabe escolher episódio", async () => {
    const service = state.catalog.services.find((s) => s.episode.length === 0);
    const started = (await api("/api/open", {
      method: "POST",
      body: JSON.stringify({
        service_id: service!.id,
        title: "Severance",
        season: 1,
        episode: 4,
      }),
    })) as Record<string, unknown>;

    expect(String(started["label"])).toContain("sem macro de episódio");
    await state.runner.settle();
  });
});

describe("gravador e presets", () => {
  it("grava um preset e ele volta na listagem", async () => {
    await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "preset", service_id: "max" }),
    });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "DOWN" }) });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "ENTER" }) });

    const saved = (await api("/api/record/stop", {
      method: "POST",
      body: JSON.stringify({ label: "Legenda PT" }),
    })) as Record<string, Record<string, unknown>>;

    expect(saved["status"]).toBe("saved");
    expect(saved["preset"]?.["id"]).toBe("legenda-pt");
    expect(saved["preset"]?.["steps"]).toBe(2);

    const listed = (await api("/api/presets?service_id=max")) as Record<string, unknown[]>;
    expect(listed["presets"]).toHaveLength(1);
  });

  it("mantém o preset salvo entre sessões", async () => {
    // O que o gravador escreve vai para o armazenamento, não para a memória.
    await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "preset", service_id: "max" }),
    });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "DOWN" }) });
    await api("/api/record/stop", {
      method: "POST",
      body: JSON.stringify({ label: "Dublado" }),
    });

    const revived = await AppState.create({ store, bundledCatalog: CATALOG });
    expect(revived.presets.get("max", "dublado")?.label).toBe("Dublado");
    await revived.close();
  });

  it("grava a macro de busca dentro do catálogo", async () => {
    await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "search", service_id: "max", sample_title: "Fallout" }),
    });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "LEFT" }) });
    await api("/api/text", { method: "POST", body: JSON.stringify({ text: "Fallout" }) });

    const saved = (await api("/api/record/stop", {
      method: "POST",
      body: JSON.stringify({}),
    })) as Record<string, unknown>;

    expect(saved["status"]).toBe("saved");
    expect(saved["target"]).toBe("search");
    // O título digitado virou placeholder: a macro serve para qualquer título.
    expect(saved["steps"]).toContain('{text: "{title}"}');
    // E o catálogo em memória já reflete a macro nova, sem precisar reabrir o app.
    expect(state.catalog.byId("max")!.search).toContainEqual({
      kind: "text",
      template: "{title}",
    });
  });

  it("exige o app em foco para gravar preset sem serviço escolhido", async () => {
    // Senão o preset nasce preso ao serviço errado.
    await tv.stop();

    const error = await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "preset" }),
    }).catch((e: unknown) => e);

    expect((error as ApiError).status).toBe(409);
  });

  it("apaga o preset", async () => {
    await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "preset", service_id: "max" }),
    });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "DOWN" }) });
    await api("/api/record/stop", {
      method: "POST",
      body: JSON.stringify({ label: "Dublado" }),
    });

    await api("/api/presets/max/dublado", { method: "DELETE" });

    const listed = (await api("/api/presets")) as Record<string, unknown[]>;
    expect(listed["presets"]).toHaveLength(0);
  });

  it("roda o preset gravado na TV", async () => {
    await api("/api/record/start", {
      method: "POST",
      body: JSON.stringify({ target: "preset", service_id: "max" }),
    });
    await api("/api/button", { method: "POST", body: JSON.stringify({ name: "DOWN" }) });
    await api("/api/record/stop", {
      method: "POST",
      body: JSON.stringify({ label: "Dublado" }),
    });
    // O socket de input não confirma nada: medir antes da entrega daria zero.
    await expect.poll(() => tv.buttons.length).toBe(1);

    await api("/api/presets/max/dublado/run", { method: "POST" });
    await state.runner.settle();

    await expect.poll(() => tv.buttons.length).toBe(2);
  });
});

describe("busca", () => {
  it("desliga a busca quando não há token", async () => {
    const error = await api("/api/search?q=fallout").catch((e: unknown) => e);

    expect((error as ApiError).status).toBe(503);
  });
});

describe("pareamento pela própria tela", () => {
  it("lista as TVs que a varredura achou", async () => {
    const port = Number(new URL(tv.url).port);
    const paired = await AppState.create({
      store: new MemoryStore(),
      bundledCatalog: CATALOG,
      scan: async () => [{ host: "127.0.0.1", port }],
    });
    const found = (await createRouter(paired)("/api/pair/scan", { method: "POST" })) as {
      candidates: { host: string }[];
    };

    expect(found.candidates).toEqual([{ host: "127.0.0.1", port }]);
    await paired.close();
  });

  it("explica que a varredura precisa do app nativo", async () => {
    const fresh = await AppState.create({ store: new MemoryStore(), bundledCatalog: CATALOG });
    const error = await createRouter(fresh)("/api/pair/scan", { method: "POST" }).catch(
      (e: unknown) => e,
    );

    expect((error as ApiError).status).toBe(503);
    await fresh.close();
  });

  it("pareia do zero e guarda a chave", async () => {
    // O caminho que antes exigia voltar ao PC e rodar `lgremote pair`.
    await tv.stop();
    tv = new FakeTv({ promptAnswer: "accept", grantedKey: "chave-do-pareamento" });
    await tv.start();
    const port = Number(new URL(tv.url).port);

    const virgin = new MemoryStore();
    const fresh = await AppState.create({ store: virgin, bundledCatalog: CATALOG });
    expect(fresh.paired).toBe(false);

    const result = (await createRouter(fresh)("/api/pair", {
      method: "POST",
      body: JSON.stringify({ host: "127.0.0.1", port }),
    })) as Record<string, unknown>;

    expect(result["status"]).toBe("ok");
    // A chave foi para o cofre, não para as preferências: é o que sobrevive à
    // re-assinatura de 7 dias do SideStore.
    expect(await virgin.getClientKey()).toBe("chave-do-pareamento");
    expect(fresh.paired).toBe(true);
    await fresh.close();
  });

  it("devolve 409 com o conselho certo quando a TV recusa sozinha", async () => {
    await tv.stop();
    tv = new FakeTv({ instantRefusal: true });
    await tv.start();
    const port = Number(new URL(tv.url).port);

    const fresh = await AppState.create({ store: new MemoryStore(), bundledCatalog: CATALOG });
    const error = await createRouter(fresh)("/api/pair", {
      method: "POST",
      body: JSON.stringify({ host: "127.0.0.1", port }),
    }).catch((e: unknown) => e);

    expect((error as ApiError).status).toBe(409);
    // Recusa sem pedido na tela: o conselho é resolver o standby, não tentar de novo.
    expect((error as ApiError).message).toMatch(/standby/);
    await fresh.close();
  });

  it("esquece a TV a pedido", async () => {
    await api("/api/forget", { method: "POST" });

    expect(await store.getClientKey()).toBeNull();
    expect(state.paired).toBe(false);
  });
});

describe("ajustes", () => {
  it("liga a busca ao salvar o token e nunca o devolve", async () => {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ tmdb_token: "0123456789abcdef0123456789abcdef" }),
    });

    const settings = (await api("/api/settings")) as Record<string, unknown>;
    expect(settings["has_token"]).toBe(true);
    expect(JSON.stringify(settings)).not.toContain("0123456789abcdef");

    const config = (await api("/api/config")) as Record<string, unknown>;
    expect(config["catalog_enabled"]).toBe(true);
  });

  it("habilita ligar a TV quando há MAC e Wake-on-LAN", async () => {
    const woken: string[] = [];
    const withWol = await AppState.create({
      store: new MemoryStore({ [KEYS.tvMac]: "aa:bb:cc:11:22:33" }),
      bundledCatalog: CATALOG,
      wake: async (mac) => void woken.push(mac),
    });
    const wolApi = createRouter(withWol);

    expect(((await wolApi("/api/config")) as Record<string, unknown>)["can_wake"]).toBe(true);
    await wolApi("/api/power/on", { method: "POST" });

    expect(woken).toEqual(["aa:bb:cc:11:22:33"]);
    await withWol.close();
  });

  it("recusa ligar a TV sem MAC, explicando o porquê", async () => {
    const error = await api("/api/power/on", { method: "POST" }).catch((e: unknown) => e);

    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).message).toMatch(/MAC/);
  });
});

describe("roteamento", () => {
  it("recusa rota que não existe", async () => {
    const error = await api("/api/inventado").catch((e: unknown) => e);
    expect((error as ApiError).status).toBe(404);
  });
});
