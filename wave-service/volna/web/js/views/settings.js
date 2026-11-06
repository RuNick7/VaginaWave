// Настройки по категориям, как в Tempus и Symfonium.
import { del, get, post, put } from "../api.js";
import { ago, h, icon, plural, prefs, savePrefs, toast } from "../dom.js";
import { chips, empty, group, loading, pageHeader, toggle, waveSettingsPanel, waveSummary } from "../components.js";
import { forgetSearch } from "./search.js";

const CATEGORIES = [
  ["server", "Сервер", "server", "Navidrome и AudioMuse"],
  ["wave", "Моя волна", "wave", "Характер, настроение, смелость подбора"],
  ["playback", "Воспроизведение", "playback", "Качество потока, что играть после альбома"],
  ["interface", "Интерфейс", "palette", "Фон, обложки"],
  ["library", "Библиотека и вкус", "database", "Синхронизация и что система о вас знает"],
  ["about", "О приложении", "info", "Версия, горячие клавиши"],
];

export function renderSettings(root, ctx, section) {
  const renderer = { server: serverPage, wave: wavePage, playback: playbackPage, interface: interfacePage, library: libraryPage, about: aboutPage }[section];
  if (renderer) return renderer(root, ctx);
  return rootPage(root, ctx);
}

function rootPage(root, { navigate, player }) {
  const subtitles = {};
  const list = h("div", { class: "tiles" });
  const render = () =>
    list.replaceChildren(
      ...CATEGORIES.map(([key, title, ic, sub]) =>
        h("button", { class: "tile tile--tall", type: "button", onClick: () => navigate(`#/settings/${key}`) },
          icon(ic, "tile__icon"),
          h("span", { class: "tile__text" }, h("span", { class: "tile__title" }, title), h("span", { class: "tile__sub" }, subtitles[key] || sub)),
          icon("chevron", "tile__chevron"),
        ),
      ),
    );
  render();
  root.append(pageHeader("Настройки"), list);

  subtitles.wave = waveSummary(player.settings);
  render();
  get("/api/server").then((s) => {
    subtitles.server = s.mode === "demo" ? "Демо-библиотека · подключите свой Navidrome" : `Подключено: ${host(s.server.navidrome_url)}`;
    if (s.library) subtitles.library = `${s.library.tracks} ${plural(s.library.tracks, ["трек", "трека", "треков"])} · синхронизировано ${ago(s.library.synced_at)}`;
    render();
  }).catch(() => {});
  get("/api/preferences").then((p) => {
    subtitles.playback = `Качество: ${QUALITY_LABELS[p.quality] || p.quality}`;
    render();
  }).catch(() => {});
  get("/api/auth/me").then((me) => {
    if (!me.auth) return;
    const logout = async () => {
      await post("/api/auth/logout").catch(() => {});
      forgetSearch();
      location.reload();
    };
    root.append(h("button", { class: "link-btn settings-logout", type: "button", onClick: logout }, `Выйти (${me.user})`));
  }).catch(() => {});
}

// --- сервер ---

