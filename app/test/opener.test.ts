/** Camadas de abertura de título: deep link -> macro -> só abrir. */

import { describe, expect, it } from "vitest";

import { parseCatalog, type Service } from "../src/tv/apps.ts";
import { TitleOpener } from "../src/tv/opener.ts";
import { FakeSession, instantSleep } from "./helpers.ts";

const PROFILE_MACRO = [{ button: "ENTER" }];

function buildService(overrides: Record<string, unknown> = {}): Service {
  const catalog = parseCatalog({
    services: [
      {
        id: "max",
        label: "Max",
        app_id: "com.wbd.stream",
        wait_after_launch: 12,
        search: [{ wait: 2 }, { button: "LEFT", times: 2 }, { text: "{title}" }],
        ...overrides,
      },
    ],
  });
  return catalog.byId("max")!;
}

describe("camadas", () => {
  it("usa a macro como camada padrão", async () => {
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(buildService(), "The Last of Us");

    expect(result.strategy).toBe("macro");
    expect(tv.launches).toEqual(["com.wbd.stream"]);
    expect(tv.buttons).toEqual(["LEFT", "LEFT"]);
    expect(tv.texts).toEqual([{ text: "The Last of Us", replace: true }]);
  });

  it("espera o app carregar antes de digitar", async () => {
    // Teclas mandadas antes do app abrir se perdem — é a falha nº 1 dessas macros.
    const sleep = instantSleep();
    const opener = new TitleOpener(new FakeSession(), { sleep });

    await opener.openTitle(buildService(), "Fallout");

    // wait_after_launch vem antes de qualquer passo da macro.
    expect(sleep.waits[0]).toBe(12);
  });

  it("prefere o deep link quando ele está calibrado", async () => {
    const tv = new FakeSession();
    const service = buildService({ content_target: "https://play.max.com/t/{tmdb_id}" });
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(service, "Fallout", { tmdbId: "106379" });

    expect(result.strategy).toBe("deep_link");
    expect(tv.launchParams).toEqual([
      { appId: "com.wbd.stream", params: { contentTarget: "https://play.max.com/t/106379" } },
    ]);
    expect(tv.buttons).toEqual([]);
  });

  it("ao menos abre o app quando não há macro", async () => {
    // Falhar em silêncio seria pior: o usuário fica olhando a TV parada.
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(buildService({ search: [] }), "Fallout");

    expect(result.strategy).toBe("launch");
    expect(tv.launches).toEqual(["com.wbd.stream"]);
  });

  it("openService não busca nada", async () => {
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openService(buildService());

    expect(result.strategy).toBe("launch");
    expect(result.title).toBeNull();
    expect(tv.buttons).toEqual([]);
    expect(tv.texts).toEqual([]);
  });
});

/*
 * Max e Disney+ mostram "Quem está assistindo?" toda vez que o app abre do zero (é
 * por design para perfil adulto). Sem tratá-la, a macro de busca digita no vazio.
 */
