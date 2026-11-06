// Точка входа: роутер, вкладки, мини-плеер, шторка настроек волны, горячие клавиши.
import { get, post } from "./api.js";
import { cover, h, icon, prefs, toast } from "./dom.js";
import { setTrackActions, waveSettingsPanel } from "./components.js";
import { setupDevices } from "./devices.js";
import { trackSwipe } from "./gestures.js";
import { setupLyricsScreen } from "./lyrics.js";
import { setupOnboarding } from "./onboarding.js";
import { Player } from "./player.js";
import { renderHome } from "./views/home.js";
import { renderAddArtist, renderImport } from "./views/imports.js";
import { renderLibrary } from "./views/library.js";
import { renderLogin } from "./views/login.js";
import { renderMix } from "./views/mix.js";
import { renderQueue } from "./views/queue.js";
import { renderEntity } from "./views/pages.js";
import { renderSearch } from "./views/search.js";
import { renderSettings } from "./views/settings.js";

const player = new Player(document.getElementById("audio"));
const view = document.getElementById("view");
const tabbar = document.getElementById("tabbar");
const mini = document.getElementById("mini");
const sheet = document.getElementById("sheet");
const backdrop = document.getElementById("sheet-backdrop");
const lyricsScreen = setupLyricsScreen(player, document.getElementById("lyrics-full"));
const onboarding = setupOnboarding(player, document.getElementById("onboarding"));

const TABS = [
  ["home", "Главная", "wave", "#/"],
  ["search", "Поиск", "search", "#/search"],
  ["library", "Коллекция", "library", "#/library"],
  ["settings", "Настройки", "settings", "#/settings"],
];
let activeTab = "home";
let cleanup = null;

const ctx = {
  player,
  navigate: (hash) => {
    if (location.hash === hash) route();
    else location.hash = hash;
  },
  openWaveSheet,
  openTrackActions,
  applyInterface,
  openLyrics: () => lyricsScreen.open(),
  openOnboarding: () => onboarding.open(),
};

// --- вкладки и маршруты ---

tabbar.append(
  ...TABS.map(([key, label, ic, hash]) =>
    h("a", { class: "tab", href: hash, dataset: { tab: key } }, icon(ic), h("span", { class: "tab__label" }, label)),
  ),
);

// «Назад» возвращает прокрутку: страницы грузятся не сразу, поэтому пробуем, пока высота не дорастёт.
const scrolls = new Map();
const trail = [];
let shownHash = null;

function restoreScroll(top) {
  let tries = 0;
  const tick = () => {
    view.scrollTop = top;
    if (Math.abs(view.scrollTop - top) > 1 && tries++ < 40) setTimeout(tick, 50);
  };
  if (top) tick();
}

function route() {
  const [section = "", a = "", b = ""] = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (shownHash !== null) scrolls.set(shownHash, view.scrollTop);
  shownHash = location.hash;
  const back = trail.length >= 2 && trail[trail.length - 2] === shownHash;
  if (back) trail.pop();
  else if (trail[trail.length - 1] !== shownHash) trail.push(shownHash);
  cleanup?.();
  cleanup = null;
  view.replaceChildren();
  view.scrollTop = 0;

  if (section === "search") {
    activeTab = "search";
    cleanup = renderSearch(view, ctx);
  } else if (section === "library") {
    activeTab = "library";
    cleanup = renderLibrary(view, ctx, a);
  } else if (section === "queue") {
    cleanup = renderQueue(view, ctx);
  } else if (section === "import") {
    cleanup = renderImport(view, ctx);
  } else if (section === "add-artist") {
    cleanup = renderAddArtist(view, ctx);
  } else if (section === "mix" && a && b) {
    cleanup = renderMix(view, ctx, a, b); // плейлист по треку или артисту
  } else if (["album", "artist", "playlist"].includes(section) && a) {
    cleanup = renderEntity(view, ctx, section, a); // остаёмся во вкладке, из которой пришли
  } else if (section === "settings") {
    activeTab = "settings";
    cleanup = renderSettings(view, ctx, a || b);
  } else {
    activeTab = "home";
    cleanup = renderHome(view, ctx);
  }
  document.body.dataset.route = section || "home";
  for (const tab of tabbar.children) tab.setAttribute("aria-current", tab.dataset.tab === activeTab ? "page" : "false");
  renderMini();
  window.Topo?.setDim(section !== "" && section !== "home");
  if (back) restoreScroll(scrolls.get(shownHash));
}