function serverPage(root, { navigate }) {
  const status = h("div", { class: "status-list" });
  const form = h("form", { class: "form", autocomplete: "on" });
  const field = (name, label, attrs = {}, hint = "") =>
    h("label", { class: "field" },
      h("span", { class: "field__label" }, label),
      h("input", { name, class: "field__input", ...attrs }),
      hint ? h("span", { class: "field__hint" }, hint) : null,
    );
  const passwordHint = h("span", {});
  form.append(
    group("Navidrome",
      field("navidrome_url", "Адрес сервера", { placeholder: "https://music.example.com", inputmode: "url", autocapitalize: "off", spellcheck: "false", required: true }),
      field("navidrome_user", "Логин", { autocomplete: "username", autocapitalize: "off", spellcheck: "false", required: true }),
      field("navidrome_password", "Пароль", { type: "password", autocomplete: "current-password" }),
      passwordHint,
    ),
    group("AudioMuse-AI (для волны)",
      field("audiomuse_url", "Адрес AudioMuse", { placeholder: "http://192.168.1.50:8000", inputmode: "url", autocapitalize: "off", spellcheck: "false" },
        "Без него работают библиотека и плеер, но не «Моя волна»."),
      h("p", { class: "hint" }, "Вход в AudioMuse: API-токен или логин и пароль, под которыми вы входите в его веб-интерфейс. Достаточно одного из двух."),
      field("audiomuse_token", "API-токен", { type: "password", autocomplete: "off" }, "Тот же API_TOKEN, что указан в плагине AudioMuse в Navidrome."),
      field("audiomuse_user", "Логин AudioMuse", { autocomplete: "off", autocapitalize: "off", spellcheck: "false" }),
      field("audiomuse_password", "Пароль AudioMuse", { type: "password", autocomplete: "off" }),
    ),
    status,
    h("div", { class: "actions" },
      h("button", { class: "btn btn--primary", type: "submit" }, "Подключиться"),
      h("button", { class: "btn", type: "button", onClick: () => test() }, "Проверить"),
    ),
    h("p", { class: "hint" }, "Пароль и токен хранятся только на сервере, где работает VaginaWave, и не показываются в интерфейсе."),
  );
  const disconnect = h("button", { class: "link-btn", type: "button", onClick: forget }, "Отключиться и вернуться к демо-библиотеке");
  root.append(pageHeader("Сервер", { back: true }), form, disconnect);
  get("/api/server").then((s) => {
    if (s.can_manage !== false) return;
    form.replaceWith(h("p", { class: "hint" }, "Адреса Navidrome и AudioMuse настраивает администратор Navidrome. Ваши волна, история и настройки — только ваши."));
    disconnect.remove();
  }).catch(() => {});

  const values = () => Object.fromEntries(new FormData(form).entries());
  const showResult = (result) => {
    const line = (name, r) =>
      h("div", { class: `status-line ${r.ok ? "is-ok" : r.ok === false ? "is-bad" : ""}` },
        h("span", { class: "status-line__dot" }),
        h("span", {}, r.ok ? `${name}: подключено${r.version ? ` · ${r.type} ${r.version}` : ""}${r.auth ? ` · вход ${r.auth}` : ""}` : `${name}: ${r.error}`),
      );
    status.replaceChildren(line("Navidrome", result.navidrome), line("AudioMuse", result.audiomuse));
  };

  async function test() {
    if (!form.reportValidity()) return;
    status.replaceChildren(loading());
    try {
      showResult(await post("/api/server/test", values()));
    } catch (error) {
      status.replaceChildren(h("div", { class: "status-line is-bad" }, error.message));
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.replaceChildren(loading());
    try {
      const result = await put("/api/server", values());
      showResult(result);
      toast("Сервер подключён, синхронизирую библиотеку…");
      waitForSync(status);
      load();
    } catch (error) {
      status.replaceChildren(h("div", { class: "status-line is-bad" }, h("span", { class: "status-line__dot" }), error.message));
    }
  });

  async function forget() {
    await del("/api/server");
    toast("Отключено: снова демо-библиотека");
    navigate("#/settings");
  }

  async function load() {
    const s = await get("/api/server");
    disconnect.hidden = s.mode === "demo";
    if (s.server) {
      form.navidrome_url.value = s.server.navidrome_url;
      form.navidrome_user.value = s.server.navidrome_user;
      form.audiomuse_url.value = s.server.audiomuse_url;
      form.navidrome_password.placeholder = s.server.has_password ? "сохранён — оставьте пустым" : "";
      form.audiomuse_token.placeholder = s.server.has_token ? "сохранён — оставьте пустым" : "";
      form.audiomuse_user.value = s.server.audiomuse_user || "";
      form.audiomuse_password.placeholder = s.server.has_audiomuse_password ? "сохранён — оставьте пустым" : "";
      passwordHint.textContent = "";
    } else {
      passwordHint.replaceChildren(h("span", { class: "field__hint" }, "Сейчас играет демо-библиотека. Введите адрес своего Navidrome, логин и пароль."));
    }
  }
  load();
}

