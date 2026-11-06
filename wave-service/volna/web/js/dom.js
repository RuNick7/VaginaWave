// Мелкие помощники для DOM: создание элементов, иконки, форматирование.
import { rel } from "./api.js";

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "html") el.innerHTML = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const ICONS = {
  wave: '<path d="M3 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  library: '<path d="M5 4v16M10 4v16M14.5 4.5l5 15"/>',
  settings: '<path d="M10.3 3h3.4l.5 2.4 1.7.7 2-1.4 2.4 2.4-1.4 2 .7 1.7 2.4.5v3.4l-2.4.5-.7 1.7 1.4 2-2.4 2.4-2-1.4-1.7.7-.5 2.4h-3.4l-.5-2.4-1.7-.7-2 1.4-2.4-2.4 1.4-2-.7-1.7L3 13.7v-3.4l2.4-.5.7-1.7-1.4-2 2.4-2.4 2 1.4 1.7-.7z"/><circle cx="12" cy="12" r="3"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  chevron: '<path d="M9 5l7 7-7 7"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  pause: '<path d="M8 5h3v14H8zM13 5h3v14h-3z" fill="currentColor"/>',
  next: '<path d="M6 6l9 6-9 6z"/><path d="M18 6v12"/>',
  prev: '<path d="M18 6l-9 6 9 6z"/><path d="M6 6v12"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  dislike: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M4 4l16 16"/>',
  shuffle: '<path d="M4 7h3c4 0 6 10 10 10h3M4 17h3c1.6 0 2.8-1.6 3.9-3.5M13.1 9.5C14.2 8 15.4 7 17 7h3M18 4l3 3-3 3M18 14l3 3-3 3"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  server: '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01"/>',
  playback: '<path d="M4 9v6h3l5 4V5L7 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.9 1-1.8-.6-1.1 0-2.2 1.2-2.2H17a4 4 0 0 0 4-4c0-5.5-4-10-9-10z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
  database: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  album: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.5"/>',
  artist: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5"/>',
  playlist: '<path d="M4 6h12M4 11h12M4 16h7"/><path d="M16 15.5V9l4-1"/><circle cx="14" cy="16" r="2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  more: '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
  mix: '<path d="M4 6h12M4 11h12M4 16h7"/><path d="M17 14v6M14 17h6"/>',
  sync: '<path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  "chevron-down": '<path d="M6 9l6 6 6-6"/>',
  expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  queue: '<path d="M4 6h16M4 11h16M4 16h8"/><path d="M15 13.5v6l5-3z" fill="currentColor"/>',
  grip: '<path d="M5 9h14M5 15h14"/>',
  lyrics: '<path d="M4 6h12M4 10h12M4 14h7"/><path d="M17 18.5V12l3-1"/><circle cx="15.5" cy="18.5" r="1.5"/>',
  download: '<path d="M12 4v11M7.5 10.5l4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
};

export function icon(name, extraClass = "") {
  const span = document.createElement("span");
  span.className = `ic ${extraClass}`.trim();
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[name] || ""}</svg>`;
  return span;
}

export function plural(n, [one, few, many]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const m = Math.floor(s / 60);
  if (m >= 60) return `${Math.floor(m / 60)} ч ${m % 60} мин`;
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function longDuration(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)} ч ${m % 60} мин` : `${m} мин`;
}

export function ago(ts) {
  if (!ts) return "никогда";
  const minutes = Math.round((Date.now() / 1000 - ts) / 60);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${plural(hours, ["час", "часа", "часов"])} назад`;
  const days = Math.round(hours / 24);
  return `${days} ${plural(days, ["день", "дня", "дней"])} назад`;
}

export function cover(src, extraClass = "") {
  const box = h("span", { class: `cover ${extraClass}`.trim() });
  if (src) box.append(h("img", { src: rel(src), alt: "", loading: "lazy", decoding: "async" }));
  return box;
}

let toastTimer = null;
export function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 3500);
}

export function prefs() {
  try {
    return { bg: "live", react: true, bwCovers: false, ...JSON.parse(localStorage.getItem("volna.ui") || "{}") };
  } catch {
    return { bg: "live", react: true, bwCovers: false };
  }
}

export function savePrefs(patch) {
  const next = { ...prefs(), ...patch };
  try {
    localStorage.setItem("volna.ui", JSON.stringify(next));
  } catch {
    /* приватный режим */
  }
  return next;
}
