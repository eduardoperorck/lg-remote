/**
 * Gravação de sequências: você navega na TV, o app anota.
 *
 * O menu de legenda/áudio muda por app e por título, então adivinhar a sequência não
 * escala. Aqui o caminho é o inverso: você faz o que já faria com o controle, e o que
 * sai é uma macro pronta.
 *
 * A compressão é o que separa uma gravação usável de um despejo de eventos: toques
 * repetidos viram `times`, e as suas hesitações humanas viram esperas arredondadas —
 * ou desaparecem, quando são curtas demais para importar.
 */

import type { ButtonStep, Step } from "./macros.ts";

/** Abaixo disto, a pausa é ruído de digitação: o app já espera `delay` entre repetições. */
export const MIN_GAP_SECONDS = 0.6;
/** Acima disto, é você pensando ou olhando a TV — não deve virar espera fixa na macro. */
export const MAX_GAP_SECONDS = 5.0;
/** Uma gravação esquecida aberta captura toques que não são dela. */
export const DEFAULT_TTL_SECONDS = 300.0;
export const MAX_EVENTS = 200;

/** Para onde vai o resultado da gravação. */
export type RecordTarget = "preset" | "profile" | "search" | "episode";

export class RecorderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecorderError";
  }
}

export interface ButtonEvent {
  name: string;
  at: number;
  /** Preenchido quando o evento é texto digitado, não um botão do direcional. */
  text?: string;
}

interface Recording {
  serviceId: string | null;
  appId: string | null;
  startedAt: number;
  target: RecordTarget;
  /** O título digitado durante a gravação, que vira {title} na macro de busca. */
  sampleTitle: string;
  events: ButtonEvent[];
}

/** Troca o título de exemplo pelo placeholder, sem diferenciar maiúsculas. */
function templatize(text: string, sampleTitle: string): string {
  if (!sampleTitle) return text;
  const position = text.toLowerCase().indexOf(sampleTitle.toLowerCase());
  if (position < 0) return text;
  return text.slice(0, position) + "{title}" + text.slice(position + sampleTitle.length);
}

/** Arredonda para uma casa decimal, como o `round(x, 1)` do Python. */
function roundTenth(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * Transforma toques crus numa macro legível e editável.
 *
 * A primeira pausa (entre iniciar a gravação e o primeiro toque) é descartada de
 * propósito: é o tempo que você levou para pegar o celular, não parte da sequência.
 *
 * `sampleTitle` é o título que você digitou durante a gravação: ele vira `{title}`
 * para a macro servir a qualquer outro título depois. Sem isso, a macro gravada só
 * saberia buscar aquela série específica.
 */
export function compress(
  events: readonly ButtonEvent[],
  startedAt: number,
  sampleTitle = "",
): Step[] {
  const steps: Step[] = [];
  let previousAt = startedAt;

  for (const event of events) {
    const gap = event.at - previousAt;
    previousAt = event.at;

    // A espera vem ANTES do botão: reproduz o ritmo em que a TV recebeu os toques.
    if (steps.length > 0 && gap >= MIN_GAP_SECONDS) {
      steps.push({ kind: "wait", seconds: roundTenth(Math.min(gap, MAX_GAP_SECONDS)) });
    }

    if (event.text !== undefined) {
      steps.push({ kind: "text", template: templatize(event.text, sampleTitle) });
      continue;
    }

    const last = steps.at(-1);
    if (
      last !== undefined &&
      last.kind === "button" &&
      last.name === event.name &&
      typeof last.times === "number"
    ) {
      steps[steps.length - 1] = { ...last, times: last.times + 1 } satisfies ButtonStep;
    } else {
      steps.push({ kind: "button", name: event.name, times: 1, delay: 0.2 });
    }
  }

  return steps;
}

export interface RecorderOptions {
  /** Relógio monotônico em segundos. Injetável para o teste não depender do tempo real. */
  clock?: () => number;
  ttlSeconds?: number;
}

export interface StartOptions {
  serviceId: string | null;
  appId: string | null;
  target?: RecordTarget;
  sampleTitle?: string;
}

/** Uma gravação por vez — duas ao mesmo tempo misturariam os toques. */
export class Recorder {
  private readonly clock: () => number;
  private readonly ttl: number;
  private recording: Recording | null = null;

  constructor(options: RecorderOptions = {}) {
    this.clock = options.clock ?? (() => Date.now() / 1000);
    this.ttl = options.ttlSeconds ?? DEFAULT_TTL_SECONDS;
  }

  get active(): boolean {
    this.expireIfStale();
    return this.recording !== null;
  }

  get count(): number {
    return this.recording?.events.length ?? 0;
  }

  get serviceId(): string | null {
    return this.recording?.serviceId ?? null;
  }

  get target(): RecordTarget {
    return this.recording?.target ?? "preset";
  }

  private expireIfStale(): void {
    if (this.recording === null) return;
    if (this.clock() - this.recording.startedAt > this.ttl) {
      this.recording = null;
    }
  }

  start(options: StartOptions): void {
    this.expireIfStale();
    if (this.recording !== null) {
      throw new RecorderError("Já existe uma gravação em andamento. Pare a atual primeiro.");
    }
    this.recording = {
      serviceId: options.serviceId,
      appId: options.appId,
      startedAt: this.clock(),
      target: options.target ?? "preset",
      sampleTitle: options.sampleTitle ?? "",
      events: [],
    };
  }

  private add(name: string, text?: string): void {
    const recording = this.recording;
    if (recording === null) return;
    if (recording.events.length >= MAX_EVENTS) {
      throw new RecorderError(
        `Gravação longa demais (${MAX_EVENTS} toques). Pare e grave um trecho menor.`,
      );
    }
    const event: ButtonEvent = { name, at: this.clock() };
    if (text !== undefined) event.text = text;
    recording.events.push(event);
  }

  /** Anota um toque. Silencioso quando não há gravação — é o caminho normal. */
  observe(button: string): void {
    this.expireIfStale();
    this.add(button);
  }

  /**
   * Anota o que foi digitado.
   *
   * Sem isto, gravar a macro de busca capturava a navegação até o campo e parava ali
   * — justamente antes do passo que faz a busca acontecer.
   */
  observeText(text: string): void {
    this.expireIfStale();
    this.add("__text__", text);
  }

  cancel(): void {
    this.recording = null;
  }

  /** Encerra e devolve os passos e o serviço. Levanta se não houver o que gravar. */
  stop(): { steps: Step[]; serviceId: string | null; target: RecordTarget } {
    this.expireIfStale();
    const recording = this.recording;
    if (recording === null) {
      throw new RecorderError("Nenhuma gravação em andamento (ou ela expirou).");
    }
    this.recording = null;
    if (recording.events.length === 0) {
      throw new RecorderError("Você não apertou nenhum botão — nada para salvar.");
    }

    return {
      steps: compress(recording.events, recording.startedAt, recording.sampleTitle),
      serviceId: recording.serviceId,
      target: recording.target,
    };
  }
}
