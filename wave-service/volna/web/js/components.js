// Общие куски интерфейса: строки треков, карточки, полки, шапки страниц, настройки волны.
import { cover, duration, h, icon } from "./dom.js";
import { queueSwipe } from "./gestures.js";

export function pageHeader(title, { back = false, actions = [] } = {}) {
  return h(
    "header",
    { class: "page-head" },
    back
      ? h("button", { class: "icon-btn", type: "button", "aria-label": "Назад", onClick: () => history.back() }, icon("back"))
      : null,
    h("h1", { class: "page-head__title" }, title),
    h("span", { class: "page-head__actions" }, ...actions),
  );
}

export function sectionTitle(text, action) {
  return h("div", { class: "section-title" }, h("h2", {}, text), action || null);
}

let trackActions = null;

/** Обработчик кнопки «⋯» у трека: его регистрирует main.js (меню действий). */
export function setTrackActions(handler) {
  trackActions = handler;
}

/** Ссылка внутри нажимаемой строки или карточки: своё нажатие, строку не запускает. */
function innerLink(cls, text, hash) {
  return h("span", {
    class: `${cls} is-link`,
    onClick: (event) => {
      event.stopPropagation();
      location.hash = hash;
    },
  }, text);
}

/** Строка и карточка — не <button>: внутри них есть ссылки на артиста, а вложенные кнопки не работают. */
function pressable(onPress) {
  return {
    role: "button",
    tabindex: "0",
    onClick: onPress,
    onKeydown: (event) => {
      if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      onPress(event);
    },
  };
}

export function trackRow(track, { number, onPlay, player, showCover = true, queueable = true } = {}) {
  const current = player?.track?.id === track.id;
  const row = h(
    "div",
    { class: `track${current ? " is-current" : ""}`, dataset: { id: track.id } },
    h("span", { class: "track__swipe", "aria-hidden": "true" }, icon("queue"), "В очередь"),
    h(
      "div",
      { class: "track__main", ...pressable(onPlay) },
      number !== undefined ? h("span", { class: "track__no" }, current ? icon("wave") : String(number)) : null,
      showCover ? cover(track.cover, "cover--sm") : null,
      h(
        "span",
        { class: "track__text" },
        h("span", { class: "track__title" }, track.title),
        track.reason || !track.artist_id
          ? h("span", { class: "track__sub" }, track.reason || track.artist)
          : innerLink("track__sub", track.artist, `#/artist/${encodeURIComponent(track.artist_id)}`),
      ),
      track.liked ? icon("heart", "track__liked") : null,
      h("span", { class: "track__time" }, duration(track.duration)),
    ),
    queueable && player
      ? h("button", { class: "icon-btn track__queue", type: "button", "aria-label": `В очередь: ${track.title}`, title: "В очередь", onClick: () => player.enqueue(track) }, icon("queue"))
      : null,
    trackActions
      ? h("button", { class: "icon-btn track__more", type: "button", "aria-label": `Действия: ${track.title}`, onClick: () => trackActions(track) }, icon("more"))
      : null,
  );
  if (queueable && player) queueSwipe(row, track, player);
  return row;
}

/** Длинные списки показываем порциями, как только прокрутка доходит до конца: иначе страница тормозит. */
function inBatches(box, count, size, append) {
  let shown = 0;
  const sentinel = h("div", { class: "batch-end" });
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) more();
  }, { root: document.getElementById("view"), rootMargin: "800px" });
  function more() {
    const end = Math.min(shown + size, count);
    for (; shown < end; shown++) append(shown);
    box.append(sentinel);
    if (shown >= count) {
      observer.disconnect();
      sentinel.remove();
    }
  }
  more();
  if (shown < count) observer.observe(sentinel);
  return observer;
}

/**
 * Список треков. show — сколько строк видно сначала (у артиста: «Все треки» открывает остальные);
 * играет всегда весь список. before(track) — вызывается перед запуском (история поиска).
 */
export function trackList(tracks, { player, context, numbered = false, showCover = true, show, before } = {}) {
  const list = h("div", { class: "tracks" });
  const makeRow = (i) => trackRow(tracks[i], {
    number: numbered ? tracks[i].track_no || i + 1 : undefined,
    showCover,
    player,
    onPlay: () => {
      before?.(tracks[i]);
      player.playList(tracks, i, context);
    },
  });
  let observer = null;
  const fill = (count) => {
    observer?.disconnect();
    list.replaceChildren();
    observer = inBatches(list, count, 100, (i) => list.append(makeRow(i)));
  };
  fill(show ? Math.min(show, tracks.length) : tracks.length);
  list.showAll = () => fill(tracks.length);
  list.markLiked = () => fill(list.querySelectorAll(".track").length);
  list.showFirst = () => fill(Math.min(show, tracks.length));
  // подсветка текущего трека без перерисовки всего списка
  const sync = () => {
    for (const row of list.children) row.classList.toggle("is-current", row.dataset.id === player.track?.id);
  };
  player.addEventListener("change", sync);
  list.cleanup = () => {
    observer?.disconnect();
    player.removeEventListener("change", sync);
  };
  return list;
}