window.addEventListener("hashchange", route);

// --- мини-плеер на всех вкладках, кроме главной ---

const miniCover = h("span", { class: "mini__cover" });
const miniTitle = h("span", { class: "mini__title" });
const miniSub = h("span", { class: "mini__sub" });
const miniPlay = h("button", { class: "icon-btn", type: "button", "aria-label": "Пауза", onClick: () => player.toggle() });
const miniBar = h("span", { class: "mini__bar" });
const miniOpen = h("button", { class: "mini__open", type: "button", "aria-label": "Открыть плеер", onClick: () => ctx.navigate("#/") },
  miniCover, h("span", { class: "mini__text" }, miniTitle, miniSub));
trackSwipe(miniOpen, player);
mini.append(
  miniBar,
  miniOpen,
  h("button", { class: "icon-btn", type: "button", "aria-label": "Назад", onClick: () => player.previous() }, icon("prev")),
  miniPlay,
  h("button", { class: "icon-btn", type: "button", "aria-label": "Дальше", onClick: () => player.next("skip") }, icon("next")),
);

function renderMini() {
  const t = player.track;
  mini.hidden = !t || document.body.dataset.route === "home";
  document.body.classList.toggle("has-mini", !mini.hidden);
  if (!t) return;
  miniCover.replaceChildren(cover(t.cover, "cover--sm"));
  miniTitle.textContent = t.title;
  miniSub.textContent = t.artist;
  miniPlay.replaceChildren(icon(player.playing ? "pause" : "play"));
  miniPlay.setAttribute("aria-label", player.playing ? "Пауза" : "Продолжить");
}
player.addEventListener("change", renderMini);
player.addEventListener("progress", () => {
  const d = player.duration;
  miniBar.style.width = d ? `${Math.min(100, (player.position / d) * 100)}%` : "0%";
});

// --- шторка: настройки волны и действия с треком ---

let lastFocus = null;

function showSheet(head, body) {
  lastFocus = document.activeElement;
  const close = h("button", { class: "icon-btn", type: "button", "aria-label": "Закрыть", onClick: closeSheet }, icon("close"));
  sheet.replaceChildren(h("header", { class: "sheet__head" }, head, close), body);
  sheet.hidden = false;
  backdrop.hidden = false;
  (sheet.querySelector(".tiles button") || close).focus();
}

function openWaveSheet() {
  showSheet(
    h("h2", { id: "sheet-title" }, "Настроить волну"),
    waveSettingsPanel(player, { onShake: () => { closeSheet(); player.shake(); } }),
  );
}

function openTrackActions(track) {
  const action = (label, ic, run) =>
    h("button", { class: "tile", type: "button", onClick: () => { closeSheet(); run(); } },
      icon(ic, "tile__icon"), h("span", { class: "tile__title" }, label));
  const liked = Boolean(track.liked);
  showSheet(
    h("span", { class: "sheet__track", id: "sheet-title" },
      cover(track.cover, "cover--sm"),
      h("span", { class: "track__text" }, h("span", { class: "track__title" }, track.title), h("span", { class: "track__sub" }, track.artist))),
    h("div", { class: "tiles" },
      action("Играть следующим", "queue", () => player.playNext(track)),
      action("Добавить в очередь", "queue", () => player.enqueue(track)),
      action("Моя волна по треку", "wave", () => {
        player.startWave({ seed: { type: "track", id: track.id } });
        ctx.navigate("#/");
      }),
      action("Собрать плейлист по треку", "mix", () => ctx.navigate(`#/mix/track/${encodeURIComponent(track.id)}`)),
      action(liked ? "Убрать из «Мне нравится»" : "Нравится", "heart", () => likeTrack(track, !liked)),
      track.album_id ? action("Перейти к альбому", "album", () => ctx.navigate(`#/album/${encodeURIComponent(track.album_id)}`)) : null,
      track.artist_id ? action("Перейти к артисту", "artist", () => ctx.navigate(`#/artist/${encodeURIComponent(track.artist_id)}`)) : null,
    ),
  );
}

