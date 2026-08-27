/**
 * Taxonomia de erro da TV.
 *
 * A distinção importa porque cada uma leva a um conselho diferente na tela: "não achei
 * a TV" manda o usuário olhar a rede, "a TV recusou a chave" manda parear de novo.
 */

export class TvError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TvError";
  }
}

/** Não deu nem para falar com a TV: desligada, fora da rede, IP trocado. */
export class TvUnreachableError extends TvError {
  constructor(message: string) {
    super(message);
    this.name = "TvUnreachableError";
  }
}

/** A TV respondeu, mas não aceita esta chave. Só re-parear resolve. */
export class TvPairError extends TvError {
  constructor(message: string) {
    super(message);
    this.name = "TvPairError";
  }
}
