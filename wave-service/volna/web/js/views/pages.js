// Страницы альбома, артиста и плейлиста.
import { get, post } from "../api.js";
import { cover, h, icon, longDuration, plural, toast } from "../dom.js";
import { empty, grid, loading, pageHeader, sectionTitle, showMore, trackList } from "../components.js";
import { actionRow } from "./library.js";

export function renderEntity(root, ctx, type, id) {
  const body = h("div", {}, loading());
  root.append(pageHeader("", { back: true }), body);
  let cleanup = () => {};
  get(`/api/${type}/${encodeURIComponent(id)}`)
    .then((page) => {
      const render = { album: albumPage, artist: artistPage, playlist: playlistPage }[type];
      const { nodes, dispose } = render(page, ctx);
      cleanup = dispose;
      body.replaceChildren(...nodes);
    })
    .catch((error) => body.replaceChildren(empty(error.message)));
  return () => cleanup();
}

function hero(page, meta, { round = false } = {}) {
  return h(
    "div",
    { class: "entity" },
    cover(page.cover, `cover--xl${round ? " cover--round" : ""}`),
    h("div", { class: "entity__text" },
      h("span", { class: "entity__kind" }, { album: "Альбом", artist: "Артист", playlist: "Плейлист" }[page.type]),
      h("h1", { class: "entity__title" }, page.title),
      ...meta,
    ),
  );
}

function likeButton(page) {
  if (page.type === "playlist") return null;
  const button = h("button", { class: "btn btn--icon", type: "button", "aria-label": "Нравится", "aria-pressed": String(Boolean(page.liked)) }, icon("heart"));
  button.addEventListener("click", async () => {
    const liked = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(liked));
    try {
      await post("/api/like", { type: page.type, id: page.id, liked });
      toast(liked ? "Отмечено в Navidrome ★" : "Отметка снята");
    } catch (error) {
      button.setAttribute("aria-pressed", String(!liked));
      toast(error.message);
    }
  });
  return button;
}

function waveButton(player, type, id, label = "Волна", navigate) {
  return h("button", { class: "btn", type: "button", onClick: () => {
    player.startWave({ seed: { type, id } });
    navigate?.("#/");
  } }, icon("wave"), label);
}

