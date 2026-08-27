/** Catálogo de serviços, casamento com os apps da TV e reescrita do apps.yaml. */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  MacroWriteError,
  ServiceConfigError,
  applyDiscoveries,
  canSearch,
  loadCatalog,
  matchInstalledApps,
  parseCatalog,
  writeMacro,
} from "../src/tv/apps.ts";
import type { ButtonStep, Step } from "../src/tv/macros.ts";

const button = (name: string, times: number | string = 1, delay = 0.2): ButtonStep => ({
  kind: "button",
  name,
  times,
  delay,
});

const SAMPLE = {
  services: [
    {
      id: "max",
      label: "Max",
      tmdb_names: ["Max", "Max Amazon Channel"],
      match: ["max", "hbo"],
      app_id: "com.wbd.stream",
      wait_after_launch: 12,
      search: [{ button: "LEFT" }, { text: "{title}" }],
    },
    {
      id: "netflix",
      label: "Netflix",
      tmdb_names: ["Netflix"],
      match: ["netflix"],
      app_id: "netflix",
    },
  ],
  shortcuts: ["netflix", "max", "inexistente"],
};

describe("catálogo", () => {
  it("carrega o apps.yaml de verdade do repositório", () => {
    // Se o arquivo de configuração real quebrar, nada sobe.
    const text = readFileSync("../config/apps.yaml", "utf-8");
    const catalog = loadCatalog(text);
    expect(catalog.services.length).toBeGreaterThan(0);
    expect(catalog.services.every((s) => s.appId)).toBe(true);
  });

  it("ignora atalho de serviço inexistente", () => {
    const catalog = parseCatalog(SAMPLE);
    expect(catalog.shortcutServices().map((s) => s.id)).toEqual(["netflix", "max"]);
  });

  it("recusa serviço sem app_id com dica de como resolver", () => {
    expect(() => parseCatalog({ services: [{ id: "x", label: "X" }] })).toThrow(
      ServiceConfigError,
    );
    expect(() => parseCatalog({ services: [{ id: "x", label: "X" }] })).toThrow(/Procurar apps/);
  });

  it("diferencia quem tem macro de busca", () => {
    const catalog = parseCatalog(SAMPLE);
    expect(canSearch(catalog.byId("max")!)).toBe(true);
    expect(canSearch(catalog.byId("netflix")!)).toBe(false);
  });

  it.each([
    ["Max", "max"],
    ["Max Amazon Channel", "max"],
    ["HBO Max", "max"], // rótulo antigo, cai no match por termo
    ["Netflix", "netflix"],
    ["Netflix basic with Ads", "netflix"],
    ["Paramount Plus", null],
  ])("casa o provedor %s do TMDb com o serviço %s", (provider, expected) => {
    // O TMDb muda o rótulo do provedor com frequência; o casamento precisa aguentar.
    const catalog = parseCatalog(SAMPLE);
    expect(catalog.byTmdbProvider(provider)?.id ?? null).toBe(expected);
  });

  it("sobe wait_before_profile quando ele é menor que a carga do app", () => {
    // O ENTER do perfil num app ainda subindo some em silêncio — era o caso do Max.
    const catalog = parseCatalog({
      services: [
        {
          id: "max",
          app_id: "x",
          wait_after_launch: 12,
          wait_before_profile: 5,
          profile: [{ button: "ENTER" }],
        },
      ],
    });
    expect(catalog.byId("max")!.waitBeforeProfile).toBe(12);
  });
});

describe("descoberta contra a TV", () => {
  it("usa o id exato quando ele existe", () => {
    const catalog = parseCatalog(SAMPLE);
    const { found, missing } = matchInstalledApps(catalog, [
      { id: "com.wbd.stream", title: "Max" },
      { id: "netflix", title: "Netflix" },
    ]);
    expect(missing).toEqual([]);
    expect(found.every((d) => !d.changed)).toBe(true);
  });

  it("corrige id divergente pelo título", () => {
    // IDs de app variam por região e ano do modelo — por isso lemos da TV.
    const catalog = parseCatalog(SAMPLE);
    const { found, missing } = matchInstalledApps(catalog, [
      { id: "com.wbd.stream.latam", title: "Max" },
      { id: "netflix", title: "Netflix" },
    ]);
    const changed = Object.fromEntries(
      found.filter((d) => d.changed).map((d) => [d.serviceId, d.appId]),
    );
    expect(changed).toEqual({ max: "com.wbd.stream.latam" });
    expect(missing).toEqual([]);
  });

  it("reporta app não instalado", () => {
    const catalog = parseCatalog(SAMPLE);
    const { found, missing } = matchInstalledApps(catalog, [{ id: "netflix", title: "Netflix" }]);
    expect(missing.map((s) => s.id)).toEqual(["max"]);
    expect(found.map((d) => d.serviceId)).toEqual(["netflix"]);
  });

  it("preserva comentários ao aplicar as descobertas", () => {
    // Os comentários do apps.yaml são a documentação de como calibrar.
    const original = [
      "# comentário do topo",
      "services:",
      "  - id: max",
      "    label: Max",
      "    # este comentário precisa sobreviver",
      "    app_id: com.wbd.stream",
      "  - id: netflix",
      "    label: Netflix",
      "    app_id: netflix",
      "",
    ].join("\n");

    const catalog = loadCatalog(original);
    const { found } = matchInstalledApps(catalog, [
      { id: "com.wbd.stream.latam", title: "Max" },
      { id: "netflix", title: "Netflix" },
    ]);

    const { text, applied } = applyDiscoveries(
      original,
      found.filter((d) => d.changed),
    );

    expect(applied).toEqual(["max -> com.wbd.stream.latam"]);
    expect(text).toContain("# comentário do topo");
    expect(text).toContain("# este comentário precisa sobreviver");
    expect(text).toContain("app_id: com.wbd.stream.latam");
    expect(text).toContain("app_id: netflix"); // o outro serviço não foi tocado
  });
});

