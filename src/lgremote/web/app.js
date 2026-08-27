"use strict";

/* PWA do controle. Sem build, sem framework: a tela é uma grade de botões e uma
   lista de resultados — dependência de bundler aqui só atrapalharia.

   Sem service worker de propósito: em HTTP na LAN o iOS não considera contexto
   seguro e não registraria. Não faz falta — o controle é inútil offline. */

const PIN_KEY = "lgremote.pin";

const el = (id) => document.getElementById(id);
const gate = el("gate");
const app = el("app");

let pin = localStorage.getItem(PIN_KEY) || "";
let pollTimer = null;
let actionTimer = null;
let currentServiceId = null;   // app em foco na TV — filtra os presets
let recording = false;
let recordTarget = "preset";  // para onde a gravação atual vai
let presets = [];
let config = { shortcuts: [] };
let tvOnline = false;          // decide se o botão de power liga ou desliga

/* ---------------- transporte ---------------- */

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Remote-Pin": pin,
      ...(options.headers || {}),
    },
  });

  if (response.status === 401) {
    forgetPin();
    throw new Error("PIN incorreto");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Erro ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body || {}) });

/* ---------------- entrada ---------------- */

function forgetPin() {
  pin = "";
  localStorage.removeItem(PIN_KEY);
  stopPolling();
  app.hidden = true;
  gate.hidden = false;
}

el("gate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = el("gate-error");
  error.hidden = true;
  pin = el("gate-pin").value.trim();
  try {
    await api("/api/status");
    localStorage.setItem(PIN_KEY, pin);
    enter();
  } catch (err) {
    pin = "";
    error.textContent = err.message;
    error.hidden = false;
  }
});

async function enter() {
  gate.hidden = true;
  app.hidden = false;
  await loadConfig();
  await loadPresets();
  startPolling();
}

/* ---------------- estado da TV ---------------- */

async function loadConfig() {
  config = await api("/api/config");
  const box = el("shortcuts");
  box.innerHTML = "";
  for (const service of config.shortcuts) {
    const button = document.createElement("button");
    button.textContent = service.label;
    button.addEventListener("click", () => launch(service.id, service.label));
    box.appendChild(button);
  }
  el("search-hint").textContent = config.catalog_enabled
    ? ""
    : "Busca desligada: falta TMDB_TOKEN no .env.";
  el("search-form").hidden = !config.catalog_enabled;
  el("btn-power").dataset.canWake = config.can_wake ? "1" : "";
}

async function refreshStatus() {
  const dot = el("dot");
  const text = el("status-text");
  try {
    const status = await api("/api/status");
    if (status.online) {
      dot.className = "dot online";
      text.textContent = status.current_service || status.current_app || "ligada";
      setCurrentService(status.current_service_id, status.current_service);
    } else {
      dot.className = "dot offline";
      // O detail diz QUAL é o problema ("a TV recusou a chave salva…"). Descartá-lo
      // deixava toda falha com a mesma cara de "TV desligada", inclusive as que têm
      // conserto conhecido.
      text.textContent = status.detail || "TV desligada";
      setCurrentService(null, null);
    }
    setOnline(status.online);
    setVolume(status.online ? status.volume : null);
    setRecording(status.recording, status.recording_presses);
  } catch (err) {
    dot.className = "dot offline";
    text.textContent = err.message;
    setOnline(false);
  }
}

function setVolume(volume) {
  const badge = el("volume-badge");
  badge.hidden = volume === null || volume === undefined;
  badge.textContent = badge.hidden ? "—" : `🔊 ${volume}`;
}

function setOnline(online) {
  tvOnline = Boolean(online);
  const button = el("btn-power");
  const canWake = button.dataset.canWake === "1";
  // Sem TV_MAC não há como ligar pela rede — dizer isso antes evita o toque inútil.
  button.disabled = !tvOnline && !canWake;
  button.title = tvOnline
    ? "Desligar a TV"
    : canWake
      ? "Ligar a TV"
      : "Para ligar a TV, configure TV_MAC no .env";
}

function setCurrentService(serviceId, label) {
  if (serviceId === currentServiceId) return;
  currentServiceId = serviceId;
  el("rec-context").textContent = serviceId
    ? `App em foco: ${label}. A gravação vai pertencer a ele.`
    : "Abra o app na TV para gravar.";
  renderPresets();
}

// A gravação vive no servidor, não aqui: se você fechar o PWA e voltar, o estado é o real.
function setRecording(active, presses) {
  recording = Boolean(active);
  el("rec-idle").hidden = recording;
  el("rec-active").hidden = !recording;
  el("rec-count").textContent = presses || 0;
}

