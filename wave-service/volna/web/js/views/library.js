// Коллекция: «Мне нравится», плейлисты, альбомы, артисты, недавно слушали, пополнение библиотеки.
import { get } from "../api.js";
import { h, icon, plural } from "../dom.js";
import { chips, empty, filterField, grid, loading, matches, pageHeader, sectionTitle, trackList } from "../components.js";

const SECTIONS = [
  ["liked", "Мне нравится", "heart"],
  ["playlists", "Плейлисты", "playlist"],
  ["albums", "Альбомы", "album"],
  ["artists", "Артисты", "artist"],
  ["recent", "Недавно слушали", "clock"],
];
const FEEDS = [
  ["#/import", "Импорт из Spotify", "playlist", "Плейлист или альбом по ссылке"],
  ["#/add-artist", "Добавить исполнителя", "artist", "Скачать дискографию через Lidarr"],
];
// что было в поиске раздела и какая сортировка: «назад» из альбома возвращает всё как было
const filterMemory = {};

export function renderLibrary(root, ctx, section) {
  if (!section) return renderRoot(root, ctx);
  const renderer = { liked: renderTracks, recent: renderTracks, albums: renderAlbums, artists: renderArtists, playlists: renderPlaylists }[section];
  if (!renderer) return renderRoot(root, ctx);
  return renderer(root, ctx, section);
}

function renderRoot(root, { navigate }) {
  const counts = {};
  const tiles = h("div", { class: "tiles" });
  const recent = h("div", {}, loading());
  root.append(pageHeader("Коллекция"), tiles, recent);

  const renderTiles = () => {
    tiles.replaceChildren(
      ...SECTIONS.map(([key, label, ic]) =>
        h("button", { class: "tile", type: "button", onClick: () => navigate(`#/library/${key}`) },
          icon(ic, "tile__icon"),
          h("span", { class: "tile__title" }, label),
          counts[key] !== undefined ? h("span", { class: "tile__count" }, String(counts[key])) : null,
          icon("chevron", "tile__chevron"),
        ),
      ),
      ...FEEDS.map(([hash, label, ic, sub]) =>
        h("button", { class: "tile tile--tall", type: "button", onClick: () => navigate(hash) },
          icon(ic, "tile__icon"),
          h("span", { class: "tile__text" }, h("span", { class: "tile__title" }, label), h("span", { class: "tile__sub" }, sub)),
          icon("chevron", "tile__chevron"),
        ),
      ),
    );
  };
  renderTiles();

  get("/api/library")
    .then((stats) => {
      Object.assign(counts, { liked: stats.liked, playlists: stats.playlists, albums: stats.albums, artists: stats.artists });
      renderTiles();
    })
    .catch(() => {});
  get("/api/library/albums?sort=newest&limit=12")
    .then(({ items }) => {
      const open = (item) => navigate(`#/album/${encodeURIComponent(item.id)}`);
      recent.replaceChildren(sectionTitle("Недавно добавлены"), items.length ? grid(items, open) : empty("Пока пусто."));
    })
    .catch((error) => recent.replaceChildren(empty(`Библиотека недоступна: ${error.message}`)));
}

function renderTracks(root, { player }, section) {
  const title = section === "liked" ? "Мне нравится" : "Недавно слушали";
  const body = h("div", {}, loading());
  const found = h("div", {});
  root.append(pageHeader(title, { back: true }), body);
  let cleanup = () => {};
  get(`/api/library/${section}`)
    .then(({ tracks }) => {
      if (!tracks.length) {
        body.replaceChildren(empty(section === "liked" ? "Отмечайте треки сердцем — они соберутся здесь." : "История пока пуста."));
        return;
      }
      const context = { type: "library", id: section, title };
      const render = () => {
        cleanup();
        const shown = tracks.filter((t) => matches(filterMemory[section], t.title, t.artist, t.album));
        if (!shown.length) {
          cleanup = () => {};
          found.replaceChildren(empty("Ничего не нашлось."));
          return;
        }
        const list = trackList(shown, { player, context });
        cleanup = list.cleanup;
        found.replaceChildren(
          h("p", { class: "page-meta" }, `${shown.length} ${plural(shown.length, ["трек", "трека", "треков"])}`),
          actionRow(player, shown, context),
          list,
        );
      };
      body.replaceChildren(
        filterField(filterMemory[section] || "", `Найти в «${title}»`, (v) => {
          filterMemory[section] = v;
          render();
        }),
        found,
      );
      render();
    })
    .catch((error) => body.replaceChildren(empty(error.message)));
  return () => cleanup();
}

