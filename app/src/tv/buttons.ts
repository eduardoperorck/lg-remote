/**
 * Botões aceitos pelo socket de input do webOS.
 *
 * A whitelist continua fazendo sentido no app: o nome do botão agora vem de uma macro
 * do apps.yaml, e um YAML torto não pode virar comando arbitrário na TV.
 */

export const NAVIGATION = [
  "UP", "DOWN", "LEFT", "RIGHT", "ENTER", "BACK", "EXIT", "HOME", "MENU",
] as const;
export const MEDIA = ["PLAY", "PAUSE", "STOP", "REWIND", "FASTFORWARD"] as const;
export const VOLUME = ["VOLUMEUP", "VOLUMEDOWN", "MUTE"] as const;
export const CHANNEL = ["CHANNELUP", "CHANNELDOWN"] as const;
export const COLORS = ["RED", "GREEN", "YELLOW", "BLUE"] as const;
export const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
export const EXTRA = ["INFO", "DASH", "ASTERISK", "CC", "GUIDE", "QMENU"] as const;

export const VALID_BUTTONS: ReadonlySet<string> = new Set<string>([
  ...NAVIGATION, ...MEDIA, ...VOLUME, ...CHANNEL, ...COLORS, ...DIGITS, ...EXTRA,
]);

export class UnknownButtonError extends Error {
  readonly button: string;

  constructor(name: string) {
    super(`Botão desconhecido: ${JSON.stringify(name)}`);
    this.name = "UnknownButtonError";
    this.button = name;
  }
}

/** Valida e normaliza o nome de um botão ('enter' -> 'ENTER'). */
export function normalize(name: string): string {
  const candidate = name.trim().toUpperCase();
  if (!VALID_BUTTONS.has(candidate)) {
    throw new UnknownButtonError(name);
  }
  return candidate;
}
