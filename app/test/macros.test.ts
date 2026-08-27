/** Parsing e execução das macros do apps.yaml. */

import { describe, expect, it } from "vitest";

import { UnknownButtonError } from "../src/tv/buttons.ts";
import {
  MAX_REPEAT,
  MacroError,
  parseSteps,
  render,
  repeatCount,
  runSteps,
  stepToYaml,
  type ButtonStep,
} from "../src/tv/macros.ts";
import { FakeTarget, instantSleep } from "./helpers.ts";

describe("parse", () => {
  it("reconhece cada tipo de passo", () => {
    const steps = parseSteps([
      { button: "left", times: 3, delay: 0.1 },
      { wait: 2 },
      { text: "{title}" },
      { enter: true },
      { clear: 40 },
    ]);

    expect(steps).toEqual([
      { kind: "button", name: "LEFT", times: 3, delay: 0.1 },
      { kind: "wait", seconds: 2 },
      { kind: "text", template: "{title}" },
      { kind: "enter" },
      { kind: "clear", count: 40 },
    ]);
  });

  it("dá erro claro em passo sem ação", () => {
    expect(() => parseSteps([{ delay: 1 }])).toThrow(/sem ação/);
  });

  it("rejeita botão inválido já no parse", () => {
    // Melhor quebrar ao carregar o YAML do que no meio de uma macro rodando.
    expect(() => parseSteps([{ button: "SUPER_SECRETO" }])).toThrow(UnknownButtonError);
  });

  it.each([
    { button: "UP", times: 0 },
    { button: "UP", times: 99 },
  ])("rejeita repetição absurda (%o)", (bad) => {
    expect(() => parseSteps([bad])).toThrow(/times/);
  });

  it("rejeita wait gigante", () => {
    expect(() => parseSteps([{ wait: 600 }])).toThrow(/wait/);
  });

  it("rejeita delay que não é número em vez de virar zero", () => {
    // `Number(null)` é 0 em JavaScript: sem a checagem, YAML torto viraria delay zero.
    expect(() => parseSteps([{ button: "UP", delay: null }])).toThrow(/delay/);
    expect(() => parseSteps([{ button: "UP", delay: "rápido" }])).toThrow(/delay/);
  });
});

describe("render", () => {
  it("substitui placeholders", () => {
    expect(render("{title} ({year})", { title: "Fallout", year: "2024" })).toBe("Fallout (2024)");
  });

  it("não quebra com chaves no título", () => {
    // Interpolação de verdade explodiria aqui; títulos do TMDb são texto arbitrário.
    expect(render("{title}", { title: "Tudo {sic} Bem" })).toBe("Tudo {sic} Bem");
  });

  it("ignora placeholder desconhecido", () => {
    expect(render("{title} {nada}", { title: "X" })).toBe("X {nada}");
  });
});

describe("runSteps", () => {
  it("executa na ordem e devolve o rastro", async () => {
    const target = new FakeTarget();
    const sleep = instantSleep();
    const steps = parseSteps([
      { button: "LEFT", times: 2 },
      { wait: 1.5 },
      { text: "{title}" },
      { enter: true },
    ]);

    const trace = await runSteps(target, steps, { title: "Severance" }, { sleep });

    expect(target.buttons).toEqual(["LEFT", "LEFT"]);
    expect(target.texts).toEqual([{ text: "Severance", replace: true }]);
    expect(target.enters).toBe(1);
    expect(trace).toEqual([
      "button LEFT",
      "button LEFT",
      "wait 1.5s",
      "text 'Severance'",
      "enter",
    ]);
  });

  it("espera entre repetições mas não depois da última", async () => {
    // Um sleep sobrando ao fim de cada repetição somaria segundos à macro inteira.
    const sleep = instantSleep();
    const steps = parseSteps([{ button: "UP", times: 3, delay: 0.2 }]);

    await runSteps(new FakeTarget(), steps, {}, { sleep });

    expect(sleep.waits).toEqual([0.2, 0.2]);
  });

  it("entrega cada passo ao vivo pro onStep", async () => {
    const seen: string[] = [];
    const steps = parseSteps([{ button: "UP" }, { text: "oi" }]);

    await runSteps(new FakeTarget(), steps, {}, { sleep: instantSleep(), onStep: (e) => seen.push(e) });

    expect(seen).toEqual(["button UP", "text 'oi'"]);
  });
});

describe("times variável (é assim que se chega a um episódio)", () => {
  const variable = (): ButtonStep => {
    const [step] = parseSteps([{ button: "RIGHT", times: "{episode_index}" }]);
    return step as ButtonStep;
  };

  it("aceita template", () => {
    expect(variable()).toEqual({
      kind: "button",
      name: "RIGHT",
      times: "{episode_index}",
      delay: 0.2,
    });
  });

  it.each([
    ["0", 0],
    ["4", 4],
    ["30", 30],
  ])("resolve %s com o contexto", (episodeIndex, expected) => {
    expect(repeatCount(variable(), { episode_index: episodeIndex })).toBe(expected);
  });

  it("limita em vez de disparar 400 toques", () => {
    // Contexto errado não pode virar uma enxurrada de comandos na TV.
    expect(repeatCount(variable(), { episode_index: "999" })).toBe(MAX_REPEAT);
  });

  it("trata negativo como zero", () => {
    expect(repeatCount(variable(), { episode_index: "-3" })).toBe(0);
  });

  it("dá erro claro quando não resolve para número", () => {
    const [step] = parseSteps([{ button: "RIGHT", times: "{titulo}" }]);
    expect(() => repeatCount(step as ButtonStep, { titulo: "Fallout" })).toThrow(MacroError);
    expect(() => repeatCount(step as ButtonStep, { titulo: "Fallout" })).toThrow(/não é um número/);
  });

  it("executa a quantidade resolvida", async () => {
    const target = new FakeTarget();
    const steps = parseSteps([{ button: "RIGHT", times: "{episode_index}" }]);

    await runSteps(target, steps, { episode_index: "3" }, { sleep: instantSleep() });

    expect(target.buttons).toEqual(["RIGHT", "RIGHT", "RIGHT"]);
  });

  it("não aperta nada quando o índice é zero", async () => {
    // Episódio 1 = índice 0: o passo existe, mas não deve mover o foco.
    const target = new FakeTarget();
    const steps = parseSteps([{ button: "RIGHT", times: "{episode_index}" }]);

    await runSteps(target, steps, { episode_index: "0" }, { sleep: instantSleep() });

    expect(target.buttons).toEqual([]);
  });
});

describe("stepToYaml", () => {
  it("mantém o estilo compacto de uma linha do apps.yaml", () => {
    const steps = parseSteps([
      { button: "UP" },
      { button: "UP", times: 3 },
      { button: "UP", times: "{episode_index}" },
      { button: "UP", delay: 0.5 },
      { wait: 2 },
      { text: "{title}" },
      { enter: true },
      { clear: 40 },
    ]);

    expect(steps.map(stepToYaml)).toEqual([
      "{button: UP}",
      "{button: UP, times: 3}",
      "{button: UP, times: '{episode_index}'}",
      "{button: UP, delay: 0.5}",
      "{wait: 2}",
      '{text: "{title}"}',
      "{enter: true}",
      "{clear: 40}",
    ]);
  });
});
