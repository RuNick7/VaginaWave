// Плейлист по ссылке для тех, у кого нет аккаунта: что внутри и, если владелец разрешил, простой
// плеер. Отдельно от сайта: гостю не нужны волна и вкладки, а отчёты о прослушивании некому писать.
import { get, rel } from "./api.js";
import { cover, h, icon, longDuration, plural, toast } from "./dom.js";
import { empty, loading, trackRow } from "./components.js";

const token = location.pathname.split("/").filter(Boolean).pop();
const view = document.getElementById("view");
const mini = document.getElementById("mini");
const audio = document.getElementById("audio");

window.Topo?.setDim(true);

function openInApp() {
  // на Android сразу в приложение; если его нет, останемся на этой странице
  const back = encodeURIComponent(location.href);
  return `intent://share/${token}#Intent;scheme=vaginawave;package=app.volna;S.browser_fallback_url=${back};end`;
}

function render(page) {
  document.title = page.title;
  const label = `${page.count} ${plural(page.count, ["трек", "трека", "треков"])}`;
  const player = page.guests ? setupPlayer(page.tracks) : null;
  const rows = page.tracks.map((track, i) => trackRow(track, {
    onPlay: () => (player ? player.play(i) : toast("Чтобы слушать, войдите в аккаунт")),
  }));
  player?.onChange((current) => {
    rows.forEach((row, i) => row.classList.toggle("is-current", i === current));
  });
  const signIn = h("a", { class: `btn${player ? "" : " btn--primary"}`, href: `#/shared/${token}` },
    icon("artist"), player ? "Войти и сохранить себе" : "Войти, чтобы слушать");
  const nodes = [
    h("div", { class: "entity" },
      cover(page.cover, "cover--xl"),
      h("div", { class: "entity__text" },
        h("span", { class: "entity__kind" }, "Плейлист"),
        h("h1", { class: "entity__title" }, page.title),
        h("span", { class: "entity__meta" }, [label, longDuration(page.duration)].join(" · ")),
      ),
    ),
    h("div", { class: "actions" },
      player ? h("button", { class: "btn btn--primary", type: "button", onClick: () => player.play(0) }, icon("play"), "Слушать") : null,
      signIn,
      /Android/i.test(navigator.userAgent) ? h("a", { class: "btn", href: openInApp() }, icon("wave"), "Открыть в приложении") : null,
    ),
    player ? null : h("p", { class: "hint share-hint" }, "Слушать могут те, у кого есть аккаунт на этом сервере. Владелец может открыть плейлист и для остальных."),
    ...rows,
  ];
  view.replaceChildren(...nodes.filter(Boolean));
}

function setupPlayer(tracks) {
  let current = -1;
  let notify = () => {};
  const title = h("span", { class: "mini__title" });
  const sub = h("span", { class: "mini__sub" });
  const art = h("span", { class: "mini__cover" });
  const bar = h("span", { class: "mini__bar" });
  const toggle = h("button", { class: "icon-btn", type: "button", onClick: () => (audio.paused ? audio.play() : audio.pause()) });
  mini.append(
    bar,
    h("div", { class: "mini__open" }, art, h("span", { class: "mini__text" }, title, sub)),
    h("button", { class: "icon-btn", type: "button", "aria-label": "Назад", onClick: () => step(-1) }, icon("prev")),
    toggle,
    h("button", { class: "icon-btn", type: "button", "aria-label": "Дальше", onClick: () => step(1) }, icon("next")),
  );

  function play(i) {
    current = i;
    const track = tracks[i];
    audio.src = rel(track.stream);
    audio.play().catch(() => {}); // браузер мог не дать играть до нажатия: кнопка останется «играть»
    mini.hidden = false;
    document.body.classList.add("has-mini");
    art.replaceChildren(cover(track.cover, "cover--sm"));
    title.textContent = track.title;
    sub.textContent = track.artist;
    bar.style.width = "0%";
    notify(i);
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: track.album,
        artwork: [{ src: new URL(rel(track.cover), document.baseURI).href }],
      });
    }
  }

  function step(delta) {
    if (current < 0) return;
    const i = current + delta;
    if (i < 0) audio.currentTime = 0;
    else if (i < tracks.length) play(i);
  }

  function showState() {
    toggle.replaceChildren(icon(audio.paused ? "play" : "pause"));
    toggle.setAttribute("aria-label", audio.paused ? "Играть" : "Пауза");
  }

  audio.addEventListener("play", showState);
  audio.addEventListener("pause", showState);
  audio.addEventListener("ended", () => step(1));
  audio.addEventListener("error", () => toast("Трек не включился: возможно, владелец закрыл доступ"));
  audio.addEventListener("timeupdate", () => {
    // длину берём из трека: у перекодированного потока браузер её не знает
    const total = tracks[current]?.duration;
    bar.style.width = total ? `${Math.min(100, (audio.currentTime / total) * 100)}%` : "0%";
  });
  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("previoustrack", () => step(-1));
    navigator.mediaSession.setActionHandler("nexttrack", () => step(1));
  }
  showState();
  return { play, onChange: (fn) => { notify = fn; } };
}

view.append(loading());
get(`/api/share/${encodeURIComponent(token)}`)
  .then(render)
  .catch((error) => view.replaceChildren(empty(error.message)));
