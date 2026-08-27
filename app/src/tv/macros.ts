/**
 * Macros de navegação: a sequência que leva um app até a busca e digita o título.
 *
 * Por que macro e não deep link: o webOS aceita `params.contentTarget`, mas o formato
 * é definido por cada app e não é documentado para Max/Disney+. A macro reproduz o que
 * uma pessoa faria com o controle — e isso funciona em qualquer app, hoje e depois.
 *
 * Como é frágil por natureza (depende do layout do app), mora no YAML, não aqui.
 */

import { normalize } from "./buttons.ts";

export const MAX_REPEAT = 30;
export const MAX_WAIT_SECONDS = 30;

/** Espera em segundos — a mesma unidade que o YAML usa. */
export type Sleeper = (seconds: number) => Promise<void>;

export class MacroError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MacroError";
  }
}

/** O pedaço da sessão que uma macro usa (facilita testar sem TV). */
export interface MacroTarget {
  button: (name: string) => Promise<void>;
  insertText: (text: string, replace?: boolean) => Promise<void>;
  sendEnter: () => Promise<void>;
  deleteCharacters: (count: number) => Promise<void>;
}

/**
 * Um botão, repetido N vezes.
 *
 * `times` aceita um template (ex.: "{episode_index}") porque é assim que se chega a um
 * episódio específico: grava-se a navegação até o episódio 1 e o número faz o resto.
 */
export interface ButtonStep {
  kind: "button";
  name: string;
  times: number | string;
  delay: number;
}

export interface WaitStep {
  kind: "wait";
  seconds: number;
}

export interface TextStep {
  kind: "text";
  template: string;
}

export interface EnterStep {
  kind: "enter";
}

export interface ClearStep {
  kind: "clear";
  count: number;
}

export type Step = ButtonStep | WaitStep | TextStep | EnterStep | ClearStep;

export type RawStep = Record<string, unknown>;

/** Resolve quantas vezes apertar, já com o contexto da execução. */
export function repeatCount(step: ButtonStep, context: Readonly<Record<string, string>>): number {
  if (typeof step.times === "number") return step.times;

  const rendered = render(step.times, context).trim();
  if (!/^[+-]?\d+$/.test(rendered)) {
    throw new MacroError(
      `'times' virou ${JSON.stringify(rendered)}, que não é um número. ` +
        `Template: ${JSON.stringify(step.times)}`,
    );
  }
  // Um índice fora da faixa significa contexto errado (ou episódio inexistente).
  // Prender no limite é melhor que apertar 400 vezes e sair do ar.
  return Math.max(0, Math.min(Number(rendered), MAX_REPEAT));
}

function asPositiveFloat(value: unknown, field: string, maximum: number): number {
  // `Number(null)` e `Number("")` dão 0, o que transformaria YAML torto em delay zero.
  if (value === null || value === undefined || typeof value === "boolean" || value === "") {
    throw new MacroError(`${field} precisa ser um número, recebi ${JSON.stringify(value)}`);
  }
  const number = Number(value);
  if (Number.isNaN(number)) {
    throw new MacroError(`${field} precisa ser um número, recebi ${JSON.stringify(value)}`);
  }
  if (number < 0 || number > maximum) {
    throw new MacroError(`${field} deve ficar entre 0 e ${maximum}, recebi ${number}`);
  }
  return number;
}

function asInteger(value: unknown, field: string): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && /^[+-]?\d+$/.test(value.trim())) return Number(value.trim());
  throw new MacroError(`'${field}' precisa ser um número inteiro, recebi ${JSON.stringify(value)}`);
}

/** `times` pode ser um número fixo ou um template resolvido na execução. */
function parseTimes(raw: unknown): number | string {
  if (typeof raw === "string" && raw.includes("{")) return raw;

  let times: number;
  try {
    times = asInteger(raw, "times");
  } catch {
    throw new MacroError(`'times' precisa ser número ou template, recebi ${JSON.stringify(raw)}`);
  }
  if (times < 1 || times > MAX_REPEAT) {
    throw new MacroError(`'times' deve ficar entre 1 e ${MAX_REPEAT}, recebi ${times}`);
  }
  return times;
}