describe("tela de perfil", () => {
  it("passa pelo perfil na abertura fria", async () => {
    const tv = new FakeSession();
    tv.current = "com.webos.app.livetv"; // Max fechado
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    await opener.openTitle(buildService({ profile: PROFILE_MACRO }), "Fallout");

    // ENTER confirma o perfil ANTES da navegação da busca.
    expect(tv.buttons).toEqual(["ENTER", "LEFT", "LEFT"]);
  });

  it("ainda passa pelo perfil com o app já em primeiro plano", async () => {
    /*
     * Este teste trava o bug ao contrário: a premissa antiga era "app em primeiro
     * plano = tela de perfil já resolvida". Ela é falsa justamente no caso que
     * importa: parado NO seletor de perfis, o app em primeiro plano já é o Max. O
     * tratamento era então desligado exatamente quando era necessário, e a macro de
     * busca rodava em cima do seletor — bastava falhar uma vez para nunca mais sair
     * de lá. Agora o app é fechado antes de abrir, e o perfil sempre é tratado.
     */
    const tv = new FakeSession();
    tv.current = "com.wbd.stream"; // parado na tela de perfil do Max
    const service = buildService({ profile: PROFILE_MACRO });
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(service, "Fallout");

    expect(tv.buttons).toEqual(["ENTER", "LEFT", "LEFT"]);
    expect(result.trace).toContain(`close ${service.appId}`);
  });

  it("fecha o app antes de abrir quando há macro de perfil", async () => {
    // Fechar é o que dá controle sobre a tela em que a TV está, em vez de adivinhar.
    const tv = new FakeSession();
    tv.current = "com.wbd.stream";
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    await opener.openService(buildService({ profile: PROFILE_MACRO }));

    expect(tv.calls.filter((c) => c === "close" || c === "launch")).toEqual([
      "close",
      "launch",
    ]);
  });

  it("espera a tela de perfil aparecer", async () => {
    // O ENTER antes da tela desenhar se perde — mesma causa das macros instáveis.
    const sleep = instantSleep();
    const service = buildService({ profile: PROFILE_MACRO, wait_before_profile: 14 });
    const opener = new TitleOpener(new FakeSession(), { sleep });

    await opener.openTitle(service, "Fallout");

    expect(sleep.waits).toContain(14);
  });

  it("nunca espera menos que a carga do app", () => {
    // Configurar 8s de perfil num app que precisa de 12s manda o ENTER num app que
    // ainda está subindo — e o toque some sem erro nenhum. Era o caso do Max.
    const service = buildService({
      profile: PROFILE_MACRO,
      wait_after_launch: 12,
      wait_before_profile: 8,
    });

    expect(service.waitBeforeProfile).toBe(12);
  });

  it("para de perguntar assim que o app aparece em primeiro plano", async () => {
    // Medir em vez de chutar: o `launch` do dublê já põe o app em primeiro plano.
    const tv = new FakeSession();
    const service = buildService({ profile: PROFILE_MACRO, foreground_timeout: 20 });
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openService(service);

    expect(result.trace).toContain("app em primeiro plano");
    // Uma consulta só: sem parar cedo, seriam 40 (20s / 0,5s) esperas à toa.
    expect(tv.currentAppQueries).toBe(1);
  });

  it("trata o perfil também no atalho do app", async () => {
    // Tocar em 'Max' na tela inicial não pode deixar você parado no seletor de perfil.
    const tv = new FakeSession();
    tv.current = "com.webos.app.livetv";
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    await opener.openService(buildService({ profile: PROFILE_MACRO }));

    expect(tv.buttons).toEqual(["ENTER"]);
  });

  it("não muda nada quando não há macro de perfil", async () => {
    const tv = new FakeSession();
    tv.current = "com.webos.app.livetv";
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    await opener.openTitle(buildService(), "Fallout"); // sem `profile`

    expect(tv.buttons).toEqual(["LEFT", "LEFT"]);
    expect(tv.closes).toEqual([]);
  });

  it("aceita perfil que precisa de vários botões", async () => {
    // Perfil que não é o primeiro da fila: RIGHT até ele, depois ENTER.
    const tv = new FakeSession();
    tv.current = "com.webos.app.livetv";
    const service = buildService({
      profile: [{ button: "RIGHT", times: 2 }, { button: "ENTER" }],
    });
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    await opener.openService(service);

    expect(tv.buttons).toEqual(["RIGHT", "RIGHT", "ENTER"]);
  });

  it("não aborta a abertura quando não dá pra ler o app atual", async () => {
    // Não saber em que app a TV está é motivo para cair no tempo fixo, não desistir.
    const tv = new FakeSession();
    tv.currentAppFails = true;
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openService(buildService({ profile: PROFILE_MACRO }));

    expect(result.trace).not.toContain("app em primeiro plano");
    expect(tv.buttons).toEqual(["ENTER"]);
  });
});

describe("episódio", () => {
  const withEpisode = () =>
    buildService({ episode: [{ button: "RIGHT", times: "{episode_index}" }, { enter: true }] });

  it("navega até o episódio pedido", async () => {
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(withEpisode(), "Severance", {
      season: 1,
      episode: 4,
    });

    expect(result.strategy).toBe("episode");
    // Episódio 4 = três RIGHT a partir do primeiro, depois ENTER.
    expect(tv.buttons).toEqual(["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"]);
    expect(tv.enters).toBe(1);
  });

  it("fica na página da série quando não há episódio pedido", async () => {
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(withEpisode(), "Severance");

    expect(result.strategy).toBe("macro");
    expect(tv.enters).toBe(0);
  });

  it("fica na página da série quando o serviço não tem macro de episódio", async () => {
    // Nunca pior que o estado atual: a série aberta já é onde o fluxo chegava antes.
    const tv = new FakeSession();
    const opener = new TitleOpener(tv, { sleep: instantSleep() });

    const result = await opener.openTitle(buildService(), "Severance", { episode: 4 });

    expect(result.strategy).toBe("macro");
  });
});
