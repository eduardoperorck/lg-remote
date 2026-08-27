/**
 * Sobe a TV falsa numa porta fixa, para o `npm run dev` ter com quem falar.
 *
 * Qualquer porta menos a 3001: para ela o app usa `wss://`, e o navegador não abre
 * `wss://` contra certificado autoassinado — que é exatamente a limitação que o plugin
 * nativo existe para contornar. Fora dela o app usa `ws://` e a tela inteira fica
 * navegável no Windows.
 */

import { FakeTv } from "./fake-tv.ts";

const port = Number(process.argv[2] ?? 3010);

const tv = new FakeTv({
  promptAnswer: "accept",
  grantedKey: "chave-da-tv-falsa",
  responses: {
    "com.webos.applicationManager/getForegroundAppInfo": {
      returnValue: true,
      appId: "com.wbd.stream",
    },
    "audio/getVolume": { returnValue: true, volumeStatus: { volume: 22 } },
    "com.webos.applicationManager/listLaunchPoints": {
      returnValue: true,
      launchPoints: [
        { id: "com.wbd.stream", title: "Max" },
        { id: "netflix", title: "Netflix" },
        { id: "com.disney.disneyplus-prod", title: "Disney+" },
      ],
    },
  },
});

await tv.startOn(port);

console.log(`TV falsa escutando em ${tv.url}`);
console.log(`Na tela de pareamento, digite: 127.0.0.1:${port}`);

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void tv.stop().then(() => process.exit(0));
  });
}
