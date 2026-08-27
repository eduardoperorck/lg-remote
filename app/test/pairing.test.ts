/**
 * Pareamento: o prazo humano e a classificação do "não".
 *
 * Distinguir a recusa instantânea da recusa depois do pedido é o que separa um
 * conselho útil ("ligue a TV e tente de novo") de um inútil ("deu erro").
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  PairRefusedError,
  PairTimeoutError,
  PairUnreachableError,
  pair,
} from "../src/tv/pairing.ts";
import { FakeTv, type FakeTvOptions } from "./fake-tv.ts";

let tv: FakeTv | null = null;

async function startTv(options: FakeTvOptions): Promise<number> {
  tv = new FakeTv(options);
  await tv.start();
  return Number(new URL(tv.url).port);
}

afterEach(async () => {
  await tv?.stop();
  tv = null;
});

describe("pair", () => {
  it("devolve a chave quando alguém aceita na TV", async () => {
    const port = await startTv({ promptAnswer: "accept", grantedKey: "chave-nova" });

    await expect(pair("127.0.0.1", port)).resolves.toBe("chave-nova");
  });

  it("faz o getSystemInfo antes do registro", async () => {
    // Firmware novo recusa o register sem isso, e sem dizer por quê.
    const port = await startTv({ promptAnswer: "accept" });

    await pair("127.0.0.1", port);

    expect(tv!.requests[0]?.uri).toBe("system/getSystemInfo");
  });

  it("marca como não-exibida a recusa que volta na hora", async () => {
    // A TV em standby responde rápido demais para alguém ter lido o pedido.
    const port = await startTv({ instantRefusal: true });

    const error = await pair("127.0.0.1", port).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(PairRefusedError);
    expect((error as PairRefusedError).prompted).toBe(false);
    expect((error as PairRefusedError).message).toMatch(/standby/);
  });

  it("marca como exibida a recusa que demora", async () => {
    // Demorou: alguém leu o pedido na tela e disse não. O conselho é outro.
    const port = await startTv({ promptAnswer: "refuse", promptDelayMs: 5 });
    let clock = 0;

    const error = await pair("127.0.0.1", port, {
      // Relógio de mentira: o teste não espera 2 segundos de verdade.
      now: () => (clock += 3000),
    }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(PairRefusedError);
    expect((error as PairRefusedError).prompted).toBe(true);
    expect((error as PairRefusedError).message).toMatch(/Sim/);
  });

  it("desiste quando ninguém responde ao pedido", async () => {
    const port = await startTv({ promptAnswer: "silence" });

    await expect(pair("127.0.0.1", port, { promptTimeout: 60 })).rejects.toBeInstanceOf(
      PairTimeoutError,
    );
  });

  it("aceita de cara quando a TV já lembra do aparelho", async () => {
    // A TV responde `registered` como PRIMEIRA mensagem, sem passar pelo pedido.
    const port = await startTv({ remembersDevice: true, grantedKey: "chave-lembrada" });

    await expect(pair("127.0.0.1", port)).resolves.toBe("chave-lembrada");
  });

  it("dá erro de rede quando não há TV na porta", async () => {
    await expect(pair("127.0.0.1", 1)).rejects.toBeInstanceOf(PairUnreachableError);
  });
});
