/** Gravação de sequências: compressão e ciclo de vida. */

import { describe, expect, it } from "vitest";

import {
  Recorder,
  RecorderError,
  compress,
  type ButtonEvent,
} from "../src/tv/recorder.ts";
import type { ButtonStep, Step, TextStep, WaitStep } from "../src/tv/macros.ts";

class FakeClock {
  now = 0;
  readonly tick = (): number => this.now;
  advance(seconds: number): void {
    this.now += seconds;
  }
}

const events = (...pairs: [string, number][]): ButtonEvent[] =>
  pairs.map(([name, at]) => ({ name, at }));

const button = (name: string, times = 1): ButtonStep => ({
  kind: "button",
  name,
  times,
  delay: 0.2,
});
const wait = (seconds: number): WaitStep => ({ kind: "wait", seconds });
const text = (template: string): TextStep => ({ kind: "text", template });

describe("compress", () => {
  it("junta toques repetidos em times", () => {
    // Sem isto, ir até o fim de um menu viraria 8 linhas de RIGHT no YAML.
    const steps = compress(events(["RIGHT", 1.0], ["RIGHT", 1.1], ["RIGHT", 1.2]), 0);
    expect(steps).toEqual([button("RIGHT", 3)]);
  });

  it("ignora pausa curta", () => {
    // Abaixo do limite é ritmo de dedo, não intenção — o delay padrão já cobre.
    expect(compress(events(["UP", 1.0], ["DOWN", 1.3]), 0)).toEqual([
      button("UP"),
      button("DOWN"),
    ]);
  });

  it("transforma pausa longa em wait", () => {
    expect(compress(events(["ENTER", 1.0], ["DOWN", 3.2]), 0)).toEqual([
      button("ENTER"),
      wait(2.2),
      button("DOWN"),
    ]);
  });

  it("limita hesitação gigante", () => {
    // Você parou para olhar a TV; isso não deve virar 40s de espera na macro.
    expect(compress(events(["ENTER", 1.0], ["DOWN", 41.0]), 0)).toEqual([
      button("ENTER"),
      wait(5.0),
      button("DOWN"),
    ]);
  });

  it("descarta o tempo até o primeiro toque", () => {
    // É o tempo de pegar o celular, não parte da sequência.
    expect(compress(events(["UP", 30.0]), 0)).toEqual([button("UP")]);
  });

  it("separa repetições do mesmo botão com wait no meio", () => {
    // Dois grupos de RIGHT com pausa entre eles não podem virar um grupo só.
    const steps = compress(
      events(["RIGHT", 1.0], ["RIGHT", 1.1], ["RIGHT", 5.0], ["RIGHT", 5.1]),
      0,
    );
    expect(steps).toEqual([button("RIGHT", 2), wait(3.9), button("RIGHT", 2)]);
  });

  it("não gera passo nenhum de gravação vazia", () => {
    expect(compress([], 0)).toEqual([] as Step[]);
  });
});

describe("Recorder", () => {
  const startedRecorder = (clock: FakeClock, options = {}) => {
    const recorder = new Recorder({ clock: clock.tick, ...options });
    recorder.start({ serviceId: "max", appId: null });
    return recorder;
  };

  it("percorre o fluxo completo", () => {
    const clock = new FakeClock();
    const recorder = startedRecorder(clock);

    expect(recorder.active).toBe(true);
    clock.advance(1);
    recorder.observe("DOWN");
    clock.advance(0.1);
    recorder.observe("DOWN");
    clock.advance(0.1);
    recorder.observe("ENTER");
    expect(recorder.count).toBe(3);

    const { steps, serviceId } = recorder.stop();

    expect(serviceId).toBe("max");
    expect(steps).toEqual([button("DOWN", 2), button("ENTER")]);
    expect(recorder.active).toBe(false);
  });

  it("ignora toques fora de gravação", () => {
    const recorder = new Recorder();
    recorder.observe("UP");
    expect(recorder.count).toBe(0);
    expect(recorder.active).toBe(false);
  });

  it("recusa duas gravações ao mesmo tempo", () => {
    const recorder = startedRecorder(new FakeClock());
    expect(() => recorder.start({ serviceId: "max", appId: null })).toThrow(RecorderError);
  });

  it("dá erro claro ao parar sem gravar", () => {
    expect(() => new Recorder().stop()).toThrow(/Nenhuma gravação/);
  });

  it("avisa quando nada foi apertado", () => {
    const recorder = startedRecorder(new FakeClock());
    expect(() => recorder.stop()).toThrow(/nenhum botão/);
  });

  it("expira gravação esquecida", () => {
    // Senão ela captura toques de horas depois, que não são dela.
    const clock = new FakeClock();
    const recorder = startedRecorder(clock, { ttlSeconds: 60 });

    clock.advance(61);

    expect(recorder.active).toBe(false);
    recorder.observe("UP");
    expect(recorder.count).toBe(0);
  });

  it("descarta tudo ao cancelar", () => {
    const recorder = startedRecorder(new FakeClock());
    recorder.observe("UP");
    recorder.cancel();
    expect(recorder.active).toBe(false);
  });

  it("barra gravação longa demais", () => {
    // Um limite evita que um toque preso gere uma macro de milhares de passos.
    const clock = new FakeClock();
    const recorder = startedRecorder(clock);

    expect(() => {
      for (let index = 0; index < 300; index += 1) {
        clock.advance(0.05);
        recorder.observe("UP");
      }
    }).toThrow(/longa demais/);
  });
});

describe("gravar as macros do apps.yaml", () => {
  it("inclui o texto digitado na macro", () => {
    // Sem isto a gravação da busca parava no campo de texto — antes do passo útil.
    const clock = new FakeClock();
    const recorder = new Recorder({ clock: clock.tick });
    recorder.start({ serviceId: "max", appId: null, target: "search" });

    clock.advance(1);
    recorder.observe("ENTER");
    clock.advance(1);
    recorder.observeText("The Last of Us");

    expect(recorder.stop().steps).toContainEqual(text("The Last of Us"));
  });

  it("troca o título digitado pelo placeholder", () => {
    // É o que torna a macro reutilizável: gravada uma vez, serve para qualquer título.
    const clock = new FakeClock();
    const recorder = new Recorder({ clock: clock.tick });
    recorder.start({
      serviceId: "max",
      appId: null,
      target: "search",
      sampleTitle: "The Last of Us",
    });

    clock.advance(1);
    recorder.observeText("The Last of Us");

    expect(recorder.stop().steps).toEqual([text("{title}")]);
  });

  it("casa o título sem diferenciar maiúsculas", () => {
    // O teclado da TV pode devolver capitalização diferente da que você informou.
    const clock = new FakeClock();
    const recorder = new Recorder({ clock: clock.tick });
    recorder.start({ serviceId: "max", appId: null, target: "search", sampleTitle: "fallout" });

    clock.advance(1);
    recorder.observeText("Fallout");

    expect(recorder.stop().steps).toEqual([text("{title}")]);
  });
});