export function card(item, { onClick } = {}) {
  const round = item.type === "artist";
  const sub = item.subtitle || item.artist;
  return h(
    "div",
    { class: `card card--${item.type}`, ...pressable(onClick) },
    cover(item.cover, round ? "cover--round" : ""),
    h("span", { class: "card__title" }, item.title),
    sub && item.type === "album" && item.artist_id
      ? innerLink("card__sub", sub, `#/artist/${encodeURIComponent(item.artist_id)}`)
      : sub ? h("span", { class: "card__sub" }, sub) : null,
  );
}

export function grid(items, onItem) {
  const box = h("div", { class: "grid" });
  box.observer = inBatches(box, items.length, 120, (i) => box.append(card(items[i], { onClick: () => onItem(items[i]) })));
  return box;
}

/** Поиск внутри раздела коллекции: фильтрует то, что уже загружено, сразу при вводе. */
export function filterField(value, placeholder, onChange) {
  const input = h("input", { class: "search__input", type: "search", placeholder, "aria-label": placeholder, autocomplete: "off", value });
  input.addEventListener("input", () => onChange(input.value));
  return h("label", { class: "search" }, icon("search"), input);
}

export function matches(query, ...fields) {
  const norm = (s) => (s || "").toLowerCase().replaceAll("ё", "е");
  const q = norm(query).trim();
  return !q || fields.some((f) => norm(f).includes(q));
}

/** «Показать все · N» / «Свернуть» под длинным списком. */
export function showMore(label, onToggle) {
  let open = false;
  const button = h("button", { class: "btn show-more", type: "button", onClick: () => {
    open = !open;
    button.textContent = open ? "Свернуть" : label;
    onToggle(open);
  } }, label);
  return h("div", { class: "show-more__box" }, button);
}

export function shelf(title, items, onItem) {
  return h(
    "section",
    { class: "shelf" },
    sectionTitle(title),
    h("div", { class: "shelf__row" }, ...items.map((item) => card(item, { onClick: () => onItem(item) }))),
  );
}

export function empty(text) {
  return h("p", { class: "empty" }, text);
}

export function loading() {
  return h("div", { class: "loading", "aria-label": "Загрузка" }, h("span"), h("span"), h("span"));
}

export function chips(options, value, onChange, { segmented = false } = {}) {
  const box = h("div", { class: segmented ? "segmented" : "chips", role: "group" });
  const render = (current) => {
    for (const button of box.children) button.setAttribute("aria-pressed", String(button.dataset.value === current));
  };
  for (const [key, label] of options) {
    box.append(
      h("button", {
        type: "button",
        dataset: { value: key },
        onClick: () => {
          render(key);
          onChange(key);
        },
      }, label),
    );
  }
  render(value);
  box.setValue = render;
  return box;
}

export function toggle(label, value, onChange) {
  const button = h("button", { class: "switch", type: "button", role: "switch", "aria-checked": String(Boolean(value)), "aria-label": label });
  button.addEventListener("click", () => {
    const next = button.getAttribute("aria-checked") !== "true";
    button.setAttribute("aria-checked", String(next));
    onChange(next);
  });
  return h("div", { class: "row" }, h("span", {}, label), button);
}

export function slider({ label, min, max, step, value, format, left, right, onInput, onChange }) {
  const output = h("output", {}, format(value));
  const input = h("input", { type: "range", min, max, step, value });
  input.addEventListener("input", () => {
    output.textContent = format(Number(input.value));
    onInput?.(Number(input.value));
  });
  input.addEventListener("change", () => onChange(Number(input.value)));
  const box = h(
    "label",
    { class: "slider" },
    h("span", { class: "slider__head" }, h("span", {}, label), output),
    input,
    h("span", { class: "slider__scale" }, h("span", {}, left), h("span", {}, right)),
  );
  box.setValue = (v, text) => {
    if (document.activeElement !== input) input.value = String(v);
    output.textContent = text ?? format(v);
  };
  return box;
}

// --- настройки волны: одни и те же контролы в шторке на главной и в разделе настроек ---

export const WAVE_LABELS = {
  character: [["favorite", "Любимое"], ["balanced", "Баланс"], ["discover", "Незнакомое"], ["forgotten", "Забытое"]],
  mood: [["any", "Любое"], ["energetic", "Бодрое"], ["happy", "Весёлое"], ["calm", "Спокойное"], ["sad", "Грустное"]],
  activity: [["any", "Любое"], ["wake", "Просыпаюсь"], ["commute", "В дороге"], ["work", "Работаю"], ["workout", "Тренируюсь"], ["sleep", "Засыпаю"]],
};
const CHARACTER_HINTS = {
  favorite: "Больше того, что вы уже любите и часто дослушиваете.",
  balanced: "Любимое вперемешку с новым, похожим на ваш вкус.",
  discover: "В основном треки, которые вы ещё не слышали.",
  forgotten: "Отмеченное и любимое, что давно не звучало.",
};
const CHARACTER_DEFAULTS = {
  favorite: { temperature: 0.35, repeat_days: 2 },
  balanced: { temperature: 0.6, repeat_days: 3 },
  discover: { temperature: 1.0, repeat_days: 30 },
  forgotten: { temperature: 0.5, repeat_days: 60 },
};

