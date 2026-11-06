// Поиск по всей библиотеке: артисты, альбомы, треки, плейлисты. Запрос и найденное живут дольше
// экрана: вернулись из альбома или с другой вкладки — результаты на месте, искать заново не нужно.
// Пока поле пустое, видна история: что открывали из результатов и запросы, по которым нажали Enter.
import { get } from "../api.js";
import { cover, h, icon } from "../dom.js";
import { empty, pageHeader, sectionTitle, shelf, trackList } from "../components.js";

const HISTORY_KEY = "volna.searchHistory";
const HISTORY_LIMIT = 25;
const KIND = { album: "Альбом", artist: "Артист", playlist: "Плейлист" };

let lastQuery = "";
let lastData = null;
let lastFor = "";

const entryKey = (e) => (e.track ? `track:${e.track.id}` : e.item ? `${e.item.type}:${e.item.id}` : `query:${e.query.toLowerCase()}`);

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveHistory(entries) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
  } catch {
    /* приватный режим */
  }
}

function remember(entry) {
  const key = entryKey(entry);
  saveHistory([entry, ...loadHistory().filter((e) => entryKey(e) !== key)].slice(0, HISTORY_LIMIT));
}

/** При выходе из аккаунта: история принадлежит пользователю, а не браузеру. */
export function forgetSearch() {
  lastQuery = "";
  lastData = null;
  lastFor = "";
  try {
    localStorage.removeItem(HISTORY_KEY);
  } catch {
    /* приватный режим */
  }
}

export function renderSearch(root, { player, navigate }) {
  const input = h("input", {
    class: "search__input", type: "search", placeholder: "Трек, альбом, артист или плейлист",
    "aria-label": "Поиск", autocomplete: "off", value: lastQuery,
  });
  const clear = h("button", { class: "icon-btn search__clear", type: "button", "aria-label": "Очистить", onClick: () => {
    input.value = "";
    update(0);
    input.focus();
  } }, icon("close"));
  const results = h("div", { class: "search__results" });
  root.append(
    pageHeader("Поиск"),
    h("label", { class: "search" }, icon("search"), input, clear),
    results,
  );
  const cleanups = [];
  let timer = null;
  let token = 0;

  const open = (item) => {
    remember({ item });
    navigate(`#/${item.type}/${encodeURIComponent(item.id)}`);
  };

  function showHistory() {
    const entries = loadHistory();
    if (!entries.length) {
      results.replaceChildren(empty("Ищите по названию, артисту или альбому. С артиста можно сразу запустить волну."));
      return;
    }
    results.replaceChildren(
      sectionTitle("История поиска", h("button", { class: "link-btn", type: "button", onClick: () => {
        saveHistory([]);
        showHistory();
      } }, "Очистить")),
      ...entries.map(historyRow),
    );
  }

  function historyRow(entry) {
    const { track, item, query } = entry;
    const title = track?.title || item?.title || query;
    const sub = track ? `Трек · ${track.artist}`
      : item ? [KIND[item.type], item.type !== "artist" ? item.subtitle : ""].filter(Boolean).join(" · ") : "";
    const press = () => {
      if (track) {
        remember({ track });
        player.playList([track], 0, { type: "search", id: track.title, title: "Поиск" });
      } else if (item) {
        open(item);
      } else {
        remember({ query });
        input.value = query;
        update(0);
      }
    };
    return h("div", { class: "track history-row" },
      h("button", { class: "track__main", type: "button", onClick: press },
        track || item
          ? cover(track?.cover || item?.cover, `cover--sm${item?.type === "artist" ? " cover--round" : ""}`)
          : h("span", { class: "history-row__icon" }, icon("clock")),
        h("span", { class: "track__text" },
          h("span", { class: "track__title" }, title),
          sub ? h("span", { class: "track__sub" }, sub) : null,
        ),
      ),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Убрать из истории", onClick: () => {
        const key = entryKey(entry);
        saveHistory(loadHistory().filter((e) => entryKey(e) !== key));
        showHistory();
      } }, icon("close")),
    );
  }

  function show(q, data) {
    cleanups.splice(0).forEach((fn) => fn());
    const parts = [];
    if (data.artists.length) parts.push(shelf("Артисты", data.artists, open));
    if (data.tracks.length) {
      const list = trackList(data.tracks, {
        player,
        context: { type: "search", id: q, title: `«${q}»` },
        before: (track) => remember({ track: { ...track, reason: "" } }),
      });
      cleanups.push(list.cleanup);
      parts.push(sectionTitle("Треки"), list);
    }
    if (data.albums.length) parts.push(shelf("Альбомы", data.albums, open));
    if (data.playlists.length) parts.push(shelf("Плейлисты", data.playlists, open));
    results.replaceChildren(...(parts.length ? parts : [empty("Ничего не нашлось.")]));
  }

  async function run() {
    const q = input.value.trim();
    const mine = ++token;
    const data = await get(`/api/search?q=${encodeURIComponent(q)}`).catch((error) => ({ error }));
    if (mine !== token) return;
    if (data.error) {
      results.replaceChildren(empty(data.error.message));
      return;
    }
    lastData = data;
    lastFor = q;
    show(q, data);
  }

  function update(delay) {
    const q = input.value.trim();
    lastQuery = input.value;
    clear.hidden = !input.value;
    clearTimeout(timer);
    if (!q) {
      token++;
      cleanups.splice(0).forEach((fn) => fn());
      showHistory();
      return;
    }
    if (lastData && lastFor === q) {
      show(q, lastData); // вернулись назад — найденное уже есть
      return;
    }
    timer = setTimeout(run, delay);
  }

  input.addEventListener("input", () => update(220));
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !input.value.trim()) return;
    remember({ query: input.value.trim() });
    input.blur();
  });
  update(0);
  if (!lastQuery) requestAnimationFrame(() => input.focus());
  return () => {
    clearTimeout(timer);
    cleanups.forEach((fn) => fn());
  };
}
