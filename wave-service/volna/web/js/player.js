// Плеер: два режима.
// * wave — очередь держит сервер: на каждое окончание трека сообщаем, чем всё кончилось, и получаем следующий;
// * list — обычная очередь из альбома, плейлиста или поиска. Прослушивания всё равно уходят в /api/events,
//   а когда список закончился, плеер просит у сервера продолжение похожим на весь список.
import { get, post, put, rel } from "./api.js";
import { toast } from "./dom.js";

export class Player extends EventTarget {
  constructor(audio) {
    super();
    this.audio = audio;
    this.mode = null; // "wave" | "list"
    this.track = null;
    this.upcoming = [];
    this.session = null;
    this.kind = "wave";
    this.title = "";
    this.settings = null;
    this.list = [];
    this.index = 0;
    this.context = null; // {type, id, title} для режима list
    this.busy = false;
    this.loading = false;
    this.handoffWave = false; // музыку забрали с устройства, где играла волна: после её треков — волна
    // «Назад» в волне: сыгранные треки (history) и те, к которым надо вернуться (forward).
    // Пока forward не пуст, мы переслушиваем прошлое и сервер волны не трогаем.
    this.history = [];
    this.forward = [];
    this.analyser = null;

    audio.addEventListener("ended", () => this.next("complete"));
    audio.addEventListener("error", () => {
      if (!this.track || !audio.getAttribute("src")) return;
      toast("Трек не воспроизводится, беру следующий");
      setTimeout(() => this.next("error"), 1200);
    });
    for (const type of ["play", "pause", "timeupdate", "loadedmetadata"]) {
      audio.addEventListener(type, () => this.emit(type === "timeupdate" ? "progress" : "change"));
    }
    this.setupMediaSession();
  }

  emit(type = "change") {
    this.dispatchEvent(new Event(type));
  }

  get playing() {
    return Boolean(this.track) && !this.audio.paused;
  }

  get position() {
    return this.audio.currentTime || 0;
  }

  get duration() {
    return Number.isFinite(this.audio.duration) ? this.audio.duration : this.track?.duration || 0;
  }

  // --- запуск ---

  async startWave(options = {}) {
    this.handoffWave = false;
    this.history = [];
    this.forward = [];
    this.setLoading(true);
    try {
      this.applyWave(await post("/api/wave/start", { device: "web", ...options }));
      await this.play();
    } catch (error) {
      toast(error.message);
    } finally {
      this.setLoading(false);
    }
  }

  async playList(tracks, index = 0, context = { type: "other", id: "", title: "" }) {
    if (!tracks.length) return;
    this.mode = "list";
    this.session = null;
    this.handoffWave = false;
    this.history = [];
    this.forward = [];
    this.list = tracks;
    this.index = Math.max(0, Math.min(index, tracks.length - 1));
    this.context = context;
    this.setTrack(this.list[this.index]);
    await this.play();
  }

  // Музыку забрали с другого устройства: тот же трек с того же места, дальше — его очередь.
  async handoff(tracks, position, wave, from) {
    await this.playList(tracks, 0, { type: "other", id: "handoff", title: from });
    this.handoffWave = wave;
    if (position > 0) {
      const seek = () => { this.audio.currentTime = position; };
      if (this.audio.readyState >= 1) seek();
      else this.audio.addEventListener("loadedmetadata", seek, { once: true });
    }
  }

  async shuffle(tracks, context) {
    const copy = [...tracks];
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return this.playList(copy, 0, context);
  }

  // --- переключение ---

