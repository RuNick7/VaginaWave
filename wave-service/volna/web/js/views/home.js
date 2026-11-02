// Главная: одна большая кнопка «Моя волна» (она же экран «сейчас играет») и полки ниже.
import { get, rel } from "../api.js";
import { h, icon } from "../dom.js";
import { empty, loading, shelf, waveSummary } from "../components.js";

export function renderHome(root, { player, navigate, openWaveSheet, openTrackActions }) {
  const ring = h("span", { class: "wave__ring", "aria-hidden": "true" });
  const coverBox = h("span", { class: "wave__cover", "aria-hidden": "true" });
  const playIcon = icon("play", "wave__icon");
  const waveButton = h("button", { class: "wave", type: "button", "aria-label": "Запустить «Мою волну»", onClick: () => player.toggle() },
    ring, coverBox, playIcon);
  const title = h("div", { class: "caption__title" });
  const sub = h("div", { class: "caption__sub" });
  const reason = h("div", { class: "caption__reason" });
  const bar = h("div", { class: "progress__bar" });
  const progress = h("div", { class: "progress", onClick: (e) => {
    const rect = progress.getBoundingClientRect();
    player.seek((e.clientX - rect.left) / rect.width);
  } }, bar);
  const like = h("button", { class: "ctl", type: "button", "aria-label": "Нравится", title: "Нравится (L)", onClick: () => player.like() }, icon("heart"));
  const controls = h(
    "div",
    { class: "controls" },
    h("button", { class: "ctl", type: "button", "aria-label": "Не нравится", title: "Не нравится (D)", onClick: () => player.next("dislike") }, icon("dislike")),
    h("button", { class: "ctl", type: "button", "aria-label": "Предыдущий трек", title: "Назад (←)", onClick: () => player.previous() }, icon("prev")),
    like,
    h("button", { class: "ctl", type: "button", "aria-label": "Следующий трек", title: "Дальше (→)", onClick: () => player.next("skip") }, icon("next")),
    h("button", { class: "ctl", type: "button", "aria-label": "Ещё: волна по треку, плейлист по треку", title: "Ещё",
      onClick: () => player.track && openTrackActions(player.track) }, icon("more")),
  );
  const tuneLabel = h("span", {});
  const tune = h("button", { class: "tune-pill", type: "button", "aria-label": "Настроить волну", onClick: openWaveSheet }, icon("sliders"), tuneLabel);
  const backToWaveLabel = h("span", {}, "Включить Мою волну");
  const backToWave = h("button", { class: "tune-pill", type: "button", onClick: () => player.startWave() }, icon("wave"), backToWaveLabel);
  const next = h("div", { class: "next" });

  const hero = h(
    "section",
    { class: "hero" },
    waveButton,
    h("div", { class: "caption" }, title, sub, reason),
    progress,
    controls,
    h("div", { class: "hero__bottom" }, tune, backToWave, next),
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
    title.textContent = t ? t.title : "Моя волна";
    sub.textContent = t ? t.artist : player.loading ? "Настраиваю волну под ваш вкус…" : "Нажмите, и музыка пойдёт сама";
    reason.textContent = t ? player.reason : "";
    progress.hidden = !t;
    controls.hidden = !t;
    like.setAttribute("aria-pressed", String(Boolean(t?.liked)));
    const steered = player.mode === "wave" && player.kind === "steer" && player.title;
    tuneLabel.textContent = steered ? `По «${player.title}» · ${waveSummary(player.settings)}` : waveSummary(player.settings);
    tune.hidden = Boolean(t) && player.mode === "list";
    backToWave.hidden = !(t && (player.mode === "list" || steered)); // из альбома или волны по треку — в обычную волну
    backToWaveLabel.textContent = steered ? "Обычная волна" : "Включить Мою волну";
    const up = player.nextUp;
    next.textContent = up ? `Дальше: ${up.title} · ${up.artist}` : "";
  }
  const onProgress = () => {
    const d = player.duration;
    bar.style.width = d ? `${Math.min(100, (player.position / d) * 100)}%` : "0%";
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
    if (item.type === "track") {
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
