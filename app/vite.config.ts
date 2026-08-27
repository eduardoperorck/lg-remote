import { defineConfig } from "vite";

export default defineConfig({
  root: "www",
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    // O WebView do Capacitor carrega de um esquema próprio; caminhos relativos são
    // os únicos que funcionam nos dois modos (dev no navegador e app instalado).
    assetsDir: "assets",
  },
  server: {
    fs: {
      // O catálogo mora fora de `app/` porque ainda é o mesmo arquivo que o projeto
      // Python usa — uma cópia viraria duas verdades.
      allow: [".."],
    },
  },
});
