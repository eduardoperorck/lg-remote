/** Dublês usados por vários testes. */

import type { MacroTarget, Sleeper } from "../src/tv/macros.ts";

export interface InsertedText {
  text: string;
  replace: boolean;
}

/** Sessão de mentira que só anota o que mandaram fazer. */
export class FakeTarget implements MacroTarget {
  readonly buttons: string[] = [];
  readonly texts: InsertedText[] = [];
  readonly deletes: number[] = [];
  enters = 0;

  async button(name: string): Promise<void> {
    this.buttons.push(name);
  }

  async insertText(text: string, replace = false): Promise<void> {
    this.texts.push({ text, replace });
  }

  async sendEnter(): Promise<void> {
    this.enters += 1;
  }

  async deleteCharacters(count: number): Promise<void> {
    this.deletes.push(count);
  }
}

export interface InstantSleep {
  (seconds: number): Promise<void>;
  /** Cada espera pedida, em segundos, na ordem em que foi pedida. */
  waits: number[];
}

/** Sleeper que não dorme: o teste confere o que foi pedido, não o relógio. */
export function instantSleep(): InstantSleep {
  const waits: number[] = [];
  const sleep = (async (seconds: number) => {
    waits.push(seconds);
  }) as InstantSleep;
  sleep.waits = waits;
  return sleep;
}

export type { Sleeper };

/** Sessão de mentira com o bastante para o abridor rodar sem TV. */
export class FakeSession extends FakeTarget {
  /** App em primeiro plano na TV. O `launch` mexe nele, como a TV de verdade faz. */
  current = "com.webos.app.livetv";
  readonly launches: string[] = [];
  readonly closes: string[] = [];
  readonly launchParams: { appId: string; params: Record<string, unknown> }[] = [];
  /** Ordem das chamadas, para provar que fechar acontece antes de abrir. */
  readonly calls: string[] = [];
  currentAppQueries = 0;
  /** Quando ligado, perguntar o app atual falha — a TV nem sempre responde isso. */
  currentAppFails = false;

  async currentApp(): Promise<string | null> {
    this.currentAppQueries += 1;
    if (this.currentAppFails) throw new Error("sem resposta");
    return this.current;
  }

  async launch(appId: string): Promise<unknown> {
    this.calls.push("launch");
    this.launches.push(appId);
    this.current = appId;
    return {};
  }

  async closeApp(appId: string): Promise<unknown> {
    this.calls.push("close");
    this.closes.push(appId);
    return {};
  }

  async launchWithParams(appId: string, params: Record<string, unknown>): Promise<unknown> {
    this.calls.push("launch_params");
    this.launchParams.push({ appId, params });
    return {};
  }
}
