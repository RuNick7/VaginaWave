// Главная: одна большая кнопка «Моя волна» (она же экран «сейчас играет») и полки ниже.
import { get, rel } from "../api.js";
import { h, icon, prefs, savePrefs, toast } from "../dom.js";
import { empty, loading, shelf, waveSummary } from "../components.js";
import { trackSwipe } from "../gestures.js";
import { lyricsView } from "../lyrics.js";

export function renderHome(root, { player, navigate, openWaveSheet, openTrackActions, openLyrics }) {
  const ring = h("span", { class: "wave__ring", "aria-hidden": "true" });
  const coverBox = h("span", { class: "wave__cover", "aria-hidden": "true" });
  const playIcon = icon("play", "wave__icon");
  const waveButton = h("button", { class: "wave", type: "button", "aria-label": "Запустить «Мою волну»", onClick: () => player.toggle() },
    ring, coverBox, playIcon);
  // текст песни встаёт на место круга
  let askedText = false;
  const text = lyricsView(player, { onReady: () => {
    if (askedText && text.missing(player.track)) toast("У этого трека нет текста");
    askedText = false;
    render();
  } });
  const panel = h("div", { class: "lyrics-panel" }, text.el,
    h("button", { class: "icon-btn lyrics-panel__expand", type: "button", "aria-label": "Текст на весь экран", title: "На весь экран (T)", onClick: openLyrics }, icon("expand")));
  const art = h("div", { class: "hero__art" }, waveButton, panel);
  // название ведёт к альбому, артист — к артисту; свайп по ним и по обложке листает треки
  const title = h("button", { class: "caption__title", type: "button", onClick: () => {
    if (player.track?.album_id) navigate(`#/album/${encodeURIComponent(player.track.album_id)}`);
  } });
  const sub = h("button", { class: "caption__sub", type: "button", onClick: () => {
    if (player.track?.artist_id) navigate(`#/artist/${encodeURIComponent(player.track.artist_id)}`);
  } });
  const reason = h("div", { class: "caption__reason" });
  const caption = h("div", { class: "caption" }, title, sub, reason);
  trackSwipe(art, player);
  trackSwipe(caption, player);
  const bar = h("div", { class: "progress__bar" });
  const progress = h("div", { class: "progress", onClick: (e) => {
    const rect = progress.getBoundingClientRect();
    player.seek((e.clientX - rect.left) / rect.width);
  } }, bar);
  const like = h("button", { class: "ctl", type: "button", "aria-label": "Нравится", title: "Нравится (L)", onClick: () => player.like() }, icon("heart"));
  const playPause = h("button", { class: "ctl ctl--primary", type: "button", title: "Пауза (пробел)", onClick: () => player.toggle() });
  const controls = h(
    "div",
    { class: "controls" },
    h("button", { class: "ctl", type: "button", "aria-label": "Не нравится", title: "Не нравится (D)", onClick: () => player.next("dislike") }, icon("dislike")),
    h("button", { class: "ctl", type: "button", "aria-label": "Предыдущий трек", title: "Назад (←)", onClick: () => player.previous() }, icon("prev")),
    playPause,
    h("button", { class: "ctl", type: "button", "aria-label": "Следующий трек", title: "Дальше (→)", onClick: () => player.next("skip") }, icon("next")),
    like,
  );
  const lyricsButton = h("button", { class: "icon-btn", type: "button", "aria-label": "Текст песни", title: "Текст песни (T)", onClick: () => {
    const on = !prefs().lyrics;
    savePrefs({ lyrics: on });
    askedText = on;
    if (on && text.missing(player.track)) {
      toast("У этого трека нет текста");
      askedText = false;
    }
    render();
  } }, icon("lyrics"));
  const queueButton = h("button", { class: "icon-btn", type: "button", "aria-label": "Очередь", title: "Очередь (Q)", onClick: () => navigate("#/queue") }, icon("queue"));
  const moreButton = h("button", { class: "icon-btn", type: "button", "aria-label": "Ещё: волна по треку, плейлист по треку", title: "Ещё",
    onClick: () => player.track && openTrackActions(player.track) }, icon("more"));
  const tuneLabel = h("span", {});
  const tune = h("button", { class: "tune-pill", type: "button", "aria-label": "Настроить волну", onClick: openWaveSheet }, icon("sliders"), tuneLabel);
  const backToWave = h("button", { class: "tune-pill", type: "button", onClick: () => player.startWave() }, icon("wave"), h("span", {}, "Включить Мою волну"));
  const plainWave = h("button", { class: "hero__link", type: "button", onClick: () => player.startWave() }, "Вернуться к обычной Моей волне");
  const next = h("div", { class: "next" });

  const hero = h(
    "section",
    { class: "hero" },
    art,
    caption,
    progress,
    controls,
    h("div", { class: "hero__bottom" },
      h("div", { class: "hero__tools" }, lyricsButton, tune, backToWave, queueButton, moreButton),
      plainWave,
      next,
    ),
  );
  const shelves = h("div", { class: "shelves" }, loading());
  root.append(hero, shelves);

  function render() {
    const t = player.track;
    waveButton.classList.toggle("is-loading", player.loading);
    waveButton.classList.toggle("has-cover", Boolean(t));
    waveButton.classList.toggle("is-playing", player.playing);
    coverBox.style.backgroundImage = t?.cover ? `url("${rel(t.cover)}")` : "";
    playIcon.innerHTML = icon(player.playing ? "pause" : "play").innerHTML;
    waveButton.setAttribute("aria-label", t ? (player.playing ? "Пауза" : "Продолжить") : "Запустить «Мою волну»");
    const wantText = Boolean(t) && prefs().lyrics;
    if (wantText) text.load();
    const showText = wantText && text.ready(t);
    panel.hidden = !showText;
    waveButton.hidden = showText;
    lyricsButton.setAttribute("aria-pressed", String(wantText));
    title.textContent = t ? t.title : "Моя волна";
    title.disabled = !t?.album_id;
    sub.textContent = t ? t.artist : player.loading ? "Настраиваю волну под ваш вкус…" : "Нажмите, и музыка пойдёт сама";
    sub.disabled = !t?.artist_id;
    reason.textContent = t ? player.reason : "";
    progress.hidden = !t;
    controls.hidden = !t;
    playPause.replaceChildren(icon(player.playing ? "pause" : "play"));
    playPause.setAttribute("aria-label", player.playing ? "Пауза" : "Играть");
    like.setAttribute("aria-pressed", String(Boolean(t?.liked)));
    const steered = player.mode === "wave" && player.kind === "steer" && player.title;
    tuneLabel.textContent = steered ? `По «${player.title}» · ${waveSummary(player.settings)}` : waveSummary(player.settings);
    tune.hidden = Boolean(t) && player.mode === "list";
    backToWave.hidden = !(t && player.mode === "list");
    plainWave.hidden = !(t && steered);
    for (const tool of [lyricsButton, queueButton, moreButton]) tool.hidden = !t;
    queueButton.setAttribute("aria-pressed", String(player.queue.length > 0));
    const up = player.nextUp;
    next.textContent = up ? `${player.queue.length ? "Дальше из очереди" : "Дальше"}: ${up.title} · ${up.artist}` : "";
  }
  const onProgress = () => {
    const d = player.duration;
    bar.style.width = d ? `${Math.min(100, (player.position / d) * 100)}%` : "0%";
    text.tick();
  };
  player.addEventListener("change", render);
  player.addEventListener("progress", onProgress);
  render();
  onProgress();

  get("/api/home")
    .then(({ shelves: data }) => {
      shelves.replaceChildren();
      if (!data.length) shelves.append(empty("Полки появятся, когда в библиотеке будет история прослушиваний."));
      for (const s of data) {
        shelves.append(shelf(s.title, s.items, (item) => openItem(item, s)));
      }
    })
    .catch((error) => shelves.replaceChildren(empty(`Библиотека недоступна: ${error.message}`)));

  function openItem(item, s) {
    if (s.id === "playlists") return player.startWave({ seed: { type: "playlist", id: item.id } });
    if (item.stream) {
      const tracks = s.items;
      return player.playList(tracks, tracks.indexOf(item), { type: "library", id: s.id, title: s.title });
    }
    navigate(`#/${item.type}/${encodeURIComponent(item.id)}`);
  }

  return () => {
    player.removeEventListener("change", render);
    player.removeEventListener("progress", onProgress);
  };
}
