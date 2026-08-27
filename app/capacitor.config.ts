import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "dev.lgremote.control",
  appName: "Controle LG",
  webDir: "dist",
  ios: {
    // A TV usa certificado autoassinado, mas nada do WebView fala com ela: quem abre
    // o socket é o plugin nativo. O WebView em si continua sem exceção nenhuma.
    contentInset: "always",
  },
  server: {
    // Sem isto o WebView serve de `capacitor://localhost`, e o `fetch` para o TMDb
    // (único destino externo do app) é bloqueado pela política de origem.
    iosScheme: "capacitor",
  },
};

export default config;
