/**
 * Abrir um título na TV — o objetivo do projeto.
 *
 * Estratégia em camadas, sempre com queda para algo que funciona:
 *
 *   1. deep link  — `params.contentTarget`, se você calibrou um para aquele app.
 *                   Instantâneo, mas o formato é privado de cada app; pode não existir.
 *   2. macro      — abre o app, espera carregar, navega até a busca e digita o título.
 *                   Mais lento, porém funciona em qualquer app.
 *   3. só abrir   — se não há macro configurada, ao menos deixa o app aberto.
 *
 * Nunca falha em silêncio: o resultado sempre diz qual camada foi usada.
 */

import { canPickEpisode, canSearch, type Service } from "./apps.ts";
import { render, runSteps, type MacroTarget, type Sleeper } from "./macros.ts";

/**
 * O que o abridor precisa de uma sessão.
 *
 * Declarar o recorte em vez de depender da `TvSession` inteira é o que permite testar
 * as camadas de abertura sem TV e sem socket — a `TvSession` satisfaz isto de graça.
 */
export interface OpenerSession extends MacroTarget {
  currentApp: () => Promise<string | null>;
  launch: (appId: string) => Promise<unknown>;
  closeApp: (appId: string) => Promise<unknown>;
  launchWithParams: (appId: string, params: Record<string, unknown>) => Promise<unknown>;
}

/**
 * De quanto em quanto tempo perguntamos à TV se o app já assumiu a tela. Curto o
 * bastante para não desperdiçar espera, longo o bastante para não competir com os
 * comandos do usuário pelo mesmo socket.
 */
export const FOREGROUND_POLL = 0.5;

export type OpenStrategy = "deep_link" | "macro" | "episode" | "launch";

export interface OpenResult {
  serviceId: string;
  appId: string;
  strategy: OpenStrategy;
  title: string | null;
  trace: string[];
}

export function searched(result: OpenResult): boolean {
  return result.strategy !== "launch";
}

/**
 * Variáveis disponíveis nas macros.
 *
 * `episode_index` é o deslocamento a partir do episódio 1 — é ele que a macro usa em
 * `times`, já que se grava a navegação até o primeiro episódio e o resto é contagem.
 */
export function buildContext(
  title: string,
  year: string | null,
  tmdbId: string | null,
  season: number | null,
  episode: number | null,
): Record<string, string> {
  return {
    title,
    year: year ?? "",
    tmdb_id: tmdbId ?? "",
    season: season ? String(season) : "",
    episode: episode ? String(episode) : "",
    season_index: season ? String(Math.max(0, season - 1)) : "0",
    episode_index: episode ? String(Math.max(0, episode - 1)) : "0",
  };
}

const defaultSleep: Sleeper = (seconds) =>
  new Promise((resolve) => setTimeout(resolve, seconds * 1000));

export interface OpenTitleOptions {
  year?: string | null;
  tmdbId?: string | null;
  season?: number | null;
  episode?: number | null;
}

export interface TitleOpenerOptions {
  sleep?: Sleeper;
  onStep?: (entry: string) => void;
}

export class TitleOpener {
  private readonly sleep: Sleeper;
  private readonly onStep: ((entry: string) => void) | undefined;

  constructor(
    private readonly session: OpenerSession,
    options: TitleOpenerOptions = {},
  ) {
    this.sleep = options.sleep ?? defaultSleep;
    this.onStep = options.onStep;
  }

  private note(entry: string): void {
    this.onStep?.(entry);
  }

  /**
   * Espera o app chegar em primeiro plano. Devolve false se estourou o tempo.
   *
   * Medir vale mais que chutar: a tela de perfil só desenha depois que o app assume a
   * tela, e esse tempo varia com o humor da TV. Perguntar é barato; contar um tempo
   * fixo a partir do launch erra por baixo justamente quando a TV está lenta.
   */
  private async awaitForeground(appId: string, budget: number): Promise<boolean> {
    let waited = 0;
    while (waited < budget) {
      try {
        if ((await this.session.currentApp()) === appId) return true;
      } catch {
        // Não saber em que app a TV está não pode abortar a abertura: seguimos pelo
        // tempo fixo, que é o comportamento de antes.
        return false;
      }
      await this.sleep(FOREGROUND_POLL);
      waited += FOREGROUND_POLL;
    }
    return false;
  }

