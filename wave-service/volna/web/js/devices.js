// «Играет на X. Слушать здесь?», как Spotify Connect. Пока играет (раз в 5 с) и пока вкладка
// открыта (раз в 10 с) сообщаем сервису, что играет здесь, и узнаём, что играет на других
// устройствах. Забрали музыку себе — тот же трек с того же места и его очередь, а прежнее
// устройство встаёт на паузу; забрали отсюда — здесь пауза.
import { get, post, put } from "./api.js";
import { h, icon, toast } from "./dom.js";

const ID_KEY = "volna.device";

export function setupDevices(player, bar) {
  const id = deviceId();
  const name = deviceName();
  let others = [];
  let busy = false;
  let timer = null;

  async function report() {
    if (busy) return;
    busy = true;
    try {
      const t = player.track;
      const answer = await put("/api/devices/me", {
        device_id: id,
        name,
        kind: "web",
        track_id: t?.id || "",
        position: player.position,
        duration: player.duration,
        playing: player.playing,
        mode: player.mode || "",
        source: player.source,
        next_ids: player.upNextIds(30),
      });
      for (const c of answer.commands || []) {
        if (c.action === "pause" && player.playing) {
          player.audio.pause();
          toast(`Музыка теперь играет на «${c.by || "другом устройстве"}»`);
        }
      }
      others = answer.devices || [];
      render();
    } catch {
      // старый сервис без устройств или нет связи — просто не показываем
    } finally {
      busy = false;
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (player.playing || document.visibilityState === "visible") await report();
      schedule();
    }, player.playing ? 5000 : 10000);
  }

  function render() {
    const d = others.find((o) => o.playing && o.track);
    bar.hidden = !d || player.playing;
    document.body.classList.toggle("has-remote", !bar.hidden);
    if (bar.hidden) return;
    bar.replaceChildren(
      icon("playback", "remote__icon"),
      h("span", { class: "remote__text" },
        h("span", { class: "remote__where" }, `Играет на «${d.name}»`),
        h("span", { class: "remote__track" }, `${d.track.title} · ${d.track.artist}`)),
      h("button", { class: "remote__take", type: "button", onClick: () => takeOver(d) }, "Слушать здесь"),
    );
  }

  // забираем музыку себе: сначала узнаём, что там играет прямо сейчас
  async function takeOver(d) {
    d.playing = false;
    render();
    const fresh = await get(`/api/devices?device_id=${encodeURIComponent(id)}`)
      .then((r) => r.devices.find((o) => o.device_id === d.device_id && o.track))
      .catch(() => null) || d;
    await player.handoff([fresh.track, ...fresh.next], fresh.position, fresh.mode === "wave", fresh.name);
    post(`/api/devices/${encodeURIComponent(d.device_id)}/pause?by=${encodeURIComponent(name)}`).catch(() => {});
  }

  // сменился трек, пауза или play — сообщаем сразу
  let last = "";
  player.addEventListener("change", () => {
    const now = `${player.track?.id}|${player.playing}`;
    if (now !== last) {
      last = now;
      report();
    }
    render();
  });
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && report());
  report();
  schedule();
}

function deviceId() {
  let id = localStorage.getItem(ID_KEY);
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(ID_KEY, id);
  }
  return id;
}

/** «Chrome на Mac», «Safari на iPhone». */
function deviceName() {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /YaBrowser/.test(ua) ? "Яндекс Браузер" : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Браузер";
  const where = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
    : /Mac OS X|Macintosh/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  return where ? `${browser} на ${where}` : browser;
}