/** Converte um item do YAML num passo. Erra alto: YAML torto vira erro claro. */
export function parseStep(raw: RawStep): Step {
  if ("button" in raw) {
    return {
      kind: "button",
      name: normalize(String(raw["button"])),
      // `??` daria default para `times:` vazio no YAML, escondendo o erro de
      // digitação. Chave ausente usa o default; chave presente e vazia é erro.
      times: parseTimes("times" in raw ? raw["times"] : 1),
      delay: asPositiveFloat("delay" in raw ? raw["delay"] : 0.2, "delay", 5),
    };
  }
  if ("wait" in raw) {
    return { kind: "wait", seconds: asPositiveFloat(raw["wait"], "wait", MAX_WAIT_SECONDS) };
  }
  if ("text" in raw) {
    return { kind: "text", template: String(raw["text"]) };
  }
  if ("enter" in raw) {
    return { kind: "enter" };
  }
  if ("clear" in raw) {
    const count = asInteger(raw["clear"], "clear");
    if (count < 1 || count > 200) {
      throw new MacroError(`'clear' deve ficar entre 1 e 200, recebi ${count}`);
    }
    return { kind: "clear", count };
  }
  throw new MacroError(`Passo sem ação reconhecida: ${JSON.stringify(raw)}`);
}

export function parseSteps(rawSteps: readonly RawStep[] | null | undefined): Step[] {
  return (rawSteps ?? []).map(parseStep);
}

/** Aspas simples no estilo do YAML compacto que o apps.yaml usa à mão. */
function quoted(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

/**
 * Serializa um passo no estilo compacto que o apps.yaml usa à mão.
 *
 * Escrito aqui, e não com a biblioteca de YAML, porque o `{button: UP, times: 3}` de
 * uma linha só é o que torna o arquivo legível e editável — o dump padrão quebraria
 * cada chave numa linha e o arquivo deixaria de convidar a edição manual.
 */
export function stepToYaml(step: Step): string {
  switch (step.kind) {
    case "button": {
      const parts = [`button: ${step.name}`];
      if (step.times !== 1) {
        // Template vai entre aspas: `times: {episode_index}` sem elas é um mapa.
        parts.push(
          typeof step.times === "string"
            ? `times: ${quoted(step.times)}`
            : `times: ${step.times}`,
        );
      }
      if (step.delay !== 0.2) parts.push(`delay: ${step.delay}`);
      return `{${parts.join(", ")}}`;
    }
    case "wait":
      return `{wait: ${step.seconds}}`;
    case "text":
      return `{text: "${step.template}"}`;
    case "enter":
      return "{enter: true}";
    case "clear":
      return `{clear: ${step.count}}`;
  }
}

/**
 * Substitui {title}, {year}... sem usar interpolação de verdade.
 *
 * Títulos vêm do TMDb e podem conter chaves; um `format` de verdade explodiria ou
 * vazaria atributos.
 */
export function render(
  template: string,
  context: Readonly<Record<string, string>>,
): string {
  let rendered = template;
  for (const [key, value] of Object.entries(context)) {
    rendered = rendered.split(`{${key}}`).join(value);
  }
  return rendered;
}

const defaultSleep: Sleeper = (seconds) =>
  new Promise((resolve) => setTimeout(resolve, seconds * 1000));

export interface RunOptions {
  sleep?: Sleeper;
  /** Recebe cada passo assim que acontece — é o que alimenta o calibrador ao vivo. */
  onStep?: (entry: string) => void;
}

/** Executa a macro e devolve o rastro do que fez. */
export async function runSteps(
  session: MacroTarget,
  steps: readonly Step[],
  context: Readonly<Record<string, string>> = {},
  options: RunOptions = {},
): Promise<string[]> {
  const sleep = options.sleep ?? defaultSleep;
  const trace: string[] = [];

  const record = (entry: string): void => {
    trace.push(entry);
    options.onStep?.(entry);
  };

  for (const step of steps) {
    switch (step.kind) {
      case "button": {
        const repeat = repeatCount(step, context);
        for (let index = 0; index < repeat; index += 1) {
          await session.button(step.name);
          record(`button ${step.name}`);
          if (step.delay && index < repeat - 1) await sleep(step.delay);
        }
        break;
      }
      case "wait":
        await sleep(step.seconds);
        record(`wait ${step.seconds}s`);
        break;
      case "text": {
        const text = render(step.template, context);
        await session.insertText(text, true);
        record(`text ${quoted(text)}`);
        break;
      }
      case "enter":
        await session.sendEnter();
        record("enter");
        break;
      case "clear":
        await session.deleteCharacters(step.count);
        record(`clear ${step.count}`);
        break;
    }
  }

  return trace;
}