  /**
   * Abre o app num estado conhecido, em vez de tentar deduzir em qual ele está.
   *
   * Max e Disney+ mostram "Quem está assistindo?" toda vez que o app abre do zero —
   * a ajuda do Max diz que em TV isso é por design para perfis adultos.
   *
   * Decidir isso comparando o app em primeiro plano com o app alvo ANTES de lançar
   * tinha uma armadilha que se fechava sozinha: parado na própria tela de perfil, o
   * app em primeiro plano já é o alvo — então o tratamento era pulado exatamente na
   * situação em que era necessário, e a macro de busca rodava em cima do seletor.
   * Bastava falhar uma vez para nunca mais sair de lá.
   *
   * Por isso, havendo macro de perfil, o app é fechado antes de abrir. Custa alguns
   * segundos e devolve o que faltava: saber em que tela a TV está.
   */
  private async launch(service: Service): Promise<string[]> {
    const trace: string[] = [];

    if (service.profile.length > 0) {
      this.note(`fechando ${service.appId} para abrir do zero`);
      try {
        await this.session.closeApp(service.appId);
        trace.push(`close ${service.appId}`);
        await this.sleep(service.closeSettle);
      } catch {
        // Fechar é preparação, não o objetivo: app que não estava aberto recusa o
        // comando, e insistir nisso impediria a abertura de acontecer.
      }
    }

    await this.session.launch(service.appId);
    this.note(`launch ${service.appId}`);
    trace.push(`launch ${service.appId}`);

    if (service.profile.length === 0) return trace;

    if (await this.awaitForeground(service.appId, service.foregroundTimeout)) {
      trace.push("app em primeiro plano");
    }

    this.note(`wait ${service.waitBeforeProfile}s (tela de perfil)`);
    await this.sleep(service.waitBeforeProfile);
    const profileTrace = await runSteps(this.session, service.profile, {}, {
      sleep: this.sleep,
      ...(this.onStep ? { onStep: this.onStep } : {}),
    });
    trace.push(`wait ${service.waitBeforeProfile}s`, ...profileTrace);
    return trace;
  }

  /** Só abre o app, sem buscar nada. */
  async openService(service: Service): Promise<OpenResult> {
    const trace = await this.launch(service);
    return {
      serviceId: service.id,
      appId: service.appId,
      strategy: "launch",
      title: null,
      trace,
    };
  }

  async openTitle(
    service: Service,
    title: string,
    options: OpenTitleOptions = {},
  ): Promise<OpenResult> {
    const { year = null, tmdbId = null, season = null, episode = null } = options;
    const context = buildContext(title, year, tmdbId, season, episode);
    const runOptions = {
      sleep: this.sleep,
      ...(this.onStep ? { onStep: this.onStep } : {}),
    };

    if (service.contentTarget) {
      const target = render(service.contentTarget, context);
      await this.session.launchWithParams(service.appId, { contentTarget: target });
      return {
        serviceId: service.id,
        appId: service.appId,
        strategy: "deep_link",
        title,
        trace: [`contentTarget '${target}'`],
      };
    }

    const launchTrace = await this.launch(service);
    if (!canSearch(service)) {
      return {
        serviceId: service.id,
        appId: service.appId,
        strategy: "launch",
        title,
        trace: launchTrace,
      };
    }

    // Digitar antes do app terminar de carregar joga as teclas no vazio —
    // é a causa nº 1 de macro que "funciona às vezes".
    this.note(`wait ${service.waitAfterLaunch}s (carregando o app)`);
    await this.sleep(service.waitAfterLaunch);
    const searchTrace = await runSteps(this.session, service.search, context, runOptions);

    const result: OpenResult = {
      serviceId: service.id,
      appId: service.appId,
      strategy: "macro",
      title,
      trace: [...launchTrace, `wait ${service.waitAfterLaunch}s`, ...searchTrace],
    };

    if (episode === null || !canPickEpisode(service)) {
      // Sem macro de episódio a série fica aberta na página inicial dela — que é
      // exatamente onde o fluxo já chegava antes. Nunca pior que o estado atual.
      return result;
    }

    this.note(`wait ${service.waitBeforeEpisode}s (abrindo a série)`);
    await this.sleep(service.waitBeforeEpisode);
    const episodeTrace = await runSteps(this.session, service.episode, context, runOptions);
    result.strategy = "episode";
    result.trace.push(`wait ${service.waitBeforeEpisode}s`, ...episodeTrace);
    return result;
  }
}
