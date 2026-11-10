"""«Моя волна»: бесконечный поток, который подстраивается под пропуски.

Ключевые идеи:
* Якоря не двигаются. Кандидаты всегда ищутся от ядра кластеров вкуса (или плейлиста,
  альбома, артиста), а не от последней песни, поэтому поток не «уплывает».
* Пропуск — главный сигнал. Ранний пропуск ослабляет кластер на всю сессию,
  отталкивает вектор сессии и выкидывает из очереди похожие треки.
* Пролистывание (пропуск в первые секунды) — «не это сейчас», а не «не нравится»: в сессии
  направление отодвигается чуть мягче, а в долгий вкус оно почти не идёт.
* После серии пропусков волна не прыгает в случайный кластер (такой трек пропускали в
  58–81% случаев), а возвращается к тому, что в этой сессии дослушали, а если ничего —
  к дослушанному недавно, сначала в это же время суток. С этого же начинается каждая волна.
* Брошенная волна — тоже сигнал: если прошлую сессию закрыли после серии пропусков,
  новая обходит то направление.
* Какой кластер играть дальше, решает Thompson sampling по статистике
  «дослушал / пропустил» с учётом времени суток и будней/выходных.
* Настройки (характер, настроение, занятие, температура, разнообразие) превращаются
  в Tuning и меняют подбор, не трогая накопленные знания о вкусе.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from .models import Candidate, Track
from .profile import BUCKETS, DAY, ZAP_SECONDS, TasteProfile, is_zap, time_bucket
from .services import Deps
from .settings import WaveSettings, match, track_traits, tune
from .store import Event
from .vectors import unit

log = logging.getLogger(__name__)
_FETCH_POOL = ThreadPoolExecutor(max_workers=4)  # запросы частей порции к AudioMuse — параллельно

EARLY_SKIP_SECONDS = 30.0
EARLY_SKIP_FRACTION = 0.25
COMPLETE_FRACTION = 0.8
FORGOTTEN_AFTER = 60 * DAY
RECOVERY_BATCHES = 2  # сколько порций после серии пропусков собирать из того, что заходило
COMFORT_DAYS = 14  # дослушанное за это время — куда возвращаться
ABANDON_HOURS = 72  # прошлую волну бросили недавно: её последнее направление обходим
PLAY_KINDS = ("complete", "late_skip", "early_skip", "dislike")

# Веса слагаемых оценки «дослушает ли» (признаки нормируются внутри порции)
WEIGHTS = dict(
    session=0.30, anchor=0.25, taste=0.15, negative=0.25, session_negative=0.35,
    artist=0.8, album=0.3, transition=0.2, context=0.7, steer=0.8,
)

PITCH = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6,
         "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}


def classify(outcome: str, position: float, duration: float) -> str:
    """complete | late_skip | early_skip | dislike | error."""
    if outcome in ("dislike", "error", "complete"):
        return outcome
    fraction = position / duration if duration > 0 else 0.0
    if fraction >= COMPLETE_FRACTION:
        return "complete"
    if position < EARLY_SKIP_SECONDS or fraction < EARLY_SKIP_FRACTION:
        return "early_skip"
    return "late_skip"


@dataclass
class WarmStart:
    """С чего начать волну: что недавно дослушивали и от чего ушли в прошлый раз."""

    comfort: list[str] = field(default_factory=list)  # дослушанное недавно, сначала в это же время суток
    abandoned: list[str] = field(default_factory=list)  # серия пропусков, после которой волну закрыли


def warm_start(events: list[Event], now: float) -> WarmStart:
    plays = sorted((e for e in events if e.entity == "track" and e.kind in PLAY_KINDS), key=lambda e: e.ts)
    bucket = time_bucket(now)
    completes = [e for e in plays if e.kind == "complete" and now - e.ts < COMFORT_DAYS * DAY]
    completes.sort(key=lambda e: (time_bucket(e.ts) != bucket, -e.ts))
    comfort = list(dict.fromkeys(e.track_id for e in completes))[:12]
    abandoned: list[str] = []
    waves = [e for e in plays if e.session and e.context_type == "wave"]
    if waves:
        tail = [e for e in waves if e.session == waves[-1].session][-3:]
        negative = [e for e in tail if e.kind in ("early_skip", "dislike")]
        if len(negative) >= 2 and tail[-1].kind != "complete" and now - tail[-1].ts < ABANDON_HOURS * 3600:
            abandoned = [e.track_id for e in negative]
    return WarmStart(comfort=comfort, abandoned=abandoned)


def redundant(events: list[Event], track_id: str, kind: str, position: float, ts: float) -> bool:
    """Событие ничего не говорит о вкусе: повтор того же в пределах 15 секунд или пролистывание
    трека, который недавно дослушали (листают историю «назад-вперёд», а не оценивают песню)."""
    for e in events:
        if e.track_id != track_id or e.entity != "track":
            continue
        if e.kind == kind and abs(ts - e.ts) < 15:
            return True
        if is_zap(kind, position) and e.kind in ("complete", "late_skip") and 0 <= ts - e.ts < 3 * 3600:
            return True
    return False


def _fifths(key: str, scale: str) -> int | None:
    pitch = PITCH.get((key or "").strip().upper())
    if pitch is None:
        return None
    if (scale or "").lower().startswith("min"):
        pitch = (pitch + 3) % 12  # параллельный мажор
    return (pitch * 7) % 12


def transition_cost(a: dict | None, b: dict | None) -> float:
    """Насколько резким будет переход: темп, энергия, тональность по квинтовому кругу."""
    if not a or not b:
        return 0.0
    cost = 0.0
    if a.get("tempo") and b.get("tempo"):
        cost += 0.4 * min(abs(a["tempo"] - b["tempo"]) / 40.0, 1.0)
    if a.get("energy") and b.get("energy"):
        cost += 0.3 * min(abs(a["energy"] - b["energy"]) / 0.05, 1.0)
    fa, fb = _fifths(a.get("key", ""), a.get("scale", "")), _fifths(b.get("key", ""), b.get("scale", ""))
    if fa is not None and fb is not None:
        d = abs(fa - fb)
        cost += 0.3 * min(d, 12 - d) / 6.0
    return cost


def _z(values: np.ndarray) -> np.ndarray:
    std = values.std()
    return (values - values.mean()) / std if std > 1e-6 else np.zeros_like(values)


def register_playback(
    deps: Deps,
    taste: TasteProfile | None,
    track_id: str,
    kind: str,
    *,
    track: Track | None = None,
    position: float = 0.0,
    duration: float = 0.0,
    context_type: str = "",
    context_id: str = "",
    session: str = "",
    mode: str = "",
    source: str = "manual",
    cluster: int | None = None,
    device: str = "",
    ts: float | None = None,
) -> None:
    """Любое прослушивание (в волне, альбоме, плейлисте): в базу, в Navidrome и в профиль."""
    ts = ts or time.time()
    if track is None and taste is not None:
        track = taste.track_info.get(track_id)
    deps.store.add_event(
        Event(
            ts=ts, track_id=track_id, kind=kind, artist=track.artist if track else "",
            session=session, position=position, duration=duration, mode=mode, source=source,
            cluster=cluster, album_id=track.album_id if track else "", artist_id=track.artist_id if track else "",
            context_type=context_type, context_id=context_id, device=device,
        )
    )
    threshold = min(240.0, 0.5 * duration) if duration else 240.0
    if kind == "complete" or position >= threshold:
        deps.run_io(deps.library.scrobble, track_id, submission=True, time_ms=int(ts * 1000))
    if kind == "dislike":
        deps.run_io(deps.library.set_rating, track_id, 1)
    if taste is not None:
        taste.note(track, track_id, kind, position=position)


@dataclass
class Steer:
    """Куда сместить волну: к треку, альбому, артисту, плейлисту или только что закончившемуся списку.

    Это не отдельный якорь вместо вкуса. Часть каждой порции ищется рядом с источником
    (с балансом между его частями), близость к нему добавляет очки, а остальное — ваш
    вкус, подтянутый в его сторону. Сила смещения — настройка `steer`, пропуски её ослабляют.
    """

    profile: TasteProfile  # кластеры источника в пространстве вкуса
    kind: str  # track | album | artist | playlist | list
    id: str = ""
    title: str = ""

    @property
    def center(self) -> np.ndarray:
        shares = np.array([c.share for c in self.profile.clusters])
        return unit((self.profile.centers * shares[:, None]).sum(axis=0))


@dataclass
class Pick:
    cluster: int  # кластер вкуса: на нём учатся веса сессии
    score: float
    explore: bool
    reason: str
    source: str = "audiomuse"
    origin: str = "taste"  # taste — из вашего вкуса, steer — рядом с источником смещения
    group: int = 0  # кластер внутри своего источника: для квот и баланса


class WaveSession:
    BATCH = 5
    LOW_WATER = 3

    def __init__(
        self,
        deps: Deps,
        taste: TasteProfile,
        settings: WaveSettings | str | None = None,
        *,
        steer: Steer | None = None,
        exclude: tuple[str, ...] | list[str] = (),
        device: str = "",
        seed: int | None = None,
        warm: WarmStart | None = None,
    ):
        self.id = secrets.token_urlsafe(8)
        self.deps = deps
        self.profile = taste  # кластеры вкуса: откуда берём «свою» часть волны
        self.taste = taste
        self.steer = steer
        if isinstance(settings, str):
            settings = WaveSettings(character=settings)
        self.settings = (settings or WaveSettings()).merged({})
        self.tuning = tune(self.settings)
        self.kind = "steer" if steer else "wave"
        self.context_type = steer.kind if steer else "wave"
        self.context_id = steer.id if steer else ""
        self.title = steer.title if steer else ""
        self.device = device
        self.rng = np.random.default_rng(seed)
        self.lock = threading.Lock()

        self.mult = np.ones(len(taste.clusters))
        self.successes = np.stack([c.successes for c in taste.clusters]).astype(float)
        self.failures = np.stack([c.failures for c in taste.clusters]).astype(float)
        self.steer_base = self._steer_target() if steer else 0.0
        self.steer_strength = self.steer_base
        self.steer_mult = np.ones(len(steer.profile.clusters)) if steer else np.ones(0)
        self.session_vec: np.ndarray | None = steer.center if steer else None  # стартуем в сторону источника
        self.skipped: list[tuple[str, np.ndarray]] = []
        self.completed: list[str] = []
        self.shown: set[str] = set(exclude)
        self.recent_artists: deque[str] = deque(maxlen=6)
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.picks: dict[str, Pick] = {}
        self.served: Counter[tuple[str, int]] = Counter()
        self.consecutive_skips = 0
        self.zaps = 0  # пролистываний подряд
        self.recover = 0  # сколько ближайших порций собирать из того, что заходило
        self.comfort: list[str] = []  # дослушанное недавно в прошлых сессиях
        self.avoid: int | None = None
        self.explore_boost = 0  # сколько ближайших порций после «встряхнуть» идут с усиленной разведкой
        self._cluster_fit: np.ndarray | None = None
        self.touched = time.time()
        # В сервисе очередь дозаполняется в фоне после ответа (refill_if_needed), чтобы «дальше»
        # не ждал AudioMuse; в тестах и симуляции — сразу, как раньше.
        self.background_refill = False
        self.refilling = False
        self.low_water = self.LOW_WATER  # в сервисе запас больше: хватает и на быстрые пропуски
        if steer is None:
            self._warm(warm or WarmStart())

    def _warm(self, warm: WarmStart) -> None:
        """Начало волны: от недавно дослушанного, в обход направления, где прошлую волну бросили.
        Первая порция — из знакомого, а не веер по всем кластерам (его пропускали чаще всего)."""
        self.comfort = list(warm.comfort)
        vecs = self._vecs(warm.abandoned + warm.comfort[:8])
        for i in warm.abandoned:
            if i in vecs:
                self.mult[self.profile.nearest(vecs[i])] *= 0.5
                self.skipped.append((i, vecs[i]))
        good = [vecs[i] for i in warm.comfort[:8] if i in vecs]
        if good:
            self.session_vec = unit(np.mean(good, axis=0))
        self.recover = 1

    # --- публичный интерфейс ---

    def start(self) -> Track | None:
        return self._next()

    def advance(self, track_id: str, outcome: str, position: float = 0.0, duration: float = 0.0) -> Track | None:
        """Трек закончился или его пропустили: учесть реакцию и выдать следующий."""
        self.touched = time.time()
        track = self._track(track_id)
        duration = duration or (track.duration if track else 0.0)
        kind = classify(outcome, position, duration)
        if kind != "error":
            self._learn(track_id, track, kind, position, duration)
        if track and track.artist:
            self.recent_artists.append(track.artist.lower())
        return self._next()

    def like(self, track_id: str, liked: bool = True) -> None:
        self.touched = time.time()
        track = self._track(track_id)
        v = self._vec(track_id)
        cluster = self._cluster_of(track_id, v)
        self.deps.store.add_event(
            Event(
                ts=time.time(), track_id=track_id, kind="like" if liked else "unlike",
                artist=track.artist if track else "", session=self.id, mode=self.tuning.character,
                source=self._source(track_id), cluster=cluster, album_id=track.album_id if track else "",
                artist_id=track.artist_id if track else "", context_type=self.context_type,
                context_id=self.context_id, device=self.device,
            )
        )
        self.deps.run_io(self.deps.library.star if liked else self.deps.library.unstar, track_id)
        if track:
            track.starred = liked
        if not liked:
            return
        self.taste.note(track, track_id, "like", weight=1.5)
        if cluster is not None:
            self.mult[cluster] = min(1.8, self.mult[cluster] * 1.15)
            self.successes[cluster, time_bucket(time.time())] += 1.5
        if v is not None:
            self.session_vec = v if self.session_vec is None else unit(0.55 * self.session_vec + 0.45 * v)

    def _steer_target(self) -> float:
        """Сила смещения из настроек: «только похожее» держит её на максимуме."""
        return 1.0 if self.settings.similar_only else self.settings.steer

    def set_settings(self, settings: WaveSettings) -> None:
        """Применить настройки: текущий трек играет дальше, очередь пересобирается."""
        self.settings = settings
        self.tuning = tune(settings)
        if self.steer:
            self.steer_base = self.steer_strength = self._steer_target()
        self._cluster_fit = None
        self._rebuild_queue()

    def shake(self) -> None:
        """«Встряхнуть»: уйти от текущего звучания и пару порций смелее исследовать вкус."""
        if self.current and self.current.id in self.picks:
            self.avoid = self.picks[self.current.id].cluster
        if self.steer and not self.settings.similar_only:
            self.steer_strength = max(0.15, self.steer_strength * 0.6)
        self.explore_boost = 2
        self.served.clear()
        self._rebuild_queue()

    def reason(self, track_id: str) -> str:
        pick = self.picks.get(track_id)
        return pick.reason if pick else ""

    def snapshot(self) -> dict:
        """Состояние сессии для отладки: как сместились веса кластеров и сила смещения."""
        state = {
            "settings": self.settings.to_dict(),
            "kind": self.kind,
            "consecutive_skips": self.consecutive_skips,
            "completed": len(self.completed),
            "skipped": len(self.skipped),
            "clusters": [
                {
                    "label": c.label,
                    "share": round(c.share, 3),
                    "session_weight": round(float(self.mult[c.index]), 3),
                    "completes": round(float(self.successes[c.index].sum()), 2),
                    "skips": round(float(self.failures[c.index].sum()), 2),
                }
                for c in self.profile.clusters
            ],
        }
        if self.steer:
            state["steer"] = {
                "kind": self.steer.kind,
                "title": self.title,
                "strength": round(self.steer_strength, 3),
                "parts": [round(float(m), 3) for m in self.steer_mult],
            }
        return state

    # --- обучение на реакции ---

    def _learn(self, track_id: str, track: Track | None, kind: str, position: float, duration: float) -> None:
        v = self._vec(track_id)
        cluster = self._cluster_of(track_id, v)
        pick = self.picks.get(track_id)
        from_steer = pick is not None and pick.origin == "steer"
        register_playback(
            self.deps, self.taste, track_id, kind, track=track, position=position, duration=duration,
            context_type=self.context_type, context_id=self.context_id, session=self.id,
            mode=self.tuning.character, source=self._source(track_id), cluster=cluster, device=self.device,
        )
        bucket = time_bucket(time.time())

        if kind == "complete":
            self.consecutive_skips = 0
            self.zaps = 0
            if from_steer:  # смещение возвращается к заданной силе, если похожее заходит
                self.steer_strength += (self.steer_base - self.steer_strength) * 0.3
            if cluster is not None:
                self.mult[cluster] = min(1.5, self.mult[cluster] * 1.05)
                self.successes[cluster, bucket] += 1
            if v is not None:
                self.session_vec = v if self.session_vec is None else unit(0.7 * self.session_vec + 0.3 * v)
                self.completed.append(track_id)
            return

        if kind == "late_skip":
            self.zaps = 0
            if cluster is not None:
                self.mult[cluster] *= 0.85
                self.failures[cluster, bucket] += 0.4
            return

        # ранний пропуск, пролистывание или «не нравится». Пролистывание (первые секунды) — «не это
        # сейчас»: направление в сессии отодвигается чуть мягче, а во вкус почти не идёт (profile.note)
        strong = kind == "dislike"
        zap = is_zap(kind, position)
        self.zaps = self.zaps + 1 if zap else 0
        self.consecutive_skips += 1
        if from_steer:  # похожее на источник не заходит: меньше держимся за него и за эту его часть
            if not self.settings.similar_only:  # «только похожее» не разбавляем своим вкусом
                self.steer_strength = max(0.15, self.steer_strength * (0.7 if strong else 0.85))
            self.steer_mult[pick.group] *= 0.6 if strong else 0.75
        if cluster is not None:
            self.mult[cluster] *= 0.4 if strong else 0.75 if zap else 0.6
            self.failures[cluster, bucket] += 2 if strong else 0.5 if zap else 1
            if self.consecutive_skips >= 2:
                self.mult[cluster] *= 0.5
                self.avoid = cluster
        if v is not None:
            self.skipped.append((track_id, v))
            if self.session_vec is not None:
                dot = float(self.session_vec @ v)
                if dot > 0:
                    self.session_vec = unit(self.session_vec - (0.3 if zap else 0.6) * dot * v)
        self._purge(v, track.artist if track else "", cluster, threshold=0.45 if strong else 0.6 if zap else 0.55)
        if self.consecutive_skips >= 2:
            self._enter_recovery()

    def _comfort_ids(self) -> list[str]:
        """Что заходило: дослушанное в этой сессии, а если ничего — недавно, в прошлых."""
        return list(reversed(self.completed[-5:])) or self.comfort[:8]

    def _comfort_clusters(self) -> list[int]:
        """Кластеры, куда вернуться: где дослушанное, с поправкой на историю этого времени суток."""
        clusters = self.profile.clusters
        vecs = self._vecs(self._comfort_ids())
        votes = Counter(self.profile.nearest(v) for v in vecs.values())
        succ, fail = self._bucket_stats()
        posterior = (1 + succ) / (2 + succ + fail)
        # доли, а не счёт: если и в знакомом пропускают (mult падает), волна переходит к следующему
        share = np.array([votes.get(c.index, 0) for c in clusters], dtype=float) / max(1, sum(votes.values()))
        score = (share + posterior) * self.mult * self._cluster_context_fit()
        if self.avoid is not None:
            score[self.avoid] *= 0.05
        order = [int(c) for c in np.argsort(-score)]
        if len(order) > 1 and votes.get(order[1], 0) and order[1] != self.avoid:
            return order[:2]
        return order[:1]

    def _enter_recovery(self) -> None:
        """Серия пропусков: следующие порции — из того, что заходило, а не разведка нового.
        Из очереди уходит всё, что не оттуда, чтобы следующий трек уже был знакомым по звучанию."""
        self.recover = RECOVERY_BATCHES
        vecs = self._vecs(self._comfort_ids())
        if vecs:
            self.session_vec = unit(np.mean(list(vecs.values()), axis=0))
        keep = set(self._comfort_clusters())
        stay = []
        for t in self.queue:
            pick = self.picks.get(t.id)
            if pick is not None and not pick.explore and pick.origin == "taste" and pick.cluster in keep:
                stay.append(t)
            else:
                self.shown.discard(t.id)
        self.queue = stay

    def _purge(self, v: np.ndarray | None, artist: str, cluster: int | None, threshold: float) -> None:
        """Убрать из очереди всё, что похоже на только что пропущенный трек."""
        if not self.queue:
            return
        vecs = self._vecs([t.id for t in self.queue])
        keep = []
        for t in self.queue:
            u = vecs.get(t.id)
            same_artist = bool(artist) and t.artist.lower() == artist.lower()
            too_close = v is not None and u is not None and float(u @ v) > threshold
            pick = self.picks.get(t.id)
            same_cluster = self.consecutive_skips >= 2 and pick is not None and pick.cluster == cluster
            if not (same_artist or too_close or same_cluster):
                keep.append(t)
        self.queue = keep

    # --- очередь ---

    def _rebuild_queue(self) -> None:
        for t in self.queue:
            self.shown.discard(t.id)
        self.queue.clear()
        self._refill()

    def needs_refill(self) -> bool:
        return len(self.queue) < self.low_water

    def refill_if_needed(self) -> None:
        if self.needs_refill():
            self._refill()

    def _next(self) -> Track | None:
        if not self.queue or (self.needs_refill() and not self.background_refill):
            self._refill()  # очередь пуста — тут уж придётся подождать
        self.current = self.queue.pop(0) if self.queue else None
        if self.needs_refill() and not self.background_refill:
            self._refill()
        if self.current:
            self.deps.run_io(self.deps.library.scrobble, self.current.id, submission=False)
        return self.current

    def _refill(self) -> None:
        batch = self._batch(self.BATCH) or self._fallback(self.BATCH)
        self.queue.extend(batch)
        self._batch_done()

    def _batch_done(self) -> None:
        if self.explore_boost:
            self.explore_boost -= 1
        if self.recover:
            self.recover -= 1

    def _bucket_stats(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.tuning.context_aware:
            return self.successes.sum(axis=1) / BUCKETS, self.failures.sum(axis=1) / BUCKETS
        b = time_bucket(time.time())
        succ = self.successes[:, b] + 0.25 * (self.successes.sum(axis=1) - self.successes[:, b])
        fail = self.failures[:, b] + 0.25 * (self.failures.sum(axis=1) - self.failures[:, b])
        return succ, fail

    def _cluster_context_fit(self) -> np.ndarray:
        """Множитель кластера под настройки: настроение и занятие, а для «Забытого» —
        сколько в кластере любимого, что давно не звучало."""
        if self._cluster_fit is None:
            fit = np.ones(len(self.profile.clusters))
            for c in self.profile.clusters:
                if self.tuning.has_target:
                    feats = self.deps.vectors.features(c.members[:15])
                    scores = [match(track_traits(f), self.tuning.target) for f in feats.values()]
                    fit[c.index] *= np.exp(2.0 * float(np.mean(scores))) if scores else 1.0
                if self.tuning.character == "forgotten":
                    fit[c.index] *= 0.15 + len(self._forgotten(c.members)) / max(1, len(c.members))
            self._cluster_fit = fit
        return self._cluster_fit

    def _forgotten(self, ids: list[str]) -> list[str]:
        now = time.time()
        return [
            i for i in ids
            if self.taste.track_weight.get(i, 0.0) > 0.5 and now - self.taste.last_played.get(i, 0.0) > FORGOTTEN_AFTER
        ]

    def _choose_clusters(self) -> list[tuple[int, bool]]:
        """Кластеры вкуса через Thompson sampling: (кластер, это разведка?)."""
        clusters = self.profile.clusters
        if len(clusters) == 1:
            return [(0, False)]
        if self.recover:  # после серии пропусков и в начале волны — знакомое, без разведки
            chosen = self._comfort_clusters()
            self.avoid = None
            return [(c, False) for c in chosen]
        share = np.array([c.share for c in clusters])
        served = np.array([self.served[("taste", c.index)] for c in clusters], dtype=float)
        succ, fail = self._bucket_stats()
        theta = self.rng.beta(1 + succ, 1 + fail)
        affinity = np.ones(len(clusters))
        if self.session_vec is not None:
            affinity *= 1 + 0.6 * np.clip(self.profile.centers @ self.session_vec, 0, None)
        if self.steer is not None and self.steer_strength > 0:
            # ваш вкус подтягивается к источнику: ближние к нему кластеры звучат чаще
            near = np.clip((self.profile.centers @ self.steer.profile.centers.T).max(axis=1), 0, None)
            affinity *= 1 + 2.0 * self.steer_strength * near
        satiety = np.exp(-self.tuning.satiety * served)  # даже любимое приедается, если играет подряд
        fit = self._cluster_context_fit()
        p = theta * np.sqrt(share) * affinity * self.mult * satiety * fit
        if self.avoid is not None:
            p[self.avoid] *= 0.05
            self.avoid = None
        order = np.argsort(-p)
        main = int(order[0])
        explore = min(0.6, self.tuning.explore + (0.35 if self.explore_boost else 0.0))
        if self.rng.random() < explore:
            others = order[1:]
            posterior = (1 + succ[others]) / (2 + succ[others] + fail[others])
            w = self.mult[others] * posterior * fit[others] / np.sqrt(share[others])
            return [(main, False), (int(self.rng.choice(others, p=w / w.sum())), True)]
        second = int(order[1])
        if p[second] >= 0.5 * p[main]:
            return [(main, False), (second, False)]
        return [(main, False)]

    def _choose_steer(self) -> list[int]:
        """Части источника по очереди, в пропорции их размера: плейлист из двух жанров
        продолжается обоими. Пропуски уменьшают долю части."""
        clusters = self.steer.profile.clusters
        if len(clusters) == 1:
            return [0]
        share = np.array([c.share for c in clusters]) * self.steer_mult
        share /= share.sum()
        served = np.array([self.served[("steer", c.index)] for c in clusters], dtype=float)
        deficit = share - served / max(served.sum(), 1.0) + self.rng.normal(0, 0.02, len(clusters))
        order = np.argsort(-deficit)
        return [int(order[0]), int(order[1])]

    def _plan(self, k: int) -> list[tuple[str, int, int, bool]]:
        """Из чего собрать порцию: (источник, кластер, сколько треков, разведка?)."""
        steer_k = int(round(k * self.steer_strength)) if self.steer else 0
        plan: list[tuple[str, int, int, bool]] = []

        def split(origin: str, items: list[tuple[int, bool]], quota: int) -> None:
            if quota <= 0:
                return
            if len(items) == 1 or quota == 1:
                plan.append((origin, items[0][0], quota, items[0][1]))
                return
            plan.append((origin, items[0][0], quota - quota // 2, items[0][1]))
            plan.append((origin, items[1][0], quota // 2, items[1][1]))

        if steer_k:
            split("steer", [(c, False) for c in self._choose_steer()], steer_k)
        split("taste", self._choose_clusters(), k - steer_k)
        return plan

    def _seeds(self, cluster) -> list[str]:
        """Опорные треки для поиска. Каждый раз другие из ядра кластера: соседи одних
        и тех же медоидов быстро заканчиваются, а якорь при этом остаётся тем же кластером."""
        core = cluster.members[:15]
        if len(core) <= 3:
            return list(core)
        p = 1.0 / (np.arange(len(core)) + 3.0)
        return [str(s) for s in self.rng.choice(core, size=3, replace=False, p=p / p.sum())]

    def _favorites_pool(self, cluster) -> list[str]:
        """Уже любимые треки кластера; для «Забытого» — только те, что давно не звучали."""
        # лайкнутое пачкой («Лайкнуть все») — не любимое, пока его не дослушали
        pool = [m for m in cluster.members if m not in self.shown and m not in self.taste.bulk_liked]
        if self.tuning.character == "forgotten":
            pool = self._forgotten(pool)
        return pool[: self.tuning.favorites * 3]

    def _request(self, origin: str, c: int, n: int) -> dict:
        """Что спросить у AudioMuse для одной части порции. Тут случайность и состояние
        сессии, поэтому вызывается под замком сессии; сеть — отдельно, в _fetch."""
        source = self.steer.profile if origin == "steer" else self.profile
        cluster = source.clusters[c]
        seeds = self._seeds(cluster)

        def same_group(ids):
            return [i for i in ids if i in self.picks and self.picks[i].origin == origin and self.picks[i].group == c]

        add = list(seeds)
        if origin == "taste" and self.recover and self.completed:
            # после серии пропусков ищем рядом с тем, что в этой сессии дослушали, а не от центра кластера
            add = list(reversed(self.completed))[:3] + seeds[:1]
        elif origin == "taste" or len(cluster.members) < 4:
            # вкус и волна по одному треку подстраиваются под дослушанное в сессии;
            # плейлист и альбом держат свои опорные треки, чтобы не уплыть
            add += same_group(reversed(self.completed))[:2]
        favorites: list[str] = []
        if self.tuning.favorites:
            pool = self._favorites_pool(cluster)
            if pool:
                favorites = [str(m) for m in self.rng.choice(pool, size=min(len(pool), self.tuning.favorites), replace=False)]
        return {
            "seeds": seeds,
            "add": add,
            "subtract": same_group([i for i, _ in reversed(self.skipped)])[:3],
            "n": n,
            "temperature": self.tuning.temperature,
            "favorites": favorites,
        }

    def _fetch_rows(self, request: dict) -> list[Candidate]:
        """Сетевой запрос к AudioMuse по одной части порции; состояние сессии не трогает."""
        rows: list[Candidate] = []
        try:
            rows = self.deps.sim.alchemy(request["add"], request["subtract"], n=request["n"], temperature=request["temperature"])
        except Exception:  # noqa: BLE001
            log.warning("Song Alchemy недоступен, ищу похожие по отдельным трекам", exc_info=True)
        if not rows:
            seeds = request["seeds"]
            for seed in seeds:
                try:
                    found = self.deps.sim.similar(seed, n=max(5, request["n"] // len(seeds)))
                    rows += [Candidate(r.id, r.title, r.artist, source="similar") for r in found]
                except Exception:  # noqa: BLE001
                    log.warning("AudioMuse не нашёл похожих для %s", seed, exc_info=True)
        return rows

    def _fetch(self, requests: list[dict]) -> list[list[Candidate]]:
        """Все запросы порции и прогрев кэша под выбранных кандидатов. В сервисе — параллельно
        и без замка сессии (см. prepare_refill / finish_refill)."""
        if self.background_refill:
            rows = list(_FETCH_POOL.map(self._fetch_rows, requests))
        else:
            rows = [self._fetch_rows(r) for r in requests]  # в тестах — по порядку, ради воспроизводимости
        ids = list(dict.fromkeys([r.id for part in rows for r in part] + [f for r in requests for f in r["favorites"]]))
        self.deps.library.songs(ids)
        self.deps.vectors.get(ids)
        self.deps.vectors.features(ids)
        return rows

    def _merge(self, request: dict, rows: list[Candidate]) -> dict[str, Candidate]:
        out = {r.id: r for r in rows}
        for m in request["favorites"]:
            info = self.taste.track_info.get(m)
            out.setdefault(m, Candidate(m, artist=info.artist if info else "", source="favorites"))
        return out

    # Фоновая дозагрузка в три шага: решить (под замком) → сходить в сеть (без замка) → выбрать (под замком)

    def prepare_refill(self) -> tuple[list, list[dict]]:
        plan = self._plan(self.BATCH)
        return plan, [self._request(o, c, max(20, 6 * q)) for o, c, q, _ in plan]

    def fetch_refill(self, prepared: tuple[list, list[dict]]) -> list[list[Candidate]]:
        return self._fetch(prepared[1])

    def finish_refill(self, prepared: tuple[list, list[dict]], rows: list[list[Candidate]]) -> None:
        if not self.needs_refill():
            return
        batch = self._batch(self.BATCH, (prepared[0], prepared[1], rows)) or self._fallback(self.BATCH)
        self.queue.extend(batch)
        self._batch_done()

    def _batch(self, k: int, prepared: tuple | None = None) -> list[Track]:
        if prepared is None:
            plan = self._plan(k)
            requests = [self._request(o, c, max(20, 6 * q)) for o, c, q, _ in plan]
            rows = self._fetch(requests)
        else:
            plan, requests, rows = prepared
        quotas = {(o, c): q for o, c, q, _ in plan}
        explore = {(o, c) for o, c, _, e in plan if e}
        group_of: dict[str, tuple[str, int]] = {}
        origin_src: dict[str, str] = {}
        tracks: dict[str, Track] = {}
        vecs: dict[str, np.ndarray] = {}
        allowed: list[str] = []
        for attempt in (1, 3):  # если фильтры съели почти всё, просим у AudioMuse втрое больше
            if attempt == 3:
                requests = [self._request(o, c, max(20, 6 * q) * 3) for o, c, q, _ in plan]
                rows = self._fetch(requests)
            for (o, c, _, _), request, part in zip(plan, requests, rows):
                for cid, cand in self._merge(request, part).items():
                    group_of.setdefault(cid, (o, c))
                    origin_src.setdefault(cid, cand.source)
            fresh = [i for i in group_of if i not in tracks and i not in self.shown and i not in self.taste.disliked]
            tracks.update(self.deps.library.songs(fresh))
            vecs.update(self._vecs(fresh))
            allowed += self._allowed(fresh, tracks, vecs)
            if len(allowed) >= 2 * k:
                break
        if not allowed:
            return []

        x = np.stack([vecs[i] for i in allowed])
        groups = [group_of[i] for i in allowed]
        scores = self._score(allowed, x, groups, tracks, k)
        picked = self._select(allowed, x, scores, groups, tracks, quotas, k)
        score_of = dict(zip(allowed, scores))
        batch = []
        for i in self._order(picked):
            o, c = group_of[i]
            self.shown.add(i)
            self.served[(o, c)] += 1
            self.picks[i] = Pick(
                cluster=c if o == "taste" else self.profile.nearest(vecs[i]),
                score=float(score_of[i]),
                explore=(o, c) in explore,
                reason=self._reason(o, c, (o, c) in explore, origin_src.get(i, "")),
                source=origin_src.get(i, "audiomuse"),
                origin=o,
                group=c,
            )
            batch.append(tracks[i])
        return batch

    def _allowed(self, ids: list[str], tracks: dict[str, Track], vecs: dict[str, np.ndarray]) -> list[str]:
        now = time.time()
        allowed = []
        for i in ids:
            t = tracks.get(i)
            if t is None or i not in vecs or i in self.taste.hidden:
                continue  # нет звучания или это лишняя копия песни, которая есть в альбоме
            if t.rating == 1:
                self.taste.disliked.add(i)
                continue
            if self.taste.is_banned(t):
                continue  # артист или альбом с оценкой 1★
            if self.taste.track_skips.get(i, 0.0) >= 1.5:
                continue  # пропускал дважды за последнее время: пусть отдохнёт
            last = max(t.last_played or 0.0, self.taste.last_played.get(i, 0.0))
            if last and now - last < self.tuning.repeat_days * DAY:
                continue
            allowed.append(i)
        return allowed

    def _score(self, ids: list[str], x: np.ndarray, groups: list[tuple[str, int]], tracks: dict[str, Track], k: int) -> np.ndarray:
        """Оценка «дослушает ли»: близость к якорю, вкусу, сессии и источнику смещения
        минус похожесть на пропущенное."""
        w = WEIGHTS
        n = len(ids)
        anchors = np.stack([(self.steer.profile if o == "steer" else self.profile).centers[c] for o, c in groups])
        f_anchor = np.einsum("nd,nd->n", x, anchors)
        f_taste = (x @ self.taste.centers.T).max(axis=1)
        neg = self.taste.negatives
        f_neg = (x @ neg.T).max(axis=1) if len(neg) else np.zeros(n)

        now = time.time()
        novelty = np.zeros(n)
        forgotten = np.zeros(n)
        for j, i in enumerate(ids):
            t = tracks[i]
            heard = i in self.taste.known or t.play_count > 0 or t.starred
            last = max(t.last_played or 0.0, self.taste.last_played.get(i, 0.0))
            recent = bool(last) and now - last < FORGOTTEN_AFTER
            novelty[j] = 1.0 if not heard else 0.0 if recent else 0.5
            forgotten[j] = 1.0 if heard and not recent and self.taste.track_weight.get(i, 0.0) > 0.5 else 0.0
        artist = np.array([self.taste.artist_affinity(tracks[i].artist) for i in ids])
        album = np.array([self.taste.album_affinity(tracks[i].album_id) for i in ids])

        prev = self._last_id()
        feats = self.deps.vectors.features(ids + ([prev] if prev else []))
        transition = np.zeros(n)
        if prev:
            transition = np.array([transition_cost(feats.get(prev), feats.get(i)) for i in ids])

        anchor_weight = w["anchor"] + (0.0 if self.session_vec is not None else w["session"])
        score = (
            anchor_weight * _z(f_anchor)
            + w["taste"] * _z(f_taste)
            - w["negative"] * _z(f_neg)
            + self.tuning.novelty * novelty
            + self.tuning.forgotten * forgotten
            + w["artist"] * artist
            + w["album"] * album
            - w["transition"] * transition
        )
        if self.session_vec is not None:
            score += w["session"] * _z(x @ self.session_vec)
        if self.steer is not None and self.steer_strength > 0:
            f_steer = (x @ self.steer.profile.centers.T).max(axis=1)
            score += w["steer"] * self.steer_strength * _z(f_steer)
        if self.skipped:
            recent_skips = np.stack([v for _, v in self.skipped[-20:]])
            f_skip = (x @ recent_skips.T).max(axis=1)
            score -= w["session_negative"] * _z(f_skip)
            score[f_skip > 0.8] -= 5.0  # почти копия того, что только что пропустили
        if self.tuning.has_target:
            fit = np.array([match(track_traits(feats.get(i)), self.tuning.target) for i in ids])
            score += w["context"] * (_z(fit) + fit)
            if (fit >= -0.2).sum() >= k:
                score[fit < -0.2] -= 3.0  # явно не под настроение
        return score

    def _select(self, ids, x, scores, groups, tracks, quotas, k) -> list[str]:
        """MMR: лучшие по оценке, но не похожие друг на друга и без повтора артистов."""
        window = list(self.recent_artists)[-self.tuning.artist_window:]
        blocked = set(window) | {t.artist.lower() for t in self.queue[-3:]}
        if self.current:
            blocked.add(self.current.artist.lower())
        chosen: list[int] = []
        per_group: Counter[tuple[str, int]] = Counter()
        artists: set[str] = set()
        for strict in (True, False):
            while len(chosen) < k:
                best, best_value = None, -np.inf
                for n, i in enumerate(ids):
                    if n in chosen:
                        continue
                    artist = tracks[i].artist.lower()
                    if artist in artists:
                        continue
                    if strict and (per_group[groups[n]] >= quotas.get(groups[n], 0) or artist in blocked):
                        continue
                    redundancy = max((float(x[n] @ x[m]) for m in chosen), default=0.0)
                    value = scores[n] - self.tuning.redundancy * max(redundancy, 0.0)
                    if value > best_value:
                        best, best_value = n, value
                if best is None:
                    break
                chosen.append(best)
                per_group[groups[best]] += 1
                artists.add(tracks[ids[best]].artist.lower())
        return [ids[n] for n in chosen]

    def _order(self, ids: list[str]) -> list[str]:
        """Жадно выстраиваем порядок, чтобы переходы по темпу и тональности были мягкими."""
        if len(ids) < 2:
            return ids
        prev = self._last_id()
        feats = self.deps.vectors.features(ids + ([prev] if prev else []))
        if not feats:
            return ids
        rest = list(ids)
        ordered: list[str] = []
        current = feats.get(prev) if prev else None
        if current is None:
            ordered.append(rest.pop(0))
            current = feats.get(ordered[0])
        while rest:
            nxt = min(rest, key=lambda i: transition_cost(current, feats.get(i)))
            rest.remove(nxt)
            ordered.append(nxt)
            current = feats.get(nxt)
        return ordered

    def _fallback(self, k: int) -> list[Track]:
        """Если похожих не нашлось совсем: случайное из библиотеки, лишь бы музыка не встала."""
        try:
            songs = self.deps.library.random_songs(40)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось получить случайные треки", exc_info=True)
            return []
        vecs = self._vecs([t.id for t in songs])
        out = []
        for t in songs:
            if t.id in self.shown or t.id not in vecs or self.taste.is_banned(t):
                continue
            self.shown.add(t.id)
            cluster = self.profile.nearest(vecs[t.id])
            self.picks[t.id] = Pick(cluster, 0.0, True, "Случайное из библиотеки", "random", group=cluster)
            out.append(t)
            if len(out) >= k:
                break
        return out

    # --- вспомогательное ---

    def _reason(self, origin: str, c: int, explore: bool, source: str) -> str:
        if origin == "steer":
            return f"Похоже на «{self.title}»" if self.title else "Похоже на то, что играло"
        label = self.profile.clusters[c].label
        if source == "favorites":
            return "Давно не слушали" if self.tuning.character == "forgotten" else "Из любимого"
        if explore:
            return f"Разведка: {label}" if label else "Разведка"
        return f"В духе: {label}" if label else "Моя волна"

    def _track(self, track_id: str) -> Track | None:
        if self.current and self.current.id == track_id:
            return self.current
        return self.taste.track_info.get(track_id)

    def _source(self, track_id: str) -> str:
        pick = self.picks.get(track_id)
        if pick is None:
            return "manual"
        return "steer" if pick.origin == "steer" else pick.source

    def _last_id(self) -> str | None:
        if self.queue:
            return self.queue[-1].id
        return self.current.id if self.current else None

    def _cluster_of(self, track_id: str, v: np.ndarray | None) -> int | None:
        if track_id in self.picks:
            return self.picks[track_id].cluster
        return self.profile.nearest(v) if v is not None else None

    def _vec(self, track_id: str) -> np.ndarray | None:
        return self._vecs([track_id]).get(track_id)

    def _vecs(self, ids: list[str]) -> dict[str, np.ndarray]:
        return {i: self.profile.space.project(v) for i, v in self.deps.vectors.get(ids).items()}


def build_mix(
    deps: Deps,
    taste: TasteProfile,
    steer: Steer,
    settings: WaveSettings,
    *,
    size: int = 25,
    exclude: list[str] | tuple[str, ...] = (),
    strength: float = 0.85,
    seed: int | None = None,
) -> list[Track]:
    """Плейлист по треку (альбому, артисту): та же смещённая волна, собранная заранее.

    Смещение сильнее, чем в волне, и разрешены недавно игравшие треки: это подборка,
    которую сохраняют, а не поток на сейчас. Реакций нет, поэтому ничего не учится.
    """
    session = WaveSession(
        deps, taste, settings.merged({"repeat_days": 0, "steer": strength}), steer=steer, exclude=exclude, seed=seed
    )
    tracks: list[Track] = []
    while len(tracks) < size:
        batch = session._batch(min(WaveSession.BATCH, size - len(tracks)))
        if not batch:
            break
        session.queue.extend(batch)  # чтобы следующая порция знала, чем закончилась предыдущая
        tracks.extend(batch)
    return tracks