function startPolling() {
  refreshStatus();
  stopPolling();
  // 5s: rápido o bastante para notar a TV mudar de app, devagar o bastante
  // para não competir com os toques no direcional pelo mesmo socket.
  pollTimer = setInterval(refreshStatus, 5000);
}

/* ---------------- presets ---------------- */

async function loadPresets() {
  try {
    const data = await api("/api/presets");
    presets = data.presets;
  } catch {
    presets = [];
  }
  renderPresets();
}

function presetsForCurrentApp() {
  if (!currentServiceId) return [];
  return presets.filter((preset) => preset.service_id === currentServiceId);
}

function renderPresets() {
  // Atalhos na aba Controle: só do app em foco, que é quando servem.
  const quick = el("quick-presets");
  quick.innerHTML = "";
  const mine = presetsForCurrentApp();
  quick.hidden = mine.length === 0;
  for (const preset of mine) {
    const button = document.createElement("button");
    button.textContent = preset.label;
    button.addEventListener("click", () => runPreset(preset));
    quick.appendChild(button);
  }

  // Lista completa na aba Legenda, agrupada por serviço.
  const list = el("presets-list");
  list.innerHTML = "";
  if (!presets.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Nenhum preset gravado ainda.";
    list.appendChild(empty);
    return;
  }
  for (const preset of presets) {
    list.appendChild(renderPresetRow(preset));
  }
}

function renderPresetRow(preset) {
  const row = document.createElement("div");
  row.className = "preset";

  const name = document.createElement("div");
  name.className = "name";
  const label = document.createElement("strong");
  label.textContent = preset.label;
  const meta = document.createElement("span");
  meta.textContent = `${preset.service_id} · ${preset.steps} passos`
    + (preset.recorded_at ? ` · ${preset.recorded_at}` : "");
  name.append(label, meta);

  const run = document.createElement("button");
  run.className = "primary";
  run.textContent = "Usar";
  run.addEventListener("click", () => runPreset(preset));

  const remove = document.createElement("button");
  remove.textContent = "🗑";
  remove.setAttribute("aria-label", `Apagar ${preset.label}`);
  remove.addEventListener("click", () => deletePreset(preset));

  row.append(name, run, remove);
  return row;
}

