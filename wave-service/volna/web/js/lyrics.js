// Текст песни: панель на месте круга на главной и экран на всё окно, как в Spotify.
// Синхронизированный текст подсвечивает текущую строку и сам прокручивается; если листать
// самому, он уступает и через несколько секунд возвращается к песне. Нажатие на строку
// перематывает к ней.
import { get } from "./api.js";
import { duration, h, icon } from "./dom.js";
import { trackSwipe } from "./gestures.js";

const cache = new Map();

function fetchLyrics(trackId) {
  if (!cache.has(trackId)) {
    cache.set(trackId, get(`/api/lyrics/${encodeURIComponent(trackId)}`).catch(() => {
      cache.delete(trackId);
      return { synced: false, lines: [] };
    }));
  }
  return cache.get(trackId);
}

/** Какая строка поётся сейчас; -1 — текст без времени или ещё до первой строки. */
function currentLine(lyrics, position) {
  if (!lyrics.synced) return -1;
  let found = -1;
  lyrics.lines.forEach((line, i) => {
    if ((line.time ?? 0) <= position + 0.25) found = i;
  });
  return found;
}

export function lyricsView(player, { big = false, onReady } = {}) {
  const box = h("div", { class: `lyrics${big ? " lyrics--big" : ""}` });
  let lyrics = null;
  let forTrack = null;
  let rows = [];
  let current = -1;
  let holdUntil = 0;
  let token = 0;

  const hold = () => {
    holdUntil = Date.now() + 4000;
  };
  box.addEventListener("wheel", hold, { passive: true });
  box.addEventListener("touchmove", hold, { passive: true });

  async function load() {
    const track = player.track;
    if (!track) {
      forTrack = null;
      lyrics = null;
      box.replaceChildren();
      return;
    }
    if (track.id === forTrack) return;
    forTrack = track.id;
    lyrics = null;
    box.replaceChildren();
    const mine = ++token;
    const data = await fetchLyrics(track.id);
    if (mine !== token) return;
    const lines = data.lines || [];
    lyrics = { lines, synced: Boolean(data.synced) && lines.every((l) => l.time !== null && l.time !== undefined) };
    render();
    onReady?.();
  }

  function render() {
    current = -1;
    holdUntil = 0;
    rows = lyrics.lines.map((line) => {
      return h(lyrics.synced ? "button" : "p", {
        class: "lyrics__line",
        type: lyrics.synced ? "button" : undefined,
        onClick: lyrics.synced ? () => {
          if (player.duration) player.seek(line.time / player.duration);
          holdUntil = 0;
        } : undefined,
      }, line.text.trim() || "♪");
    });
    box.classList.toggle("is-synced", lyrics.synced);
    box.replaceChildren(
      ...(!lyrics.synced && big && rows.length ? [h("p", { class: "hint" }, "Текст без разметки по времени")] : []),
      ...rows,
    );
    box.scrollTop = 0;
    tick();
  }

  function tick() {
    if (!lyrics?.synced) return;
    const i = currentLine(lyrics, player.position);
    if (i === current) return;
    current = i;
    rows.forEach((row, k) => {
      row.classList.toggle("is-active", k === i);
      row.classList.toggle("is-past", k < i);
    });
    if (i >= 0 && Date.now() >= holdUntil) {
      box.scrollTo({ top: rows[i].offsetTop - box.clientHeight * (big ? 1 / 3 : 0.3), behavior: "smooth" });
    }
  }

  return {
    el: box,
    load,
    tick,
    /** Текст загружен для этого трека и в нём есть строки. */
    ready: (track) => Boolean(track) && forTrack === track.id && Boolean(lyrics?.lines.length),
    /** Загрузка закончилась, а текста нет. */
    missing: (track) => Boolean(track) && forTrack === track.id && lyrics !== null && !lyrics.lines.length,
  };
}

/** Экран на всё окно: открывается кнопкой на панели текста или клавишей T. */
export function setupLyricsScreen(player, box) {
  const view = lyricsView(player, { big: true, onReady: () => render() });
  const title = h("div", { class: "lyrics-full__title" });
  const artist = h("div", { class: "lyrics-full__artist" });
  const none = h("p", { class: "lyrics-full__none" }, "У этого трека нет текста");
  const bar = h("div", { class: "progress__bar" });
  const position = h("span", {});
  const total = h("span", {});
  const progress = h("div", { class: "progress", onClick: (e) => {
    const rect = progress.getBoundingClientRect();
    player.seek((e.clientX - rect.left) / rect.width);
  } }, bar);
  const toggle = h("button", { class: "ctl ctl--primary", type: "button", onClick: () => player.toggle() });
  const head = h("div", { class: "lyrics-full__text" }, title, artist);
  trackSwipe(head, player);
  box.append(
    h("header", { class: "lyrics-full__head" },
      h("button", { class: "icon-btn", type: "button", "aria-label": "Свернуть", onClick: close }, icon("chevron-down")),
      head,
      h("span", { class: "lyrics-full__spacer" }),
    ),
    h("div", { class: "lyrics-full__body" }, view.el, none),
    h("footer", { class: "lyrics-full__foot" },
      progress,
      h("div", { class: "lyrics-full__times" }, position, total),
      h("div", { class: "controls" },
        h("button", { class: "ctl", type: "button", "aria-label": "Назад", onClick: () => player.previous() }, icon("prev")),
        toggle,
        h("button", { class: "ctl", type: "button", "aria-label": "Дальше", onClick: () => player.next("skip") }, icon("next")),
      ),
    ),
  );

  function render() {
    if (box.hidden) return;
    const t = player.track;
    view.load();
    title.textContent = t?.title || "";
    artist.textContent = t?.artist || "";
    none.hidden = !view.missing(t) && Boolean(t);
    toggle.replaceChildren(icon(player.playing ? "pause" : "play"));
    toggle.setAttribute("aria-label", player.playing ? "Пауза" : "Играть");
    onProgress();
  }

  function onProgress() {
    if (box.hidden) return;
    const d = player.duration;
    bar.style.width = d ? `${Math.min(100, (player.position / d) * 100)}%` : "0%";
    position.textContent = duration(player.position);
    total.textContent = duration(d);
    view.tick();
  }

  function open() {
    box.hidden = false;
    render();
  }

  function close() {
    box.hidden = true;
  }

  player.addEventListener("change", render);
  player.addEventListener("progress", onProgress);
  return { open, close, get isOpen() { return !box.hidden; } };
}