/**
 * Gravar uma macro de volta no apps.yaml.
 *
 * O arquivo é do usuário e é cheio de comentários que explicam como calibrar. Perdê-los
 * numa regravação seria pior que não ter a funcionalidade — daí o peso destes testes.
 */
describe("writeMacro", () => {
  const YAML = `# Cabeçalho que explica como calibrar as macros.
services:
  - id: max
    label: Max
    app_id: com.wbd.stream
    wait_after_launch: 12
    # Tela "Quem está assistindo?" — este comentário não pode sumir.
    profile:
      - {button: ENTER}
    wait_before_profile: 14
    search:
      - {wait: 3}
      - {button: LEFT, times: 4, delay: 0.25}
      - {text: "{title}"}
    episode: []
    wait_before_episode: 6

  - id: netflix
    label: Netflix
    app_id: netflix
    search:
      - {button: UP}

shortcuts: [max, netflix]
`;

  const stepsOf = (text: string, serviceId: string, key: "search" | "profile" | "episode"): Step[] =>
    loadCatalog(text).byId(serviceId)![key];

  it("grava a macro e ela volta pelo parser", () => {
    const text = writeMacro(YAML, "max", "search", [
      button("DOWN", 3),
      { kind: "text", template: "{title}" },
    ]);

    expect(stepsOf(text, "max", "search")).toEqual([
      button("DOWN", 3),
      { kind: "text", template: "{title}" },
    ]);
  });

  it("preserva os comentários", () => {
    const text = writeMacro(YAML, "max", "search", [button("DOWN")]);

    expect(text).toContain("# Cabeçalho que explica como calibrar as macros.");
    expect(text).toContain('# Tela "Quem está assistindo?" — este comentário não pode sumir.');
  });

  it("não engole as chaves seguintes", () => {
    // Parar na próxima chave do mesmo nível é o que impede comer o resto do serviço.
    const text = writeMacro(YAML, "max", "profile", [button("RIGHT"), button("ENTER")]);

    const service = loadCatalog(text).byId("max")!;
    expect(service.waitBeforeProfile).toBe(14);
    expect(service.waitAfterLaunch).toBe(12);
    expect(service.search.length).toBeGreaterThan(0);
  });

  it("substitui lista vazia escrita em uma linha só", () => {
    // `episode: []` é uma linha só; sem tratar isso, a lista nova ficaria órfã embaixo.
    const text = writeMacro(YAML, "max", "episode", [button("DOWN", 2)]);

    expect(stepsOf(text, "max", "episode")).toEqual([button("DOWN", 2)]);
    expect(text).not.toContain("episode: []");
  });

  it("não vaza para o próximo serviço", () => {
    const text = writeMacro(YAML, "netflix", "search", [button("LEFT")]);

    expect(stepsOf(text, "netflix", "search")).toEqual([button("LEFT")]);
    expect(stepsOf(text, "max", "search").length).toBeGreaterThan(0);
  });

  it("lida com o último serviço do arquivo", () => {
    // Sem outra chave depois, o bloco termina no fim do serviço — não do arquivo.
    const text = writeMacro(YAML, "netflix", "search", [button("DOWN"), { kind: "wait", seconds: 1 }]);

    expect(text).toContain("shortcuts: [max, netflix]");
  });

  it("preserva a indentação", () => {
    const lines = writeMacro(YAML, "max", "search", [button("DOWN")]).split("\n");
    const start = lines.indexOf("    search:");

    expect(lines[start + 1]).toBe("      - {button: DOWN}");
  });

  it("não mexe no texto original", () => {
    // A versão que veio embutida no app é o caminho de volta.
    const before = YAML;
    writeMacro(YAML, "max", "search", [button("DOWN")]);
    expect(YAML).toBe(before);
  });

  it("recusa serviço inexistente", () => {
    expect(() => writeMacro(YAML, "hulu", "search", [button("DOWN")])).toThrow(/não existe/);
  });

  it("recusa chave ausente em vez de inventar onde pôr", () => {
    const minimal = "services:\n  - id: max\n    app_id: x\n";
    expect(() => writeMacro(minimal, "max", "search", [button("DOWN")])).toThrow(/Chave/);
  });

  it("recusa chave fora da lista de macros", () => {
    expect(() => writeMacro(YAML, "max", "wait_after_launch", [button("DOWN")])).toThrow(
      MacroWriteError,
    );
  });

  it("recusa gravação vazia", () => {
    expect(() => writeMacro(YAML, "max", "search", [])).toThrow(/nenhum passo/);
  });

  it("mantém o template de times na ida e volta", () => {
    // Sem aspas, `times: {episode_index}` viraria um mapa YAML em vez de texto.
    const text = writeMacro(YAML, "max", "episode", [button("RIGHT", "{episode_index}")]);

    expect(stepsOf(text, "max", "episode")).toEqual([button("RIGHT", "{episode_index}")]);
  });
});
