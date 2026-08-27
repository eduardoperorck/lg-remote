import { registerPlugin } from "@capacitor/core";

import type { LgSsapPlugin } from "./definitions.ts";

export const LgSsap = registerPlugin<LgSsapPlugin>("LgSsap", {
  web: async () => new (await import("./web.ts")).LgSsapWeb(),
});

export * from "./definitions.ts";