async function runPreset(preset) {
  try {
    await post(`/api/presets/${preset.service_id}/${preset.id}/run`);
    showBanner(preset.label + "…");
    watchAction();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

async function deletePreset(preset) {
  if (!confirm(`Apagar "${preset.label}"?`)) return;
  try {
    await api(`/api/presets/${preset.service_id}/${preset.id}`, { method: "DELETE" });
    await loadPresets();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

/* ---------------- gravador ----------------
   Dois destinos: "preset" vira um botão de atalho (legenda/áudio); os demais gravam a
   macro correspondente no apps.yaml — é o que substitui calibrar editando YAML. */

// O título de exemplo só faz sentido na busca: é ele que vira {title} na macro.
el("rec-target").addEventListener("change", () => {
  el("rec-title").hidden = el("rec-target").value !== "search";
});

el("btn-record").addEventListener("click", async () => {
  const target = el("rec-target").value;
  try {
    const started = await post("/api/record/start", {
      target,
      sample_title: el("rec-title").value.trim(),
    });
    recordTarget = started.target;
    setRecording(true, 0);
    showBanner(`Gravando em ${started.service_id} — navegue na aba Controle`);
    hideBannerLater(3000);
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
});

el("btn-record-cancel").addEventListener("click", async () => {
  await post("/api/record/cancel").catch(() => {});
  setRecording(false, 0);
});

el("btn-record-stop").addEventListener("click", async () => {
  // Macro do apps.yaml não tem nome: a chave já diz o que ela é. Só o preset precisa.
  if (recordTarget !== "preset") {
    await saveMacro();
    return;
  }
  el("name-hint").textContent = `${el("rec-count").textContent} toques gravados.`;
  el("name-input").value = "";
  el("name-modal").hidden = false;
  el("name-input").focus();
});

async function saveMacro() {
  try {
    const saved = await post("/api/record/stop", { label: "" });
    setRecording(false, 0);
    showBanner(`Macro "${saved.target}" gravada no apps.yaml`, "done");
    hideBannerLater();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

el("name-cancel").addEventListener("click", () => {
  el("name-modal").hidden = true;
});

el("name-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const label = el("name-input").value.trim();
  if (!label) return;
  try {
    await post("/api/record/stop", { label });
    el("name-modal").hidden = true;
    setRecording(false, 0);
    await loadPresets();
    showBanner(`Atalho "${label}" salvo`, "done");
    hideBannerLater();
  } catch (err) {
    el("name-hint").textContent = err.message;
  }
});

/* ---------------- ajustes ----------------
   Gravador e presets moraram numa aba até aqui. São configuração feita uma vez por app,
   e disputavam espaço de igual para igual com o controle em si. */

el("btn-settings").addEventListener("click", () => {
  el("settings-modal").hidden = false;
});
el("settings-close").addEventListener("click", () => closeModal("settings-modal"));

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

// Parar de pesquisar quando o app sai da tela poupa bateria e a TV.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else if (!app.hidden) startPolling();
});

/* ---------------- banner de ação ---------------- */

function showBanner(message, variant = "") {
  const banner = el("banner");
  banner.textContent = message;
  banner.className = "banner" + (variant ? ` is-${variant}` : "");
  banner.hidden = false;
}

function hideBannerLater(delay = 4000) {
  setTimeout(() => {
    el("banner").hidden = true;
  }, delay);
}

function watchAction() {
  if (actionTimer) clearInterval(actionTimer);
  actionTimer = setInterval(async () => {
    try {
      const action = await api("/api/action");
      if (action.status === "running") {
        // O `step` diz em que ponto a abertura está. Sem ele eram ~25s de banner
        // parado, e uma falha no meio parecia que nada tinha acontecido.
        showBanner(action.step ? `${action.label} — ${action.step}` : action.label + " …");
        return;
      }
      clearInterval(actionTimer);
      actionTimer = null;
      if (action.status === "error") {
        showBanner("Falhou: " + action.detail, "error");
      } else if (action.status === "done") {
        showBanner(action.label + " — pronto", "done");
        hideBannerLater();
      }
    } catch {
      clearInterval(actionTimer);
      actionTimer = null;
    }
  }, 1200);
}

/* ---------------- controle ---------------- */

async function press(name, times = 1) {
  try {
    await post("/api/button", { name, times });
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

// Único retorno físico possível num PWA. O iOS ignora, mas onde existe é o que confirma
// o toque sem obrigar a olhar para o celular — que é como um controle deveria funcionar.
function buzz(ms = 12) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

for (const button of document.querySelectorAll("[data-button]")) {
  // pointerdown em vez de click: o direcional precisa responder no toque,
  // e o click do Safari só dispara ~100ms depois de soltar.
  // Sem preventDefault: ele suprimia os eventos de compatibilidade do WebKit e levava
  // junto o estado :active — o controle mais usado não dava retorno visual nenhum.
  button.addEventListener("pointerdown", () => {
    buzz();
    press(button.dataset.button);
  });
}

/* ---------------- área de deslize ----------------
   Um passo de seta a cada STEP_PX percorridos: dá rolagem longa contínua sem
   long-press, e cada passo é um toque de botão que o gravador de macro registra. */

const STEP_PX = 46;
// Abaixo disto o dedo não saiu do lugar: é um toque, não um deslize.
const TAP_PX = 12;

let padStart = null;
let padSpent = { x: 0, y: 0 };

function padEcho(symbol) {
  const echo = el("pad-echo");
  echo.textContent = symbol;
  echo.classList.add("is-on");
  clearTimeout(padEcho.timer);
  padEcho.timer = setTimeout(() => echo.classList.remove("is-on"), 220);
}

function padSteps(delta) {
  return Math.trunc(delta / STEP_PX);
}

el("pad").addEventListener("pointerdown", (event) => {
  el("pad").setPointerCapture(event.pointerId);
  el("pad").classList.add("is-touched");
  padStart = { x: event.clientX, y: event.clientY, moved: false };
  padSpent = { x: 0, y: 0 };
});

el("pad").addEventListener("pointermove", (event) => {
  if (!padStart) return;
  const dx = event.clientX - padStart.x;
  const dy = event.clientY - padStart.y;

  // Um eixo por vez: um deslize humano nunca é perfeitamente reto, e sem isto cada
  // movimento na diagonal mandaria setas para dois lados ao mesmo tempo.
  const horizontal = Math.abs(dx) > Math.abs(dy);
  const steps = horizontal ? padSteps(dx) - padSpent.x : padSteps(dy) - padSpent.y;
  if (!steps) return;

  if (horizontal) padSpent.x += steps;
  else padSpent.y += steps;
  padStart.moved = true;

  const name = horizontal ? (steps > 0 ? "RIGHT" : "LEFT") : (steps > 0 ? "DOWN" : "UP");
  padEcho(horizontal ? (steps > 0 ? "▶" : "◀") : (steps > 0 ? "▼" : "▲"));
  buzz(8);
  press(name, Math.min(Math.abs(steps), 30));
});

function padRelease(event) {
  if (!padStart) return;
  const moved =
    padStart.moved ||
    Math.abs(event.clientX - padStart.x) > TAP_PX ||
    Math.abs(event.clientY - padStart.y) > TAP_PX;
  padStart = null;
  el("pad").classList.remove("is-touched");
  if (!moved) {
    padEcho("OK");
    buzz();
    press("ENTER");
  }
}

el("pad").addEventListener("pointerup", padRelease);
el("pad").addEventListener("pointercancel", () => {
  padStart = null;
  el("pad").classList.remove("is-touched");
});

// Decide pelo estado REAL da TV, não pela configuração: com TV_MAC preenchido o botão
// mandava sempre "ligar" e nunca desligava nada.
el("btn-power").addEventListener("click", async () => {
  const turningOff = tvOnline;
  const path = turningOff ? "/api/power/off" : "/api/power/on";
  try {
    await post(path);
    showBanner(turningOff ? "Desligando a TV…" : "Ligando a TV…");
    // A TV leva alguns segundos para responder ao novo estado; sem isto o próximo
    // toque no botão ainda pensaria que ela está como antes.
    setTimeout(refreshStatus, 4000);
    hideBannerLater();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
});

el("type-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = el("type-text");
  const text = input.value;
  if (!text) return;
  try {
    await post("/api/text", { text, enter: true });
    input.value = "";
    showBanner("Texto enviado");
    hideBannerLater(1800);
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
});

async function launch(serviceId, label) {
  try {
    await post("/api/launch", { service_id: serviceId });
    showBanner("Abrindo " + label + "…");
    hideBannerLater();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

/* ---------------- abas ---------------- */

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    for (const other of document.querySelectorAll(".tab")) {
      const active = other === tab;
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-selected", active ? "true" : "false");
    }
    for (const name of ["remote", "find"]) {
      el("tab-" + name).hidden = tab.dataset.tab !== name;
    }
  });
}

/* ---------------- busca ---------------- */

el("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = el("search-input").value.trim();
  if (!query) return;
  const results = el("results");
  results.innerHTML = "";
  el("search-hint").textContent = "Buscando…";
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(query));
    el("search-hint").textContent = data.results.length ? "" : "Nada encontrado.";
    for (const title of data.results) results.appendChild(renderTitle(title));
  } catch (err) {
    el("search-hint").textContent = err.message;
  }
});

function renderTitle(title) {
  const card = document.createElement("article");
  card.className = "card";

  const poster = document.createElement("img");
  poster.loading = "lazy";
  poster.alt = "";
  poster.width = 64;
  poster.height = 96;
  if (title.poster_url) poster.src = title.poster_url;
  card.appendChild(poster);

  const meta = document.createElement("div");
  meta.className = "meta";

  const heading = document.createElement("h3");
  heading.textContent = title.name;
  meta.appendChild(heading);

  const sub = document.createElement("p");
  sub.className = "sub";
  const kind = title.media_type === "tv" ? "Série" : "Filme";
  sub.textContent = [kind, title.year].filter(Boolean).join(" · ");
  meta.appendChild(sub);

  const actions = document.createElement("div");
  actions.className = "actions";

  if (title.services.length) {
    for (const service of title.services) {
      const button = document.createElement("button");
      button.className = "primary";
      button.textContent = service.label;
      button.addEventListener("click", () => openTitle(service, title));
      actions.appendChild(button);

      if (title.media_type === "tv") {
        const episodes = document.createElement("button");
        episodes.textContent = "Episódios";
        episodes.addEventListener("click", () => openEpisodePicker(service, title));
        actions.appendChild(episodes);
      }
    }
  } else {
    const none = document.createElement("span");
    none.className = "none";
    // Distinguir "não está em lugar nenhum" de "está num serviço que a TV não abre"
    // evita o usuário achar que o app quebrou.
    none.textContent = title.providers.length
      ? "Só em: " + title.providers.join(", ")
      : "Indisponível por assinatura no Brasil";
    actions.appendChild(none);
  }

  meta.appendChild(actions);
  card.appendChild(meta);
  return card;
}

async function openTitle(service, title, season, episode) {
  try {
    await post("/api/open", {
      service_id: service.id,
      title: title.name,
      year: title.year,
      tmdb_id: title.tmdb_id,
      season: season || null,
      episode: episode || null,
    });
    const suffix = episode ? ` T${season}E${episode}` : "";
    showBanner("Abrindo " + service.label + ": " + title.name + suffix + " …");
    watchAction();
  } catch (err) {
    showBanner(err.message, "error");
    hideBannerLater();
  }
}

/* ---------------- modais ---------------- */

function closeModal(id) {
  el(id).hidden = true;
  if (id === "ep-modal") episodeTarget = null;
}

// Toque no fundo escuro e Esc também fecham: um modal que só sai pelo ✕ é
// indistinguível de tela travada se o botão não responder.
for (const id of ["name-modal", "ep-modal", "settings-modal"]) {
  el(id).addEventListener("click", (event) => {
    if (event.target === el(id)) closeModal(id);
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const id of ["name-modal", "ep-modal", "settings-modal"]) {
    if (!el(id).hidden) closeModal(id);
  }
});

/* ---------------- episódios ---------------- */

let episodeTarget = null;   // {service, title} enquanto o modal está aberto

el("ep-close").addEventListener("click", () => closeModal("ep-modal"));

async function openEpisodePicker(service, title) {
  episodeTarget = { service, title };
  el("ep-title").textContent = title.name;
  el("ep-seasons").innerHTML = "";
  el("ep-list").innerHTML = "";
  setEpisodeMessage("Carregando temporadas…");
  el("ep-modal").hidden = false;

  // Dito antes de escolher, não depois de falhar: sem a macro calibrada o app
  // para na página da série e o resto é com o controle.
  const warn = el("ep-warn");
  const shortcut = (config.shortcuts || []).find((item) => item.id === service.id);
  if (shortcut && !shortcut.can_pick_episode) {
    warn.textContent =
      `A navegação até o episódio ainda não foi gravada para ${service.label}: `
      + "a série vai abrir, mas o episódio você escolhe na TV.";
    warn.hidden = false;
  } else {
    warn.hidden = true;
  }

  try {
    const data = await api(`/api/seasons/${title.tmdb_id}`);
    if (!data.seasons.length) {
      setEpisodeMessage("Sem temporadas listadas para este título.");
      return;
    }
    for (const season of data.seasons) {
      const button = document.createElement("button");
      button.textContent = "T" + season.season_number;
      button.addEventListener("click", () => selectSeason(button, season));
      el("ep-seasons").appendChild(button);
    }
    selectSeason(el("ep-seasons").firstElementChild, data.seasons[0]);
  } catch (err) {
    setEpisodeMessage(err.message, "error");
  }
}

function setEpisodeMessage(text, variant) {
  const list = el("ep-list");
  list.innerHTML = "";
  const message = document.createElement("p");
  message.className = variant === "error" ? "error" : "muted";
  message.textContent = text;
  list.appendChild(message);
}

async function selectSeason(button, season) {
  for (const other of el("ep-seasons").children) {
    other.classList.toggle("is-active", other === button);
  }
  setEpisodeMessage("Carregando episódios…");
  // Guarda quem estava aberto: trocar de temporada rápido poderia deixar a resposta
  // antiga sobrescrever a nova.
  const target = episodeTarget;

  try {
    const data = await api(
      `/api/episodes/${target.title.tmdb_id}/${season.season_number}`
    );
    if (episodeTarget !== target) return;
    const list = el("ep-list");
    list.innerHTML = "";
    for (const episode of data.episodes) {
      list.appendChild(renderEpisode(episode));
    }
  } catch (err) {
    if (episodeTarget === target) setEpisodeMessage(err.message, "error");
  }
}

function renderEpisode(episode) {
  const row = document.createElement("button");
  row.className = "ep";

  const still = document.createElement("img");
  still.loading = "lazy";
  still.alt = "";
  still.width = 84;
  still.height = 47;
  if (episode.still_url) still.src = episode.still_url;

  const meta = document.createElement("div");
  meta.className = "ep-meta";

  const num = document.createElement("div");
  num.className = "ep-num";
  num.textContent = `T${episode.season_number} E${episode.episode_number}`;

  const name = document.createElement("div");
  name.className = "ep-name";
  name.textContent = episode.name;

  meta.append(num, name);
  if (episode.runtime) {
    const sub = document.createElement("div");
    sub.className = "ep-sub";
    sub.textContent = `${episode.runtime} min`;
    meta.appendChild(sub);
  }

  row.append(still, meta);
  row.addEventListener("click", () => {
    const { service, title } = episodeTarget;
    closeModal("ep-modal");
    openTitle(service, title, episode.season_number, episode.episode_number);
  });
  return row;
}

/* ---------------- início ---------------- */

if (pin) {
  api("/api/status").then(enter).catch(forgetPin);
} else {
  gate.hidden = false;
}
