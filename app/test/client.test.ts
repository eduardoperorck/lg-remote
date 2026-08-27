import { afterEach, describe, expect, it } from "vitest";

import { SsapClient } from "../src/tv/client.ts";
import { TvPairError, TvUnreachableError } from "../src/tv/errors.ts";
import { FakeTv, type FakeTvOptions } from "./fake-tv.ts";

let tv: FakeTv | null = null;

async function connectTo(
  options: FakeTvOptions,
  clientKey: string | null = null,
): Promise<SsapClient> {
  tv = new FakeTv(options);
  await tv.start();
  const port = Number(new URL(tv.url).port);
  const client = new SsapClient("127.0.0.1", port, clientKey, { requestTimeout: 2000 });
  await client.connect();
  return client;
}

afterEach(async () => {
  await tv?.stop();
  tv = null;
});

describe("SsapClient", () => {
  it("entra direto quando a TV já conhece a chave", async () => {
    const client = await connectTo({ acceptedKey: "chave-boa" }, "chave-boa");

    expect(client.connected).toBe(true);
    expect(client.clientKey).toBe("chave-boa");
    // Firmware novo exige o getSystemInfo antes do registro.
    expect(tv!.requests[0]?.uri).toBe("system/getSystemInfo");

    await client.disconnect();
  });

  it("guarda a chave nova quando o pedido é aceito na tela", async () => {
    const client = await connectTo({ promptAnswer: "accept", grantedKey: "chave-fresca" });

    expect(client.clientKey).toBe("chave-fresca");
    await client.disconnect();
  });

  it("vira TvPairError quando a TV recusa na hora", async () => {
    tv = new FakeTv({ instantRefusal: true });
    await tv.start();
    const port = Number(new URL(tv.url).port);
    const client = new SsapClient("127.0.0.1", port, "chave-velha");

    await expect(client.connect()).rejects.toBeInstanceOf(TvPairError);
    expect(client.connected).toBe(false);
  });

  it("vira TvPairError quando o pedido aparece e é recusado", async () => {
    tv = new FakeTv({ promptAnswer: "refuse" });
    await tv.start();
    const port = Number(new URL(tv.url).port);
    const client = new SsapClient("127.0.0.1", port);

    await expect(client.connect()).rejects.toBeInstanceOf(TvPairError);
  });

  it("vira TvUnreachableError quando não há TV nenhuma na porta", async () => {
    // Porta fechada de propósito: é o caso "TV desligada da tomada".
    const client = new SsapClient("127.0.0.1", 1, "chave");
    await expect(client.connect()).rejects.toBeInstanceOf(TvUnreachableError);
  });

  it("manda botão pelo socket de input, não pelo principal", async () => {
    const client = await connectTo({ acceptedKey: "k" }, "k");

    await client.button("UP");
    await client.button("ENTER");
    await client.click();

    // O socket de input é mão única: a TV nunca confirma. Esperar a entrega é
    // responsabilidade de quem observa, não do cliente.
    await expect.poll(() => tv!.buttons).toEqual(["UP", "ENTER"]);
    expect(tv!.inputMessages.at(-1)).toBe("type:click\n\n");
    // O endereço do socket de input veio da TV, uma vez só.
    const asked = tv!.requests.filter(
      (r) => r.uri === "com.webos.service.networkinput/getPointerInputSocket",
    );
    expect(asked).toHaveLength(1);

    await client.disconnect();
  });

  it("devolve o payload da requisição", async () => {
    const client = await connectTo(
      {
        acceptedKey: "k",
        responses: { "audio/getVolume": { returnValue: true, volume: 17 } },
      },
      "k",
    );

    const result = await client.request("audio/getVolume");
    expect(result["volume"]).toBe(17);

    await client.disconnect();
  });

  it("transforma returnValue:false em erro em vez de sucesso silencioso", async () => {
    const client = await connectTo(
      {
        acceptedKey: "k",
        responses: {
          "com.webos.applicationManager/launch": { returnValue: false, errorText: "app não existe" },
        },
      },
      "k",
    );

    await expect(
      client.request("com.webos.applicationManager/launch", { id: "fantasma" }),
    ).rejects.toThrow(/app não existe/);

    await client.disconnect();
  });
});
