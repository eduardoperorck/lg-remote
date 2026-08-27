/** Presets gravados: slug, roundtrip e limites. */

import { describe, expect, it } from "vitest";

import {
  MAX_PRESETS_PER_SERVICE,
  PresetError,
  PresetStore,
  parseStore,
  slugify,
  storeToData,
  type Preset,
} from "../src/tv/presets.ts";
import type { Step } from "../src/tv/macros.ts";

const STEPS: Step[] = [
  { kind: "button", name: "DOWN", times: 2, delay: 0.2 },
  { kind: "wait", seconds: 1.5 },
  { kind: "button", name: "ENTER", times: 1, delay: 0.2 },
];

const preset = (label: string, serviceId = "max"): Preset => ({
  id: slugify(label),
  label,
  serviceId,
  steps: STEPS,
  recordedAt: "2026-08-10 21:00",
});

describe("slugify", () => {
  it.each([
    ["Dublado", "dublado"],
    ["Legenda PT + áudio original", "legenda-pt-audio-original"],
    ["  Áudio Inglês  ", "audio-ingles"],
    ["!!!", "preset"], // sobrou nada utilizável
  ])("%s -> %s", (label, expected) => {
    expect(slugify(label)).toBe(expected);
  });
});

describe("PresetStore", () => {
  it("trata ausência de dados como caixa vazia", () => {
    // Antes da primeira gravação não existe nada salvo — não é erro.
    expect(parseStore(null).allPresets()).toEqual([]);
  });

  it("preserva os passos na ida e volta", () => {
    const store = new PresetStore();
    store.put(preset("Dublado"));

    const saved = parseStore(storeToData(store)).get("max", "dublado");

    expect(saved?.label).toBe("Dublado");
    expect(saved?.steps).toEqual(STEPS);
    expect(saved?.recordedAt).toBe("2026-08-10 21:00");
  });

  it("substitui ao regravar com o mesmo nome", () => {
    const store = new PresetStore();
    store.put(preset("Dublado"));
    store.put({ ...preset("Dublado"), steps: [{ kind: "button", name: "UP", times: 1, delay: 0.2 }] });

    expect(store.forService("max")).toHaveLength(1);
    expect(store.get("max", "dublado")?.steps).toEqual([
      { kind: "button", name: "UP", times: 1, delay: 0.2 },
    ]);
  });

  it("mantém presets separados por serviço", () => {
    const store = new PresetStore();
    store.put(preset("Dublado", "max"));
    store.put(preset("Dublado", "disney"));

    expect(store.forService("max")).toHaveLength(1);
    expect(store.forService("disney")).toHaveLength(1);
    expect(store.allPresets()).toHaveLength(2);
  });

  it("aplica o limite por serviço", () => {
    const store = new PresetStore();
    for (let index = 0; index < MAX_PRESETS_PER_SERVICE; index += 1) {
      store.put(preset(`Preset ${index}`));
    }
    expect(() => store.put(preset("Estouro"))).toThrow(PresetError);
  });

  it("diz se o preset existia ao remover", () => {
    const store = new PresetStore();
    store.put(preset("Dublado"));

    expect(store.remove("max", "dublado")).toBe(true);
    expect(store.remove("max", "dublado")).toBe(false);
    expect(store.remove("inexistente", "x")).toBe(false);
  });

  it("dá erro claro em dado malformado", () => {
    expect(() => parseStore({ max: "nao-e-lista" } as never)).toThrow(/lista/);
  });

  it("não grava serviço que ficou vazio", () => {
    // Apagar o último preset não deve deixar uma chave órfã.
    const store = new PresetStore();
    store.put(preset("Dublado"));
    store.remove("max", "dublado");

    const data = storeToData(store);
    expect(parseStore(data).allPresets()).toEqual([]);
    expect(Object.keys(data)).not.toContain("max");
  });
});
