/**
 * Executor de uma ação longa por vez.
 *
 * Abrir um título leva ~20s (esperar o app carregar + navegar + digitar). Travar a
 * tela todo esse tempo faria o app parecer morto. Então a ação roda solta e a tela
 * pergunta o progresso.
 *
 * Uma ação por vez de propósito: duas macros simultâneas mandariam teclas concorrentes
 * para a mesma TV e as duas falhariam.
 */

export type ActionStatus = "idle" | "running" | "done" | "error";

export interface ActionState {
  status: ActionStatus;
  label: string;
  detail: string;
  /**
   * O passo que está acontecendo AGORA. Sem isto, abrir um título eram ~25 segundos
   * de tela parada: quando algo dava errado no meio, parecia que nada aconteceu.
   */
  step: string;
  trace: string[];
}

const idle = (): ActionState => ({
  status: "idle",
  label: "",
  detail: "",
  step: "",
  trace: [],
});

export class ActionRunner {
  state: ActionState = idle();
  private running: Promise<void> | null = null;

  get busy(): boolean {
    return this.running !== null;
  }

  /** Publica o passo atual. Serve de `onStep` para quem executa a ação. */
  readonly note = (step: string): void => {
    this.state.step = step;
  };

  /** Dispara a ação. Devolve false se já havia uma rodando. */
  start(label: string, action: () => Promise<string[]>): boolean {
    if (this.busy) return false;

    this.state = { ...idle(), status: "running", label };
    this.running = this.run(action).finally(() => {
      this.running = null;
    });
    return true;
  }

  private async run(action: () => Promise<string[]>): Promise<void> {
    try {
      const trace = await action();
      this.state.status = "done";
      this.state.trace = trace;
      this.state.step = "";
    } catch (error) {
      // Captura ampla de propósito: rodando solta, qualquer erro que escape some no
      // vazio — e a tela fica esperando uma ação que já morreu.
      this.state.status = "error";
      this.state.detail = error instanceof Error ? error.message : String(error);
    }
  }

  /** Espera a ação atual acabar. Só os testes precisam disto. */
  async settle(): Promise<void> {
    await this.running;
  }

  reset(): void {
    this.state = idle();
  }
}
