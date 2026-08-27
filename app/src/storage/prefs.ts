/** O `Store` de verdade: Preferences do Capacitor + Keychain via plugin nativo. */

import { Preferences } from "@capacitor/preferences";

import { LgSsap } from "../../plugins/lg-ssap/src/index.ts";
import type { PrefKey, Store } from "./store.ts";

const CLIENT_KEY = "tv-client-key";

export const capacitorStore: Store = {
  async get(key: PrefKey) {
    const { value } = await Preferences.get({ key });
    return value;
  },
  async set(key: PrefKey, value: string) {
    await Preferences.set({ key, value });
  },
  async remove(key: PrefKey) {
    await Preferences.remove({ key });
  },
  async getClientKey() {
    const { value } = await LgSsap.keychainGet({ key: CLIENT_KEY });
    return value;
  },
  async setClientKey(value: string) {
    await LgSsap.keychainSet({ key: CLIENT_KEY, value });
  },
  async forgetClientKey() {
    await LgSsap.keychainDelete({ key: CLIENT_KEY });
  },
};