export function waveSummary(s) {
  if (!s) return "Баланс";
  const label = (group, key) => (WAVE_LABELS[group].find(([k]) => k === key) || [])[1];
  const parts = [label("character", s.character)];
  if (s.mood !== "any") parts.push(label("mood", s.mood));
  if (s.activity !== "any") parts.push(label("activity", s.activity));
  if (s.no_vocals) parts.push("Без слов");
  return parts.filter(Boolean).join(" · ");
}

const DEFAULT_SETTINGS = {
  character: "balanced", mood: "any", activity: "any", no_vocals: false,
  temperature: null, diversity: 0.5, repeat_days: null, context_aware: true, steer: 0.65, similar_only: false,
};

export function waveSettingsPanel(player, { onShake } = {}) {
  let s = { ...DEFAULT_SETTINGS, ...(player.settings || {}) };
  const change = async (patch) => {
    s = { ...s, ...patch };
    render();
    try {
      s = await player.changeWaveSettings(patch);
      render();
    } catch (error) {
      hint.textContent = error.message;
    }
  };
  const defaults = () => CHARACTER_DEFAULTS[s.character] || CHARACTER_DEFAULTS.balanced;
  const hint = h("p", { class: "hint" });
  const character = chips(WAVE_LABELS.character, s.character, (v) => change({ character: v }), { segmented: true });
  const mood = chips(WAVE_LABELS.mood, s.mood, (v) => change({ mood: v }));
  const activity = chips(WAVE_LABELS.activity, s.activity, (v) => change({ activity: v }));
  const noVocals = toggle("Без слов", s.no_vocals, (v) => change({ no_vocals: v }));
  const steered = player.kind === "steer" && player.title;
  const similarOnly = toggle(steered ? `Только похожее на «${player.title}»` : "Волна по треку: только похожее", s.similar_only, (v) => change({ similar_only: v }));
  const context = toggle("Учитывать время суток и выходные", s.context_aware, (v) => change({ context_aware: v }));
  const temperature = slider({
    label: "Смелость подбора", min: 0.1, max: 1.5, step: 0.05, value: s.temperature ?? defaults().temperature,
    format: (v) => v.toFixed(2), left: "предсказуемо", right: "смело", onChange: (v) => change({ temperature: v }),
  });
  const diversity = slider({
    label: "Разнообразие", min: 0, max: 1, step: 0.05, value: s.diversity,
    format: (v) => `${Math.round(v * 100)}%`, left: "одно звучание", right: "всё разное", onChange: (v) => change({ diversity: v }),
  });
  const repeat = slider({
    label: "Не повторять треки", min: 0, max: 60, step: 1, value: s.repeat_days ?? defaults().repeat_days,
    format: (v) => (v === 0 ? "можно сразу" : `${Math.round(v)} дн.`), left: "можно сразу", right: "2 месяца",
    onChange: (v) => change({ repeat_days: v }),
  });

  const steer = slider({
    label: "Волна по треку и плейлисту", min: 0.1, max: 0.95, step: 0.05, value: s.steer,
    format: (v) => `${Math.round(v * 100)}% похожего`, left: "больше своего вкуса", right: "ближе к источнику",
    onChange: (v) => change({ steer: v }),
  });

  function render() {
    character.setValue(s.character);
    mood.setValue(s.mood);
    activity.setValue(s.activity);
    hint.textContent = CHARACTER_HINTS[s.character] || "";
    const t = s.temperature ?? defaults().temperature;
    const r = s.repeat_days ?? defaults().repeat_days;
    temperature.setValue(t, `${t.toFixed(2)}${s.temperature == null ? " · авто" : ""}`);
    diversity.setValue(s.diversity);
    repeat.setValue(r, `${r === 0 ? "можно сразу" : `${Math.round(r)} дн.`}${s.repeat_days == null ? " · авто" : ""}`);
    steer.setValue(s.steer);
    steer.hidden = Boolean(s.similar_only);
    steerHint.hidden = Boolean(s.similar_only);
  }

  const steerHint = h("p", { class: "hint" }, "Сколько в волне по треку, альбому или плейлисту — и в продолжении после них — похожего на источник. Остальное — ваш вкус, подтянутый к нему. Пропуски похожего ослабляют смещение на ходу.");

  const panel = h(
    "div",
    { class: "wave-settings" },
    group("Характер", character, hint),
    group("Настроение", mood),
    group(null, noVocals, similarOnly),
    group(
      "Тонкая настройка",
      temperature,
      diversity,
      repeat,
      steer,
      steerHint,
      context,
      h("button", { class: "link-btn", type: "button", onClick: () => change({ temperature: null, repeat_days: null, diversity: 0.5 }) },
        "Вернуть значения характера"),
    ),
    group("Занятие", activity),
    onShake ? h("button", { class: "wide-btn", type: "button", onClick: onShake }, "Встряхнуть волну") : null,
  );
  render();
  return panel;
}

export function group(label, ...children) {
  return h("div", { class: "group" }, label ? h("div", { class: "group__label" }, label) : null, ...children);
}