export function actionRow(player, tracks, context, extra = []) {
  return h(
    "div",
    { class: "actions" },
    h("button", { class: "btn btn--primary", type: "button", onClick: () => player.playList(tracks, 0, context) }, icon("play"), "Слушать"),
    h("button", { class: "btn", type: "button", onClick: () => player.shuffle(tracks, context) }, icon("shuffle"), "Перемешать"),
    ...extra,
  );
}

const ALBUM_SORTS = [["alpha", "А–Я"], ["newest", "Новые"], ["recent", "Недавно слушали"], ["frequent", "Часто"], ["year", "По году"], ["liked", "Любимые"]];
const ARTIST_SORTS = [["alpha", "А–Я"], ["frequent", "Часто слушаете"], ["liked", "Любимые"]];
const sortMemory = { albums: "alpha", artists: "alpha" };

function renderAlbums(root, ctx) {
  return renderSortedGrid(root, ctx, "albums", "Альбомы", ALBUM_SORTS, "album");
}

function renderArtists(root, ctx) {
  return renderSortedGrid(root, ctx, "artists", "Артисты", ARTIST_SORTS, "artist");
}

/** Сетка с поиском: показывает только то, что подходит под запрос. */
function filteredGrid(body, kind, title, items, open, none) {
  const found = h("div", {});
  let current = null;
  const render = () => {
    current?.observer?.disconnect();
    const shown = items.filter((i) => matches(filterMemory[kind], i.title, i.subtitle));
    current = shown.length ? grid(shown, open) : null;
    found.replaceChildren(current || empty(items.length ? "Ничего не нашлось." : none));
  };
  body.replaceChildren(
    ...(items.length ? [filterField(filterMemory[kind] || "", `Найти в «${title}»`, (v) => {
      filterMemory[kind] = v;
      render();
    })] : []),
    found,
  );
  render();
  return () => current?.observer?.disconnect();
}

function renderSortedGrid(root, { navigate }, kind, title, sorts, type) {
  const body = h("div", {}, loading());
  let cleanup = () => {};
  const load = (sort) => {
    sortMemory[kind] = sort;
    cleanup();
    body.replaceChildren(loading());
    get(`/api/library/${kind}?sort=${sort}&limit=5000`)
      .then(({ items }) => {
        const open = (item) => navigate(`#/${type}/${encodeURIComponent(item.id)}`);
        cleanup = filteredGrid(body, kind, title, items, open, "Здесь пока пусто.");
      })
      .catch((error) => body.replaceChildren(empty(error.message)));
  };
  root.append(pageHeader(title, { back: true }), chips(sorts, sortMemory[kind], load), body);
  load(sortMemory[kind]);
  return () => cleanup();
}

function renderPlaylists(root, { navigate }) {
  const body = h("div", {}, loading());
  let cleanup = () => {};
  root.append(
    pageHeader("Плейлисты", { back: true }),
    h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onClick: () => navigate("#/import") }, icon("playlist"), "Импорт из Spotify")),
    body,
  );
  get("/api/library/playlists")
    .then(({ items }) => {
      const open = (item) => navigate(`#/playlist/${encodeURIComponent(item.id)}`);
      cleanup = filteredGrid(body, "playlists", "Плейлисты", items, open, "Плейлистов пока нет: их можно создать в Navidrome или импортировать из Spotify.");
    })
    .catch((error) => body.replaceChildren(empty(error.message)));
  return () => cleanup();
}
