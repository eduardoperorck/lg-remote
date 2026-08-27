/**
 * Presets: sequências de botões gravadas por você, salvas por serviço.
 *
 * Existem porque não há API para legenda/áudio dentro do Max ou Disney+ — o menu é
 * desenhado pelo próprio app e só responde a botões. Em vez de chutar a sequência e
 * você calibrar no YAML, você grava navegando como sempre faz.
 *
 * Diferente do apps.yaml, isto aqui é escrito pela máquina: mora no armazenamento do
 * app como JSON, sem comentários para preservar.
 */

import { parseSteps, type Step } from "./macros.ts";

export const MAX_PRESETS_PER_SERVICE = 20;

export class PresetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PresetError";
  }
}

/** 'Legenda PT + áudio original' -> 'legenda-pt-audio-original'. */
export function slugify(label: string): string {
  const asciiOnly = label
    .normalize("NFKD")
    // Mesmo efeito do `encode("ascii", "ignore")` do Python: o que sobrou de acento
    // depois da decomposição sai fora, e "ç" já virou "c".
    .replace(/[^\x00-\x7F]/g, "")
    .toLowerCase();
  const slug = asciiOnly.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug || "preset";
}

/** Uma sequência nomeada, pertencente a um serviço. */
export interface Preset {
  id: string;
  label: string;
  serviceId: string;
  steps: Step[];
  recordedAt?: string;
}

/** Serializa um passo no mesmo formato que o `apps.yaml` usa. */
export function stepToData(step: Step): Record<string, unknown> {
  switch (step.kind) {
    case "button": {
      const data: Record<string, unknown> = { button: step.name };
      if (step.times !== 1) data["times"] = step.times;
      if (step.delay !== 0.2) data["delay"] = step.delay;
      return data;
    }
    case "wait":
      return { wait: step.seconds };
    case "text":
      return { text: step.template };
    case "enter":
      return { enter: true };
    case "clear":
      return { clear: step.count };
  }
}

export function presetToData(preset: Preset): Record<string, unknown> {
  const data: Record<string, unknown> = {
    id: preset.id,
    label: preset.label,
    steps: preset.steps.map(stepToData),
  };
  if (preset.recordedAt) data["recorded_at"] = preset.recordedAt;
  return data;
}

/** Todos os presets, agrupados por serviço. */
export class PresetStore {
  constructor(readonly byService: Map<string, Preset[]> = new Map()) {}

  forService(serviceId: string): Preset[] {
    return [...(this.byService.get(serviceId) ?? [])];
  }

  get(serviceId: string, presetId: string): Preset | null {
    return this.forService(serviceId).find((p) => p.id === presetId) ?? null;
  }

  allPresets(): Preset[] {
    return [...this.byService.values()].flat();
  }

  /** Adiciona ou substitui. Regravar com o mesmo nome sobrescreve, que é o esperado. */
  put(preset: Preset): void {
    let presets = this.byService.get(preset.serviceId);
    if (!presets) {
      presets = [];
      this.byService.set(preset.serviceId, presets);
    }
    const index = presets.findIndex((existing) => existing.id === preset.id);
    if (index >= 0) {
      presets[index] = preset;
      return;
    }
    if (presets.length >= MAX_PRESETS_PER_SERVICE) {
      throw new PresetError(
        `Limite de ${MAX_PRESETS_PER_SERVICE} presets para ${preset.serviceId}. ` +
          "Apague algum antes de gravar outro.",
      );
    }
    presets.push(preset);
  }

  remove(serviceId: string, presetId: string): boolean {
    const presets = this.byService.get(serviceId);
    if (!presets || presets.length === 0) return false;
    const remaining = presets.filter((p) => p.id !== presetId);
    if (remaining.length === presets.length) return false;
    this.byService.set(serviceId, remaining);
    return true;
  }
}

export function parseStore(data: Record<string, unknown> | null | undefined): PresetStore {
  const byService = new Map<string, Preset[]>();
  for (const [serviceId, rawPresets] of Object.entries(data ?? {})) {
    if (!Array.isArray(rawPresets)) {
      throw new PresetError(`'${serviceId}' deveria ser uma lista de presets`);
    }
    const presets: Preset[] = rawPresets.map((entry) => {
      if (typeof entry !== "object" || entry === null || !("id" in entry)) {
        throw new PresetError(`preset sem 'id' em ${serviceId}: ${JSON.stringify(entry)}`);
      }
      const raw = entry as Record<string, unknown>;
      const preset: Preset = {
        id: String(raw["id"]),
        label: String(raw["label"] ?? raw["id"]),
        serviceId: String(serviceId),
        steps: parseSteps(raw["steps"] as Record<string, unknown>[] | undefined),
      };
      const recordedAt = raw["recorded_at"];
      if (typeof recordedAt === "string" && recordedAt) preset.recordedAt = recordedAt;
      return preset;
    });
    byService.set(String(serviceId), presets);
  }
  return new PresetStore(byService);
}

export function storeToData(store: PresetStore): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const serviceId of [...store.byService.keys()].sort()) {
    const presets = store.byService.get(serviceId) ?? [];
    if (presets.length === 0) continue;
    payload[serviceId] = presets.map(presetToData);
  }
  return payload;
}

/** Carimbo em UTC, no formato que a UI mostra. */
export function nowStamp(at: Date = new Date()): string {
  return at.toISOString().slice(0, 16).replace("T", " ");
}