  async next(outcome = "skip") {
    if (!this.track || this.busy) return;
    this.busy = true;
    const report = {
      track_id: this.track.id,
      outcome,
      position: this.position,
      duration: this.duration,
    };
    this.audio.pause();
    try {
      if (this.replaying) {
        // переслушивали прошлое: реакцию учитываем как обычное прослушивание и идём обратно вперёд
        this.report(report, { type: "other" });
        this.remember(this.track);
        this.setTrack(this.forward.pop());
        await this.play();
      } else if (this.mode === "wave") {
        const finished = this.track;
        this.applyWave(await post(`/api/wave/${this.session}/advance`, report));
        this.remember(finished);
        await this.play();
      } else {
        this.report(report, this.context);
        if (this.index + 1 < this.list.length) {
          this.index += 1;
          this.setTrack(this.list[this.index]);
          await this.play();
        } else {
          await this.continueAfterList();
        }
      }
    } catch (error) {
      if (error.status === 404 && this.mode === "wave") {
        await this.startWave();
      } else {
        toast(error.message);
      }
    } finally {
      this.busy = false;
    }
  }

  /** «Назад»: в начало трека, если он играет дольше 3 секунд, иначе — к предыдущему. */
  async previous() {
    if (!this.track) return;
    if (this.position > 3) {
      this.audio.currentTime = 0;
      return;
    }
    if (this.mode === "list" && !this.replaying) {
      if (this.index === 0) {
        this.audio.currentTime = 0;
        return;
      }
      this.index -= 1;
      this.setTrack(this.list[this.index]);
    } else {
      const back = this.history.pop();
      if (!back) {
        this.audio.currentTime = 0;
        return;
      }
      this.forward.push(this.track);
      this.setTrack(back);
    }
    await this.play();
  }

  get replaying() {
    return this.mode === "wave" && this.forward.length > 0;
  }

  remember(track) {
    if (!track) return;
    this.history.push(track);
    if (this.history.length > 50) this.history.shift();
  }

  report(report, context) {
    if (report.outcome === "error") return;
    post("/api/events", {
      ...report,
      outcome: report.outcome === "dislike" ? "dislike" : report.outcome,
      context_type: context?.type || "other",
      context_id: context?.id || "",
      device: "web",
    }).catch(() => {});
  }

  async continueAfterList() {
    if (this.handoffWave) return this.startWave(); // на том устройстве играла волна — она и продолжится
    const prefs = await get("/api/preferences").catch(() => ({ continue_after_end: true }));
    if (!prefs.continue_after_end) {
      this.stop();
      return;
    }
    try {
      this.setLoading(true);
      const data = await post("/api/continue", {
        track_ids: this.list.map((t) => t.id),
        title: this.context?.title || "",
        device: "web",
      });
      toast(`Список закончился — дальше похожее на «${this.context?.title || "него"}»`);
      this.applyWave(data);
      await this.play();
    } catch (error) {
      toast(`Список закончился. ${error.message}`);
      this.stop();
    } finally {
      this.setLoading(false);
    }
  }

  stop() {
    this.audio.pause();
    this.audio.currentTime = 0;
    this.emit();
  }

  toggle() {
    if (!this.track) return this.startWave();
    if (this.audio.paused) return this.play();
    this.audio.pause();
  }

  seek(fraction) {
    if (this.duration) this.audio.currentTime = fraction * this.duration;
  }

  // --- реакции ---

  async like() {
    if (!this.track) return;
    const liked = !this.track.liked;
    this.track.liked = liked;
    this.emit();
    try {
      if (this.mode === "wave" && !this.replaying) {
        this.applyWave(await post(`/api/wave/${this.session}/like`, { track_id: this.track.id, liked }));
      } else {
        await post("/api/like", { type: "track", id: this.track.id, liked });
      }
    } catch (error) {
      this.track.liked = !liked;
      this.emit();
      toast(error.message);
    }
  }

  async shake() {
    if (this.mode !== "wave") return this.startWave();
    try {
      this.applyWave(await post(`/api/wave/${this.session}/shake`));
      toast("Волна встряхнута: дальше будет иначе");
    } catch (error) {
      toast(error.message);
    }
  }

