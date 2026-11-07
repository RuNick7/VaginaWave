// Страницы альбома, артиста и плейлиста.
import { del, get, post, put } from "../api.js";
import { cover, h, icon, longDuration, plural, toast } from "../dom.js";
import { empty, grid, loading, pageHeader, sectionTitle, showMore, toggle, trackList } from "../components.js";
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

function playlistPage(page, ctx) {
  const { player, navigate } = ctx;
  const context = { type: "playlist", id: page.id, title: page.title };
  const list = trackList(page.tracks, { player, context });
  const meta = [h("span", { class: "entity__meta" }, [tracksLabel(page.tracks.length), longDuration(page.duration)].join(" · "))];
  const share = h("button", { class: "btn", type: "button", onClick: () => openShare(page, ctx) }, icon("share"), "Поделиться");
  return {
    nodes: [hero(page, meta), actionRow(player, page.tracks, context, [waveButton(player, "playlist", page.id, "Волна", navigate), share, likeAllButton(page.tracks, list)]), list],
    dispose: list.cleanup,
  };
}

/** Шторка «Поделиться»: ссылка, переключатель «Слушать без аккаунта» и отзыв ссылки. */
function openShare(page, { showSheet, closeSheet }) {
  const url = `/api/playlist/${encodeURIComponent(page.id)}/share`;
  const box = h("div", { class: "share-sheet" }, loading());
  showSheet(h("h2", { id: "sheet-title" }, "Поделиться плейлистом"), box);

  function show(share) {
    const field = h("input", { class: "field__input", readonly: true, value: share.url, "aria-label": "Ссылка на плейлист", onFocus: (e) => e.target.select() });
    const copy = h("button", { class: "btn btn--primary", type: "button", onClick: async () => {
      try {
        await navigator.clipboard.writeText(share.url);
      } catch {
        field.select(); // сайт открыт не по https: браузер не даёт доступа к буферу обмена
        document.execCommand("copy");
      }
      toast("Ссылка скопирована");
    } }, icon("share"), "Скопировать");
    const send = navigator.share
      ? h("button", { class: "btn", type: "button", onClick: () => navigator.share({ title: page.title, url: share.url }).catch(() => {}) }, "Отправить")
      : null;
    const until = share.guests_until ? new Date(share.guests_until * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "";
    let sure = false;
    const revoke = h("button", { class: "link-btn share-sheet__revoke", type: "button", onClick: async () => {
      if (!sure) {
        sure = true;
        revoke.textContent = "Точно отозвать? Эта ссылка перестанет работать";
        return;
      }
      try {
        await del(url);
        closeSheet();
        toast("Ссылка отозвана");
      } catch (error) {
        toast(error.message);
      }
    } }, "Отозвать ссылку");
    box.replaceChildren(
      h("p", { class: "hint" }, "Те, у кого есть аккаунт на этом сервере, смогут слушать плейлист и сохранить его копию себе. Остальным видно только название, обложки и список треков."),
      field,
      h("div", { class: "actions" }, copy, send),
      toggle("Слушать без аккаунта", share.guests, async (on) => {
        try {
          show(await put(url, { guests: on }));
        } catch (error) {
          toast(error.message);
          show(share);
        }
      }),
      h("p", { class: "hint" }, share.guests
        ? `Слушать может любой, у кого есть ссылка. Само выключится ${until}.`
        : "Если включить, любой, у кого есть ссылка, сможет слушать эти треки без входа 30 дней. Это раздача музыки посторонним: включайте для тех, кому доверяете."),
      revoke,
    );
  }

  post(url).then(show).catch((error) => box.replaceChildren(empty(error.message)));
}

/** Плейлист по ссылке от другого человека: слушать, волна по нему и своя копия. */
export function renderShared(root, ctx, token) {
  const body = h("div", {}, loading());
  root.append(pageHeader("", { back: true }), body);
  let cleanup = () => {};
  get(`/api/share/${encodeURIComponent(token)}`)
    .then((page) => {
      if (page.own) return location.replace(`#/playlist/${encodeURIComponent(page.playlist_id)}`);
      const { player, navigate } = ctx;
      const context = { type: "playlist", id: page.playlist_id, title: page.title };
      const list = trackList(page.tracks, { player, context });
      cleanup = list.cleanup;
      const meta = [
        h("span", { class: "entity__meta" }, `Поделился: ${page.owner}`),
        h("span", { class: "entity__meta" }, [tracksLabel(page.tracks.length), longDuration(page.duration)].join(" · ")),
      ];
      body.replaceChildren(
        hero(page, meta),
        actionRow(player, page.tracks, context, [waveButton(player, "share", page.token, "Волна", navigate), saveButton(page, navigate)]),
        list,
      );
    })
    .catch((error) => body.replaceChildren(empty(error.message)));
  return () => cleanup();
}

function saveButton(page, navigate) {
  const opened = (id) => h("button", { class: "btn", type: "button", onClick: () => navigate(`#/playlist/${encodeURIComponent(id)}`) }, icon("check"), "В вашей коллекции");
  if (page.saved_id) return opened(page.saved_id);
  const button = h("button", { class: "btn", type: "button", onClick: async () => {
    button.disabled = true;
    try {
      const saved = await post(`/api/share/${encodeURIComponent(page.token)}/save`);
      button.replaceWith(opened(saved.id));
      toast(`«${page.title}» — в вашей коллекции`);
    } catch (error) {
      button.disabled = false;
      toast(error.message);
    }
  } }, icon("playlist"), "Сохранить себе");
  return button;
}

function tracksLabel(n) {
  return `${n} ${plural(n, ["трек", "трека", "треков"])}`;
}