/** «Лайкнуть все»: треки отмечаются одной пачкой в Navidrome, сердечки — сразу. */
function likeAllButton(tracks, list) {
  if (!tracks.some((t) => !t.liked)) return null;
  const button = h("button", { class: "btn", type: "button", onClick: async () => {
    button.disabled = true;
    try {
      await post("/api/like/tracks", { track_ids: tracks.map((t) => t.id), liked: true });
      tracks.forEach((t) => { t.liked = true; });
      list.markLiked?.();
      button.remove();
      toast(`Все ${tracksLabel(tracks.length)} в «Мне нравится»`);
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  } }, icon("heart"), "Лайкнуть все");
  return button;
}

/** Дискография артиста через Lidarr: кнопка «Скачать» или сколько альбомов уже скачано. */
function discographyRow(artistId) {
  const box = h("div", { class: "discography" });
  const url = `/api/lidarr/artist/${encodeURIComponent(artistId)}`;
  const show = (d) => {
    if (!d.available) return box.replaceChildren();
    if (d.following) {
      return box.replaceChildren(h("p", { class: "hint" }, d.albums
        ? `Дискография на докачке: есть ${d.have} из ${d.albums} ${plural(d.albums, ["альбома", "альбомов", "альбомов"])}`
        : "Дискография на докачке: Lidarr уточняет список альбомов"));
    }
    const button = h("button", { class: "btn", type: "button", onClick: async () => {
      button.disabled = true;
      button.lastChild.textContent = "Ставлю на докачку…";
      try {
        show(await post(url));
        toast("Дискография поставлена на докачку");
      } catch (error) {
        button.disabled = false;
        button.lastChild.textContent = "Скачать дискографию";
        toast(error.message);
      }
    } }, icon("download"), h("span", {}, "Скачать дискографию"));
    box.replaceChildren(button);
  };
  get(url).then(show).catch(() => {});
  return box;
}

function albumPage(page, { player, navigate }) {
  const context = { type: "album", id: page.id, title: page.title };
  const list = trackList(page.tracks, { player, context, numbered: true, showCover: false });
  const meta = [
    h("button", { class: "entity__link", type: "button", onClick: () => page.artist_id && navigate(`#/artist/${encodeURIComponent(page.artist_id)}`) }, page.subtitle),
    h("span", { class: "entity__meta" }, [page.year || null, tracksLabel(page.tracks.length), longDuration(page.duration)].filter(Boolean).join(" · ")),
  ];
  return {
    nodes: [hero(page, meta), actionRow(player, page.tracks, context, [waveButton(player, "album", page.id, "Волна", navigate), likeAllButton(page.tracks, list), likeButton(page)]), list],
    dispose: list.cleanup,
  };
}

const ARTIST_TRACKS = 5;
const ARTIST_ALBUMS = 4;

function artistPage(page, { player, navigate }) {
  const context = { type: "artist", id: page.id, title: page.title };
  // все треки по популярности: сначала пять, остальные по кнопке
  const tracks = page.tracks?.length ? page.tracks : page.top;
  const list = trackList(tracks, { player, context, show: ARTIST_TRACKS });
  const plays = page.plays ? `${page.plays} ${plural(page.plays, ["прослушивание", "прослушивания", "прослушиваний"])}` : "";
  const meta = [h("span", { class: "entity__meta" }, [page.subtitle, plays].filter(Boolean).join(" · "))];
  const actions = h(
    "div",
    { class: "actions" },
    h("button", { class: "btn btn--primary", type: "button", onClick: () => {
      player.startWave({ seed: { type: "artist", id: page.id } });
      navigate("#/");
    } }, icon("wave"), "Волна по артисту"),
    h("button", { class: "btn", type: "button", onClick: () => player.playList(tracks, 0, context) }, icon("play"), "Популярное"),
    h("button", { class: "btn", type: "button", onClick: () => navigate(`#/mix/artist/${encodeURIComponent(page.id)}`) }, icon("mix"), "Плейлист"),
    likeButton(page),
  );
  const open = (item) => navigate(`#/album/${encodeURIComponent(item.id)}`);
  const albums = h("div", {}, grid(page.albums.slice(0, ARTIST_ALBUMS), open));
  return {
    nodes: [
      hero(page, meta, { round: true }),
      actions,
      discographyRow(page.id),
      tracks.length ? sectionTitle("Популярные треки") : null,
      tracks.length ? list : null,
      tracks.length > ARTIST_TRACKS
        ? showMore(`Все треки · ${tracks.length}`, (all) => (all ? list.showAll() : list.showFirst()))
        : null,
      page.albums.length ? sectionTitle("Альбомы") : null,
      page.albums.length ? albums : null,
      page.albums.length > ARTIST_ALBUMS
        ? showMore(`Все альбомы · ${page.albums.length}`, (all) => albums.replaceChildren(grid(all ? page.albums : page.albums.slice(0, ARTIST_ALBUMS), open)))
        : null,
    ].filter(Boolean),
    dispose: list.cleanup,
  };
}

function playlistPage(page, { player, navigate }) {
  const context = { type: "playlist", id: page.id, title: page.title };
  const list = trackList(page.tracks, { player, context });
  const meta = [h("span", { class: "entity__meta" }, [tracksLabel(page.tracks.length), longDuration(page.duration)].join(" · "))];
  return {
    nodes: [hero(page, meta), actionRow(player, page.tracks, context, [waveButton(player, "playlist", page.id, "Волна", navigate), likeAllButton(page.tracks, list)]), list],
    dispose: list.cleanup,
  };
}

function tracksLabel(n) {
  return `${n} ${plural(n, ["трек", "трека", "треков"])}`;
}
