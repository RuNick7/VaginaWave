// Пополнение библиотеки: импорт плейлиста по ссылке через Explo и докачка исполнителя через Lidarr.
import { get, post } from "../api.js";
import { cover, h, icon, toast } from "../dom.js";
import { empty, loading, pageHeader } from "../components.js";

/** Плейлист или альбом Spotify, Apple Music: Explo докачает недостающее, плейлист появится в Navidrome. */
export function renderImport(root, { navigate }) {
  const input = h("input", {
    class: "field__input", type: "url", inputmode: "url", autocomplete: "off", spellcheck: "false",
    placeholder: "https://open.spotify.com/playlist/…", "aria-label": "Ссылка на плейлист",
  });
  const button = h("button", { class: "btn btn--primary", type: "submit" }, icon("playlist"), "Импортировать");
  const error = h("p", { class: "field__hint login__error", role: "alert" });
  const form = h("form", { class: "form", onSubmit: start },
    h("p", { class: "hint" }, "Ссылка на плейлист или альбом Spotify или Apple Music. Чего нет в библиотеке, Explo докачает, а плейлист появится у вас в Navidrome."),
    input,
    h("div", { class: "actions" }, button),
    error,
    h("p", { class: "hint" }, "«Любимые треки» Spotify по ссылке не отдаёт: выделите их в Spotify и добавьте в обычный плейлист."),
  );
  const list = h("div", {}, loading());
  root.append(pageHeader("Импорт", { back: true }), form, list);
  let timer = null;
  let alive = true;

  async function refresh() {
    try {
      const data = await get("/api/imports");
      if (!alive) return;
      if (!data.available) {
        form.hidden = true;
        list.replaceChildren(empty("Импорт не настроен: сервису VaginaWave нужен доступ к Explo на сервере."));
        return;
      }
      list.replaceChildren(
        ...(data.items.length ? [h("div", { class: "group__label import-label" }, "Импорты")] : []),
        ...data.items.map(row),
      );
      // пока что-то импортируется — показываем, как идут дела
      clearTimeout(timer);
      timer = setTimeout(refresh, data.items.some((i) => !["done", "failed"].includes(i.status)) ? 3000 : 15000);
    } catch (e) {
      if (alive) error.textContent = e.message;
    }
  }

  async function start(event) {
    event.preventDefault();
    const url = input.value.trim();
    if (!url) return;
    button.disabled = true;
    error.textContent = "";
    try {
      await post("/api/imports", { url });
      input.value = "";
      await refresh();
    } catch (e) {
      error.textContent = e.message;
    } finally {
      button.disabled = false;
    }
  }

  function row(job) {
    const ready = job.status === "done" && job.playlist_id;
    const active = !["done", "failed"].includes(job.status);
    const missing = job.status === "done" && job.missing?.length
      ? `Не нашлось: ${job.missing.slice(0, 3).join("; ")}${job.missing.length > 3 ? "…" : ""}` : "";
    return h(ready ? "button" : "div", {
      class: "import-row", type: ready ? "button" : undefined,
      onClick: ready ? () => navigate(`#/playlist/${encodeURIComponent(job.playlist_id)}`) : undefined,
    },
      job.cover ? cover(job.cover, "cover--sm") : icon("playlist", "tile__icon"),
      h("span", { class: "track__text" },
        h("span", { class: "track__title" }, job.name || job.url),
        h("span", { class: "import-row__note" }, job.message),
        missing ? h("span", { class: "import-row__note import-row__missing" }, missing) : null,
      ),
      active ? h("span", { class: "spinner", "aria-label": "Идёт импорт" }) : ready ? icon("chevron", "tile__chevron") : null,
    );
  }

  refresh();
  return () => {
    alive = false;
    clearTimeout(timer);
  };
}

/** Найти исполнителя в MusicBrainz и поставить на докачку через Lidarr: Soularr скачает альбомы, EP и синглы. */
export function renderAddArtist(root) {
  const input = h("input", {
    class: "search__input", type: "search", placeholder: "Имя исполнителя", "aria-label": "Имя исполнителя", autocomplete: "off",
  });
  const results = h("div", {});
  const intro = h("p", { class: "hint" }, "Найдите исполнителя — Soularr скачает его альбомы, EP и синглы и будет докачивать новые релизы.");
  const search = h("label", { class: "search" }, icon("search"), input);
  root.append(pageHeader("Добавить исполнителя", { back: true }), intro, search, results);
  let timer = null;
  let token = 0;

  get("/api/lidarr").then(({ available }) => {
    if (available) return;
    intro.hidden = true;
    search.hidden = true;
    results.replaceChildren(empty("Докачка дискографий не настроена: сервису VaginaWave нужен доступ к Lidarr на сервере."));
  }).catch(() => {});

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 400); // поиск идёт в MusicBrainz — не на каждую букву
  });

  async function run() {
    const q = input.value.trim();
    const mine = ++token;
    if (!q) return results.replaceChildren();
    results.replaceChildren(loading());
    try {
      const { artists } = await get(`/api/lidarr/search?q=${encodeURIComponent(q)}`);
      if (mine !== token) return;
      results.replaceChildren(...(artists.length ? artists.map(row) : [empty("В MusicBrainz никого не нашлось.")]));
    } catch (e) {
      if (mine === token) results.replaceChildren(empty(e.message));
    }
  }

  function row(artist) {
    const action = h("span", { class: "found-artist__action" });
    const showState = (following) => action.replaceChildren(following
      ? h("span", { class: "tile__sub" }, "Качается")
      : h("button", { class: "btn", type: "button", onClick: follow }, "Скачать"));
    async function follow() {
      showState(true);
      try {
        await post("/api/lidarr/follow", { mbid: artist.mbid });
        toast(`«${artist.name}» — на докачке`);
      } catch (e) {
        showState(false);
        toast(e.message);
      }
    }
    showState(artist.following);
    const note = artist.disambiguation || artist.type;
    return h("div", { class: "found-artist" },
      cover(artist.image, "cover--round found-artist__cover"),
      h("span", { class: "track__text" },
        h("span", { class: "track__title" }, artist.name),
        note ? h("span", { class: "track__sub" }, note) : null,
        artist.overview ? h("span", { class: "found-artist__overview" }, artist.overview) : null,
      ),
      action,
    );
  }

  requestAnimationFrame(() => input.focus());
}
