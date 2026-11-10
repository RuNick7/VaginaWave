"""Профиль вкуса: что слушатель любит (кластеры), что не любит (негативы), как реагирует.

Сигналы берутся из двух мест:
* Navidrome — вся история и отметки, общие для всех клиентов: сколько раз и когда
  играл каждый трек, звёзды и оценки треков, альбомов и артистов, свои плейлисты;
* события Volna — дослушал, пропустил (и на какой секунде), лайк, «не нравится», и в
  каком контексте это было (волна, альбом, плейлист). Пропуски Subsonic не хранит,
  поэтому они живут только в нашей базе.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .library import duplicate_copies
from .models import LibrarySnapshot, Track
from .services import Deps
from .store import Event
from .vectors import Space, kmeans

DAY = 86400.0
BUCKETS = 8  # утро / день / вечер / ночь × будни / выходные

# Вклад отметок Navidrome в вес трека
SONG_STARRED = 3.0
SONG_RATING = {5: 3.0, 4: 1.5, 3: 0.3, 2: -1.0, 1: -5.0}
ALBUM_STARRED = 1.0
ALBUM_RATING = {5: 1.5, 4: 1.0, 2: -0.7, 1: -5.0}
ARTIST_STARRED = 0.6  # на каждый из самых слушаемых треков артиста
ARTIST_RATING = {5: 1.0, 4: 0.6, 2: -0.5, 1: -5.0}
ARTIST_TOP_TRACKS = 15
PLAYLIST_TRACK = 0.8  # трек в собственном плейлисте; затухает с давностью изменения плейлиста

# Вклад событий Volna (дальше умножается на затухание)
EVENT_WEIGHTS = {"complete": 1.0, "like": 3.0, "late_skip": -0.4, "early_skip": -1.5, "dislike": -5.0}
POSITIVE = ("complete", "like")
NEGATIVE = ("early_skip", "dislike")

# Пропуск в первые секунды — человек листает волну в поисках настроения, песню он ещё не слышал.
# Это не «не нравится»: трек слегка отодвигается, артист и альбом не наказываются.
ZAP_SECONDS = 5.0
ZAP_WEIGHT = -0.3
ZAP_SKIPS = 0.3  # вклад в «пропускал недавно» (обычный ранний пропуск — 1)

# «Лайкнуть все» на большом плейлисте — не 500 отдельных признаний. Лайк из такой пачки весит
# как одно дослушивание, пока трек не дослушают или не лайкнут отдельно.
BULK_LIKES = 15  # столько лайков за минуту — это пачка
BULK_LIKE_WEIGHT = 0.5


def is_zap(kind: str, position: float | None) -> bool:
    return kind == "early_skip" and (position or 0.0) < ZAP_SECONDS


def bulk_likes(events: list[Event]) -> set[str]:
    """Треки, лайкнутые пачкой и с тех пор не подтверждённые: ни отдельного лайка, ни дослушивания."""
    likes = sorted((e for e in events if e.kind == "like" and e.entity == "track"), key=lambda e: e.ts)
    times = [e.ts for e in likes]
    bulk: dict[str, float] = {}
    lo = hi = 0
    for e in likes:
        while times[lo] < e.ts - 60:
            lo += 1
        while hi < len(times) and times[hi] <= e.ts + 60:
            hi += 1
        if hi - lo >= BULK_LIKES:
            bulk[e.track_id] = e.ts
        else:
            bulk.pop(e.track_id, None)  # лайкнут отдельно — это настоящий лайк
    for e in events:
        if e.kind == "complete" and e.track_id in bulk and e.ts > bulk[e.track_id]:
            del bulk[e.track_id]
        elif e.kind == "unlike" and e.track_id in bulk and e.ts > bulk[e.track_id]:
            del bulk[e.track_id]  # лайка больше нет: вес и так уйдёт вместе со звездой
    return set(bulk)


def decay(age_seconds: float, half_life_days: float) -> float:
    return 0.5 ** (max(age_seconds, 0.0) / (half_life_days * DAY))


def time_bucket(ts: float) -> int:
    t = time.localtime(ts)
    hour = t.tm_hour
    part = 0 if 5 <= hour < 11 else 1 if 11 <= hour < 17 else 2 if 17 <= hour < 23 else 3
    return part + (4 if t.tm_wday >= 5 else 0)


@dataclass
class Cluster:
    index: int
    center: np.ndarray
    weight: float
    share: float
    members: list[str]  # отсортированы по близости к центру
    medoids: list[str]
    artists: list[str]
    successes: np.ndarray = field(default_factory=lambda: np.zeros(BUCKETS))
    failures: np.ndarray = field(default_factory=lambda: np.zeros(BUCKETS))

    @property
    def label(self) -> str:
        return ", ".join(self.artists[:3])

    @property
    def completion_rate(self) -> float | None:
        total = self.successes.sum() + self.failures.sum()
        return float(self.successes.sum() / total) if total else None


@dataclass
class Stats:
    """Дослушал / пропустил по артисту или альбому (с затуханием)."""

    completes: float = 0.0
    skips: float = 0.0

    def affinity(self) -> float:
        """От -0.5 (всегда пропускает) до +0.5 (всегда дослушивает)."""
        return (self.completes + 1) / (self.completes + self.skips + 2) - 0.5


@dataclass
class TasteProfile:
    space: Space
    clusters: list[Cluster]
    negatives: np.ndarray  # (m, d), центрированные векторы нелюбимого
    disliked: set[str]
    known: set[str]  # всё, что уже звучало или отмечено
    last_played: dict[str, float]
    track_skips: dict[str, float]  # затухающее число ранних пропусков трека
    track_weight: dict[str, float]
    artist_stats: dict[str, Stats]  # ключ — имя артиста в нижнем регистре
    album_stats: dict[str, Stats]  # ключ — id альбома
    banned_artists: set[str]  # id и имена (в нижнем регистре) артистов с 1★
    banned_albums: set[str]
    track_info: dict[str, Track]
    signals: dict[str, int] = field(default_factory=dict)
    cold_start: bool = False
    built_at: float = 0.0
    hidden: set[str] = field(default_factory=set)  # лишние копии песен: в волну не ставим
    bulk_liked: set[str] = field(default_factory=set)  # лайкнуты пачкой и не подтверждены: не «любимое»

    def __post_init__(self) -> None:
        self.centers = np.stack([c.center for c in self.clusters])

    def nearest(self, v: np.ndarray) -> int:
        return int(np.argmax(self.centers @ v))

    def artist_affinity(self, artist: str) -> float:
        stats = self.artist_stats.get(artist.lower())
        return stats.affinity() if stats else 0.0

    def album_affinity(self, album_id: str) -> float:
        stats = self.album_stats.get(album_id)
        return stats.affinity() if stats else 0.0

    def is_banned(self, t: Track) -> bool:
        return (
            t.id in self.disliked
            or t.album_id in self.banned_albums
            or t.artist_id in self.banned_artists
            or t.artist.lower() in self.banned_artists
        )

    def note(self, track: Track | None, track_id: str, kind: str, weight: float = 1.0, position: float | None = None) -> None:
        """Мгновенно учитывает событие, не дожидаясь пересборки профиля."""
        artist = track.artist.lower() if track else ""
        album = track.album_id if track else ""
        now = time.time()
        if position is not None and is_zap(kind, position):  # пролистал, не послушав
            self.track_skips[track_id] = self.track_skips.get(track_id, 0.0) + ZAP_SKIPS
            self.track_weight[track_id] = self.track_weight.get(track_id, 0.0) + ZAP_WEIGHT
            self.known.add(track_id)
            return
        if kind == "complete":
            self.bulk_liked.discard(track_id)
        if kind in POSITIVE:
            for key, table in ((artist, self.artist_stats), (album, self.album_stats)):
                if key:
                    table.setdefault(key, Stats()).completes += weight
            self.last_played[track_id] = now
        elif kind in NEGATIVE:
            for key, table in ((artist, self.artist_stats), (album, self.album_stats)):
                if key:
                    table.setdefault(key, Stats()).skips += weight
            self.track_skips[track_id] = self.track_skips.get(track_id, 0.0) + (2.0 if kind == "dislike" else 1.0)
        elif kind == "late_skip":
            self.last_played[track_id] = now
        if kind == "dislike":
            self.disliked.add(track_id)
        self.track_weight[track_id] = self.track_weight.get(track_id, 0.0) + EVENT_WEIGHTS.get(kind, 0.0)
        self.known.add(track_id)


def _cluster_count(n: int) -> int:
    return int(min(10, max(1, round(math.sqrt(n / 3)))))


def build_clusters(ids: list[str], x: np.ndarray, weights: np.ndarray, info: dict[str, Track], k: int) -> list[Cluster]:
    centers, labels = kmeans(x, weights, k)
    total = float(weights.sum()) or 1.0
    clusters: list[Cluster] = []
    for j in range(len(centers)):
        idx = np.flatnonzero(labels == j)
        if not len(idx):
            continue
        closeness = x[idx] @ centers[j] + 0.03 * np.log1p(weights[idx])
        members = [ids[i] for i in idx[np.argsort(-closeness)]]
        by_artist: dict[str, float] = defaultdict(float)
        for i in idx:
            artist = info[ids[i]].artist if ids[i] in info else ""
            if artist:
                by_artist[artist] += float(weights[i])
        clusters.append(
            Cluster(
                index=len(clusters),
                center=centers[j],
                weight=float(weights[idx].sum()),
                share=float(weights[idx].sum()) / total,
                members=members,
                medoids=members[:3],
                artists=sorted(by_artist, key=by_artist.get, reverse=True),
            )
        )
    return clusters


def collect_signals(snapshot: LibrarySnapshot, events: list[Event], now: float) -> dict:
    """Сводит историю и отметки в веса треков и статистику артистов и альбомов."""
    weight: dict[str, float] = defaultdict(float)
    known: set[str] = set()
    last_played: dict[str, float] = {}
    disliked: set[str] = set()
    artist_stats: dict[str, Stats] = defaultdict(Stats)
    album_stats: dict[str, Stats] = defaultdict(Stats)
    banned_artists: set[str] = set()
    banned_albums: set[str] = set()
    track_skips: dict[str, float] = defaultdict(float)

    by_album: dict[str, list[Track]] = defaultdict(list)
    by_artist: dict[str, list[Track]] = defaultdict(list)
    artist_plays: dict[str, int] = defaultdict(int)
    starred: set[str] = set()
    bulk = bulk_likes(events)
    for t in snapshot.songs:
        by_album[t.album_id].append(t)
        by_artist[t.artist_id].append(t)
        if t.starred:
            starred.add(t.id)
            weight[t.id] += SONG_STARRED
            known.add(t.id)
        if t.rating:
            weight[t.id] += SONG_RATING.get(t.rating, 0.0)
            if t.rating == 1:
                disliked.add(t.id)
        if t.play_count > 0:
            age = now - t.last_played if t.last_played else 90 * DAY
            weight[t.id] += math.log1p(t.play_count) * decay(age, 120) * (1.0 if t.last_played else 0.5)
            known.add(t.id)
            artist_plays[t.artist.lower()] += t.play_count
            if t.last_played:
                last_played[t.id] = t.last_played

    for album in snapshot.albums:
        bonus = (ALBUM_STARRED if album.starred else 0.0) + ALBUM_RATING.get(album.rating, 0.0)
        if album.rating == 1:
            banned_albums.add(album.id)
        if album.starred or album.rating >= 4:
            album_stats[album.id].completes += 2.0
        if bonus:
            for t in by_album[album.id]:
                weight[t.id] += bonus

    for artist in snapshot.artists:
        if artist.rating == 1:
            banned_artists.update({artist.id, artist.name.lower()})
        bonus = (ARTIST_STARRED if artist.starred else 0.0) + ARTIST_RATING.get(artist.rating, 0.0)
        if artist.starred or artist.rating >= 4:
            artist_stats[artist.name.lower()].completes += 2.0
        if bonus:
            top = sorted(by_artist[artist.id], key=lambda t: -t.play_count)[:ARTIST_TOP_TRACKS]
            for t in top:
                weight[t.id] += bonus

    # прослушивания из других клиентов: это тоже «дослушал», только без пропусков
    for name, plays in artist_plays.items():
        artist_stats[name].completes += 0.3 * math.log1p(plays)

    for playlist in snapshot.playlists:
        freshness = decay(now - playlist.changed, 365) if playlist.changed else 0.5
        for tid in playlist.track_ids[:200]:
            weight[tid] += PLAYLIST_TRACK * freshness

    # звезда из пачки «Лайкнуть все» весит как лайк из пачки, а не как выбранная вручную
    for tid in bulk & starred:
        weight[tid] -= SONG_STARRED - BULK_LIKE_WEIGHT

    for e in events:
        d = decay(now - e.ts, 60)
        if e.entity != "track":
            continue  # лайки альбомов и артистов уже пришли из Navidrome как звёзды
        known.add(e.track_id)
        if is_zap(e.kind, e.position):  # пролистал в первые секунды: не оценка песни
            weight[e.track_id] += ZAP_WEIGHT * d
            track_skips[e.track_id] += decay(now - e.ts, 30) * ZAP_SKIPS
            continue
        if e.kind == "like" and e.track_id in bulk:
            weight[e.track_id] += BULK_LIKE_WEIGHT * d
            continue
        weight[e.track_id] += EVENT_WEIGHTS.get(e.kind, 0.0) * d
        if e.kind in ("complete", "late_skip"):
            last_played[e.track_id] = max(last_played.get(e.track_id, 0.0), e.ts)
        for key, table in ((e.artist.lower(), artist_stats), (e.album_id, album_stats)):
            if not key:
                continue
            if e.kind in POSITIVE:
                table[key].completes += d
            elif e.kind in NEGATIVE:
                table[key].skips += d
            elif e.kind == "late_skip":
                table[key].skips += 0.3 * d
        if e.kind in NEGATIVE:
            track_skips[e.track_id] += decay(now - e.ts, 30) * (2.0 if e.kind == "dislike" else 1.0)
        if e.kind == "dislike":
            disliked.add(e.track_id)

    return dict(
        weight=weight, known=known, last_played=last_played, disliked=disliked,
        artist_stats=dict(artist_stats), album_stats=dict(album_stats),
        banned_artists=banned_artists, banned_albums=banned_albums, track_skips=dict(track_skips), bulk_liked=bulk,
    )


def build_profile(
    deps: Deps, snapshot: LibrarySnapshot | None = None, now: float | None = None, max_positive: int = 800
) -> TasteProfile:
    now = now or time.time()
    snapshot = snapshot or deps.library.sync()
    info = {t.id: t for t in snapshot.songs}
    events: list[Event] = deps.store.events(since=now - 180 * DAY)
    s = collect_signals(snapshot, events, now)
    weight, disliked = s["weight"], s["disliked"]

    def banned(track_id: str) -> bool:
        t = info.get(track_id)
        return track_id in disliked or (t is not None and (
            t.album_id in s["banned_albums"] or t.artist_id in s["banned_artists"]
        ))

    positive = sorted((i for i, w in weight.items() if w > 0.2 and not banned(i)), key=weight.get, reverse=True)
    positive = positive[:max_positive]
    negative = [i for i, w in weight.items() if w < -0.5 or i in disliked][:300]
    rng = np.random.default_rng(len(snapshot.songs))  # выборка для среднего: стабильна между сборками
    sample = [str(i) for i in rng.choice([t.id for t in snapshot.songs], size=min(300, len(snapshot.songs)), replace=False)]
    event_ids = [e.track_id for e in events if e.entity == "track"]
    raw = deps.vectors.get(positive + negative + event_ids + sample)
    if not raw:
        raise RuntimeError("Нет ни одного эмбеддинга: проверьте, что AudioMuse проанализировал библиотеку")
    space = Space.fit(list(raw.values()))

    positive = [i for i in positive if i in raw]
    cold = len(positive) < 3
    if cold:
        positive = [i for i in sample if i in raw]
        for i in positive:
            weight[i] = 0.5
    x = space.project(np.stack([raw[i] for i in positive]))
    w = np.array([weight[i] for i in positive])
    clusters = build_clusters(positive, x, w, info, _cluster_count(len(positive)))
    negatives = [raw[i] for i in negative if i in raw]

    profile = TasteProfile(
        space=space,
        clusters=clusters,
        negatives=space.project(np.stack(negatives)) if negatives else np.zeros((0, len(space.mean)), np.float32),
        disliked=disliked,
        known=s["known"],
        last_played=s["last_played"],
        track_skips=s["track_skips"],
        track_weight=dict(weight),
        artist_stats=s["artist_stats"],
        album_stats=s["album_stats"],
        banned_artists=s["banned_artists"],
        banned_albums=s["banned_albums"],
        track_info=info,
        hidden=set(duplicate_copies(snapshot.songs)),
        bulk_liked=s["bulk_liked"],
        signals={
            "tracks": len(snapshot.songs),
            "played_tracks": sum(1 for t in snapshot.songs if t.play_count > 0),
            "starred_tracks": sum(1 for t in snapshot.songs if t.starred),
            "starred_albums": sum(1 for a in snapshot.albums if a.starred),
            "starred_artists": sum(1 for a in snapshot.artists if a.starred),
            "playlists": len(snapshot.playlists),
            "events": len(events),
        },
        cold_start=cold,
        built_at=now,
    )

    # «Дослушал / пропустил» по кластерам и контексту: её использует выбор кластера
    for e in events:
        if e.entity != "track" or e.track_id not in raw or e.kind not in EVENT_WEIGHTS:
            continue
        c = profile.clusters[profile.nearest(space.project(raw[e.track_id]))]
        d = decay(now - e.ts, 45)
        b = time_bucket(e.ts)
        if is_zap(e.kind, e.position):
            c.failures[b] += 0.2 * d  # листал: кластер «не сейчас», но не «не нравится»
        elif e.kind == "like" and e.track_id in profile.bulk_liked:
            continue
        elif e.kind in POSITIVE:
            c.successes[b] += d
        elif e.kind == "late_skip":
            c.failures[b] += 0.4 * d
        else:
            c.failures[b] += d
    return profile


def build_context_profile(deps: Deps, track_ids: list[str], taste: TasteProfile) -> TasteProfile:
    """Профиль одного плейлиста, альбома или артиста: несколько якорей вместо «последней песни»."""
    raw = deps.vectors.get(track_ids)
    ids = [i for i in track_ids if i in raw]
    if not ids:
        raise ValueError("Для этих треков нет эмбеддингов в AudioMuse")
    info = dict(taste.track_info)
    info.update(deps.library.songs([i for i in ids if i not in info]))
    x = taste.space.project(np.stack([raw[i] for i in ids]))
    k = min(4, max(1, math.ceil(len(ids) / 8)))
    clusters = build_clusters(ids, x, np.ones(len(ids)), info, k)
    return TasteProfile(
        space=taste.space,
        clusters=clusters,
        negatives=taste.negatives,
        disliked=taste.disliked,
        known=taste.known,
        last_played=taste.last_played,
        track_skips=taste.track_skips,
        track_weight=taste.track_weight,
        artist_stats=taste.artist_stats,
        album_stats=taste.album_stats,
        banned_artists=taste.banned_artists,
        banned_albums=taste.banned_albums,
        track_info=info,
        hidden=taste.hidden,
        bulk_liked=taste.bulk_liked,
        signals=taste.signals,
        built_at=time.time(),
    )