async function likeTrack(track, liked) {
  if (player.track?.id === track.id) {
    if (Boolean(player.track.liked) !== liked) await player.like();
    return;
  }
  try {
    await post("/api/like", { type: "track", id: track.id, liked });
    track.liked = liked;
    toast(liked ? "Добавлено в «Мне нравится»" : "Убрано из «Мне нравится»");
  } catch (error) {
    toast(error.message);
  }
}

setTrackActions(openTrackActions);

function closeSheet() {
  sheet.hidden = true;
  backdrop.hidden = true;
  lastFocus?.focus?.();
}
backdrop.addEventListener("click", closeSheet);

// --- клавиатура ---

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !sheet.hidden) return closeSheet();
  if (event.key === "Escape" && lyricsScreen.isOpen) return lyricsScreen.close();
  const typing = event.target.closest?.("input, textarea, select");
  if (typing || !sheet.hidden || event.metaKey || event.ctrlKey || event.altKey) return;
  const actions = {
    Space: () => player.toggle(),
    ArrowRight: () => player.next("skip"),
    ArrowLeft: () => player.previous(),
    KeyL: () => player.like(),
    KeyD: () => player.next("dislike"),
    KeyS: () => player.shake(),
    KeyT: () => (lyricsScreen.isOpen ? lyricsScreen.close() : player.track && lyricsScreen.open()),
    KeyQ: () => ctx.navigate("#/queue"),
    Slash: () => ctx.navigate("#/search"),
  };
  const action = actions[event.code];
  if (!action) return;
  if (event.code === "Space" && event.target.closest?.("button, a")) return; // пробел на кнопке нажимает её саму
  event.preventDefault();
  action();
});

// --- фон и интерфейс ---

function applyInterface(p = prefs()) {
  document.body.classList.toggle("color-covers", !p.bwCovers);  // цветные по умолчанию
  window.Topo?.setMode(p.bg);
  window.Topo?.setEnergySource(p.react ? () => player.loudness() : () => 0);
}

(function pulse() {
  const energy = window.Topo ? window.Topo.getEnergy() : 0;
  document.documentElement.style.setProperty("--pulse", (1 + energy * 0.08).toFixed(3));
  requestAnimationFrame(pulse);
})();

// --- вход и старт ---

function showLogin() {
  if (document.body.dataset.route === "login") return;
  player.audio.pause();
  cleanup?.();
  cleanup = null;
  window.removeEventListener("hashchange", route);
  document.body.dataset.route = "login";
  view.replaceChildren();
  renderLogin(view);
}

function start() {
  get("/api/settings").then((s) => {
    player.settings = s.settings;
    player.emit();
  }).catch(() => {});
  get("/api/server").then((s) => {
    document.getElementById("status").textContent = s.mode === "demo" ? "демо-библиотека" : "";
    if (s.syncing) toast("Синхронизирую библиотеку…");
  }).catch((e) => e.status !== 401 && toast("Сервис VaginaWave недоступен"));
  route();
  setupDevices(player, document.getElementById("remote"));
  // первый вход: сначала любимые артисты и предпочтения
  get("/api/onboarding").then((data) => {
    if (!data.done) onboarding.open(data);
  }).catch(() => {});
}

applyInterface();
// сервис, открытый в интернет, просит войти; токен живёт в cookie, повторно входить не нужно
window.addEventListener("volna:login", showLogin);
get("/api/auth/me")
  .then((me) => (me.auth && !me.user ? showLogin() : start()))
  .catch(start);