  async changeWaveSettings(patch) {
    if (this.mode === "wave" && this.session) {
      try {
        this.applyWave(await post(`/api/wave/${this.session}/settings`, patch));
        return this.settings;
      } catch (error) {
        if (error.status !== 404) throw error;
      }
    }
    this.settings = (await put("/api/settings", patch)).settings;
    this.emit();
    return this.settings;
  }

  // --- внутреннее ---

  applyWave(data) {
    this.mode = "wave";
    this.session = data.session;
    this.kind = data.kind;
    this.title = data.title || "";
    this.upcoming = data.upcoming || [];
    if (data.settings) this.settings = data.settings;
    if (this.replaying) {
      this.emit(); // переслушиваем прошлое: сервер знает про очередь, но играет сейчас не она
      return;
    }
    if (!this.track || !data.track || this.track.id !== data.track.id) this.setTrack(data.track);
    else {
      this.track = data.track;
      this.emit();
    }
  }

  setTrack(track) {
    this.track = track;
    if (!track) {
      this.audio.removeAttribute("src");
      this.emit();
      return;
    }
    this.audio.src = rel(track.stream);
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: track.album,
        artwork: track.cover ? [{ src: new URL(rel(track.cover), location.href).href, sizes: "600x600" }] : [],
      });
    }
    this.emit();
  }

  get nextUp() {
    if (this.replaying) return this.forward[this.forward.length - 1];
    if (this.mode === "wave") return this.upcoming[0] || null;
    return this.list[this.index + 1] || null;
  }

  /** Что будет после текущего трека: для другого устройства, если оно заберёт музыку. */
  upNextIds(limit) {
    const rest = this.mode === "wave" ? this.upcoming : this.list.slice(this.index + 1);
    return rest.slice(0, limit).map((t) => t.id);
  }

  /** Откуда играет: «Моя волна», название альбома или плейлиста. */
  get source() {
    if (this.mode === "wave") return this.kind === "steer" && this.title ? `Моя волна по «${this.title}»` : "Моя волна";
    return this.context?.title || "";
  }

  get reason() {
    if (this.mode === "list" && this.context?.id === "handoff") return `Продолжаю с «${this.context.title}»`;
    if (this.mode === "list") return this.context?.title ? `${contextLabel(this.context.type)}: ${this.context.title}` : "";
    return this.track?.reason || "";
  }

  async play() {
    if (!this.track) return;
    this.ensureAnalyser();
    try {
      await this.audio.play();
    } catch (error) {
      if (error.name !== "AbortError") toast("Нажмите на волну, чтобы включить звук");
    }
  }

  setLoading(loading) {
    this.loading = loading;
    this.emit();
  }

  ensureAnalyser() {
    if (this.analyser !== null) return;
    try {
      const context = new (window.AudioContext || window.webkitAudioContext)();
      const source = context.createMediaElementSource(this.audio);
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 512;
      this.samples = new Uint8Array(this.analyser.fftSize);
      source.connect(this.analyser);
      this.analyser.connect(context.destination);
      this.audio.addEventListener("play", () => context.resume());
    } catch {
      this.analyser = false;
    }
  }

  loudness() {
    if (!this.analyser || this.audio.paused) return 0;
    this.analyser.getByteTimeDomainData(this.samples);
    let sum = 0;
    for (let i = 0; i < this.samples.length; i++) {
      const v = (this.samples[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / this.samples.length) * 3.2);
  }

  setupMediaSession() {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.setActionHandler("play", () => this.play());
    navigator.mediaSession.setActionHandler("pause", () => this.audio.pause());
    navigator.mediaSession.setActionHandler("nexttrack", () => this.next("skip"));
    navigator.mediaSession.setActionHandler("previoustrack", () => this.previous());
    this.addEventListener("change", () => {
      navigator.mediaSession.playbackState = this.playing ? "playing" : "paused";
    });
  }
}

function contextLabel(type) {
  return { album: "Альбом", playlist: "Плейлист", artist: "Артист", search: "Поиск", library: "Коллекция" }[type] || "Сейчас";
}