async function waitForSync(status) {
  for (let i = 0; i < 300; i++) {
    const s = await get("/api/server").catch(() => null);
    if (s && !s.syncing) {
      const line = s.sync_error
        ? h("div", { class: "status-line is-bad" }, h("span", { class: "status-line__dot" }), `Синхронизация: ${s.sync_error}`)
        : h("div", { class: "status-line is-ok" }, h("span", { class: "status-line__dot" }),
            `Библиотека: ${s.library?.tracks ?? 0} треков, ${s.library?.albums ?? 0} альбомов, ${s.library?.playlists ?? 0} плейлистов`);
      status.append(line);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function host(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

// --- волна ---

function wavePage(root, { player, openOnboarding }) {
  root.append(
    pageHeader("Моя волна", { back: true }),
    h("p", { class: "hint" }, "Те же настройки открываются кнопкой под волной на главной. Меняются на ходу: текущий трек доигрывает, пересобирается очередь."),
    waveSettingsPanel(player, { onShake: () => player.shake() }),
    group(null,
      h("button", { class: "btn", type: "button", onClick: () => openOnboarding() }, icon("artist"), "Настроить вкус заново"),
      h("p", { class: "hint" }, "Снова выбрать любимых артистов и то, как вам слушается."),
    ),
  );
}

// --- воспроизведение ---

const QUALITY_LABELS = { original: "оригинал", 320: "320 кбит/с", 192: "192 кбит/с", 128: "128 кбит/с" };

function playbackPage(root) {
  const body = h("div", {}, loading());
  root.append(pageHeader("Воспроизведение", { back: true }), body);
  get("/api/preferences").then((p) => {
    const save = (patch) => put("/api/preferences", patch).then(() => toast("Сохранено")).catch((e) => toast(e.message));
    body.replaceChildren(
      group("Качество потока",
        chips(Object.entries(QUALITY_LABELS), p.quality, (v) => save({ quality: v })),
        h("p", { class: "hint" }, "Меньше битрейт — меньше мобильного трафика. Navidrome перекодирует в MP3 на лету; «оригинал» отдаёт файл как есть."),
      ),
      group(null,
        toggle("Когда альбом или плейлист закончился, продолжать похожим", p.continue_after_end, (v) => save({ continue_after_end: v })),
        h("p", { class: "hint" }, "Продолжение ищется по всему списку целиком, а не по последней песне."),
      ),
    );
  });
}

// --- интерфейс ---

function interfacePage(root, { applyInterface }) {
  const p = prefs();
  const update = (patch) => applyInterface(savePrefs(patch));
  root.append(
    pageHeader("Интерфейс", { back: true }),
    group("Фон",
      chips([["live", "Живые линии"], ["calm", "Спокойные"], ["off", "Без линий"]], p.bg, (v) => update({ bg: v })),
      h("p", { class: "hint" }, "Топографические линии текут волной. «Спокойные» — медленнее и тише, меньше нагружают телефон."),
    ),
    group(null,
      toggle("Линии реагируют на музыку", p.react, (v) => update({ react: v })),
      toggle("Чёрно-белые обложки", p.bwCovers, (v) => update({ bwCovers: v })),
    ),
  );
}

// --- библиотека и вкус ---

function libraryPage(root) {
  const info = h("div", {}, loading());
  const taste = h("div", {});
  const syncButton = h("button", { class: "btn", type: "button" }, icon("sync"), "Синхронизировать сейчас");
  root.append(pageHeader("Библиотека и вкус", { back: true }), info, h("div", { class: "actions" }, syncButton), taste);

  const loadInfo = async () => {
    const s = await get("/api/server");
    const lib = s.library;
    info.replaceChildren(
      group("Библиотека",
        h("p", { class: "page-meta" }, lib
          ? `${lib.tracks} ${plural(lib.tracks, ["трек", "трека", "треков"])}, ${lib.albums} альбомов, ${lib.artists} артистов, ${lib.playlists} плейлистов. ★ у ${lib.liked} треков.`
          : s.syncing ? "Синхронизация…" : "Ещё не синхронизирована."),
        h("p", { class: "hint" }, lib ? `Последняя синхронизация: ${ago(lib.synced_at)}. Обновляется сама раз в 6 часов.` : ""),
      ),
    );
  };
  syncButton.addEventListener("click", async () => {
    await post("/api/library/sync");
    toast("Синхронизирую библиотеку…");
    info.replaceChildren(loading());
    const wait = async () => {
      const s = await get("/api/server");
      if (s.syncing) return setTimeout(wait, 1000);
      loadInfo();
    };
    wait();
  });
  loadInfo();

  get("/api/taste")
    .then((t) => {
      const s = t.signals || {};
      const count = (n, forms) => `${n ?? 0} ${plural(n ?? 0, forms)}`;
      const list = (items, fmt) =>
        items?.length ? h("ol", { class: "taste__list" }, ...items.slice(0, 6).map((a) => h("li", {}, fmt(a)))) : h("p", { class: "hint" }, "пока мало данных");
      const sourceNames = { audiomuse: "похожие по звучанию", favorites: "из любимого", similar: "похожие на трек", random: "случайные", manual: "вне волны" };
      taste.replaceChildren(
        group("Ваш вкус",
          h("p", { class: "hint" },
            `Учтено: ${count(s.played_tracks, ["трек", "трека", "треков"])} с прослушиваниями, ★ у ${count(s.starred_tracks, ["трека", "треков", "треков"])}, ` +
            `${count(s.starred_albums, ["альбома", "альбомов", "альбомов"])} и ${count(s.starred_artists, ["артиста", "артистов", "артистов"])}, ` +
            `${count(s.playlists, ["плейлист", "плейлиста", "плейлистов"])} и ${count(s.events, ["реакция", "реакции", "реакций"])} в Volna.`),
          h("div", { class: "taste__cols" },
            h("div", {}, h("div", { class: "taste__title" }, "Дослушиваете"), list(t.loved_artists, (a) => `${a.artist} · ${Math.round(a.rate * 100)}%`)),
            h("div", {}, h("div", { class: "taste__title" }, "Чаще пропускаете"), list(t.skipped_artists, (a) => `${a.artist} · ${Math.round(a.skips)} проп.`)),
          ),
          h("div", { class: "taste__title" }, "Кластеры вкуса"),
          h("ol", { class: "taste__list" }, ...t.clusters.slice(0, 8).map((c) =>
            h("li", {}, `${c.label || "без названия"} · ${Math.round(c.share * 100)}%${c.completion_rate != null ? ` · дослушано ${Math.round(c.completion_rate * 100)}%` : ""}`))),
          Object.keys(t.sources || {}).length
            ? h("p", { class: "hint" }, `Дослушиваемость по источникам: ${Object.entries(t.sources).map(([k, v]) => `${sourceNames[k] || k} — ${Math.round(v.rate * 100)}%`).join(", ")}.`)
            : null,
        ),
      );
    })
    .catch((error) => taste.replaceChildren(group("Ваш вкус", empty(error.message))));
}

// --- о приложении ---

function aboutPage(root) {
  const body = h("div", {});
  root.append(pageHeader("О приложении", { back: true }), body);
  get("/api/health").then((s) => {
    body.replaceChildren(
      group("VaginaWave",
        h("p", { class: "page-meta" }, `Версия ${s.version} · ${s.mode === "demo" ? "демо-библиотека" : "свой сервер"}`),
        h("p", { class: "hint" }, "Клиент Navidrome с бесконечной «Моей волной», которая учится на ваших пропусках. Звучание треков анализирует AudioMuse-AI."),
        h("a", { class: "link-btn", href: "https://github.com/RuNick7/VaginaWave", target: "_blank", rel: "noopener" }, "Репозиторий на GitHub"),
      ),
      group("Горячие клавиши",
        h("dl", { class: "keys" },
          ...[["Пробел", "пауза / продолжить"], ["→", "дальше"], ["←", "назад (в альбоме и плейлисте)"], ["L", "нравится"], ["D", "не нравится"], ["S", "встряхнуть волну"], ["T", "текст песни на весь экран"], ["Q", "очередь"], ["/", "поиск"]]
            .flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      ),
    );
  });
}
