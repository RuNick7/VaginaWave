"""Демо-мир: синтетическая библиотека вместо Navidrome и AudioMuse.

Нужен, чтобы прототип запускался без сервера, а алгоритм можно было проверять
симуляцией. Эмбеддинги нарочно сдвинуты на общий положительный вектор, как у
MusiCNN, поэтому без центрирования косинус почти не различает стили.
"""

from __future__ import annotations

import io
import math
import re
import threading
import time
import wave
import zlib
from collections import OrderedDict
from dataclasses import replace

import numpy as np

from .models import Candidate, Entity, LibrarySnapshot, Media, Playlist, Track
from .vectors import unit

DAY = 86400.0

GENRES = ["Пост-рок", "Synthwave", "Джаз", "Lo-fi", "Техно", "Инди-фолк", "Металл", "Неоклассика"]
# корневая нота (MIDI), темп, энергия: для синтеза звука и признаков AudioMuse
GENRE_SOUND = [
    (52, 92, 0.07),
    (57, 108, 0.09),
    (55, 120, 0.06),
    (50, 78, 0.04),
    (45, 128, 0.12),
    (59, 96, 0.05),
    (40, 150, 0.14),
    (48, 70, 0.03),
]
# Настроения жанров в шкалах AudioMuse (other_features) плюс тег instrumental из MusiCNN
GENRE_TRAITS = [
    dict(relaxed=0.45, sad=0.55, happy=0.3, aggressive=0.35, danceable=0.2, party=0.1, instrumental=0.85),
    dict(relaxed=0.3, sad=0.25, happy=0.6, aggressive=0.3, danceable=0.75, party=0.65, instrumental=0.6),
    dict(relaxed=0.75, sad=0.3, happy=0.55, aggressive=0.1, danceable=0.35, party=0.2, instrumental=0.7),
    dict(relaxed=0.9, sad=0.45, happy=0.35, aggressive=0.05, danceable=0.3, party=0.1, instrumental=0.9),
    dict(relaxed=0.1, sad=0.15, happy=0.4, aggressive=0.5, danceable=0.9, party=0.85, instrumental=0.9),
    dict(relaxed=0.6, sad=0.5, happy=0.5, aggressive=0.1, danceable=0.3, party=0.15, instrumental=0.1),
    dict(relaxed=0.05, sad=0.3, happy=0.2, aggressive=0.95, danceable=0.3, party=0.3, instrumental=0.15),
    dict(relaxed=0.85, sad=0.6, happy=0.3, aggressive=0.05, danceable=0.05, party=0.02, instrumental=1.0),
]
KEYS = ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]
TITLE_A = ["Северный", "Тихий", "Стеклянный", "Ночной", "Белый", "Далёкий", "Медленный", "Лунный", "Пустой", "Тёплый", "Глубокий", "Последний"]
TITLE_B = ["берег", "свет", "город", "шум", "ветер", "сигнал", "прилив", "горизонт", "маяк", "лес", "дождь", "контур"]
ARTIST_A = ["Glass", "Echo", "North", "Velvet", "Static", "Paper", "Hollow", "Neon", "Quiet", "Silver", "Low", "Amber"]
ARTIST_B = ["Harbor", "Lines", "Fields", "Signal", "Rivers", "Motel", "Season", "Atlas", "Tides", "Engine", "Orchard", "Coast"]

LIKED_GENRES = (0, 1, 3)
FORMER_GENRE = 2  # когда-то слушал много, давно не включал
DISLIKED_GENRE = 6


class DemoWorld:
    def __init__(self, seed: int = 7, dim: int = 200, artists_per_genre: int = 6, tracks_per_artist: int = 12):
        rng = np.random.default_rng(seed)
        self._rng = np.random.default_rng(seed + 100)  # выдача alchemy и случайные треки: воспроизводимо
        self._lock = threading.Lock()
        self._audio_cache: OrderedDict[str, bytes] = OrderedDict()
        self.tracks: dict[str, Track] = {}
        self.raw: dict[str, np.ndarray] = {}
        self.genre: dict[str, int] = {}  # -1 у «мостов» между двумя жанрами
        self.sound: dict[str, tuple[int, float]] = {}
        self.features_map: dict[str, dict] = {}
        self.albums: dict[str, Entity] = {}
        self.artists: dict[str, Entity] = {}
        self.playlists: list[Playlist] = []

        offset = np.abs(rng.normal(0, 1, dim)) * 2.0
        centers = rng.normal(0, 1, (len(GENRES), dim))
        names = [f"{a} {b}" for a in ARTIST_A for b in ARTIST_B]
        rng.shuffle(names)

        specs = [(g, None) for g in range(len(GENRES)) for _ in range(artists_per_genre)]
        specs += [(g, (g + 1) % len(GENRES)) for g in range(len(GENRES))]
        meta_rng = np.random.default_rng(seed + 1)  # год и дата добавления: отдельный поток, чтобы не менять звук и векторы
        now = time.time()
        for idx, (g, bridge) in enumerate(specs):
            base = centers[g] if bridge is None else 0.5 * (centers[g] + centers[bridge])
            artist_center = base + 0.6 * rng.normal(0, 1, dim)
            album = f"{rng.choice(TITLE_A)} {rng.choice(TITLE_B)}"
            root, tempo, energy = GENRE_SOUND[g]
            traits = GENRE_TRAITS[g] if bridge is None else {
                k: (v + GENRE_TRAITS[bridge][k]) / 2 for k, v in GENRE_TRAITS[g].items()
            }
            album_id, artist_id = f"demo-album-{idx}", f"demo-artist-{idx}"
            count = tracks_per_artist if bridge is None else 8
            year = int(meta_rng.integers(1995, 2027))
            self.albums[album_id] = Entity(
                album_id, album, artist=names[idx], artist_id=artist_id, cover_id=album_id, year=year,
                song_count=count, created=now - float(meta_rng.uniform(1, 400)) * DAY,
            )
            self.artists[artist_id] = Entity(artist_id, names[idx], cover_id=artist_id, album_count=1)
            for t in range(count):
                tid = f"demo-{idx:02d}-{t:02d}"
                self.tracks[tid] = Track(
                    id=tid,
                    title=f"{rng.choice(TITLE_A)} {rng.choice(TITLE_B)}",
                    artist=names[idx],
                    album=album,
                    duration=float(rng.integers(40, 81)),
                    artist_id=artist_id,
                    album_id=album_id,
                    cover_id=album_id,
                    track_no=t + 1,
                    year=year,
                    genre=GENRES[g] if bridge is None else f"{GENRES[g]} / {GENRES[bridge]}",
                )
                self.raw[tid] = (offset + artist_center + 0.45 * rng.normal(0, 1, dim)).astype(np.float32)
                self.genre[tid] = g if bridge is None else -1
                self.sound[tid] = (root, tempo)
                self.features_map[tid] = {
                    "tempo": float(tempo + rng.normal(0, 6)),
                    "energy": float(energy * (1 + 0.2 * rng.normal())),
                    "key": KEYS[int(rng.integers(12))],
                    "scale": str(rng.choice(["major", "minor"])),
                    "moods": {"instrumental": _jitter(rng, traits["instrumental"])},
                    "other": {k: _jitter(rng, v) for k, v in traits.items() if k != "instrumental"},
                }

        self.ids = list(self.tracks)
        self._index = {tid: n for n, tid in enumerate(self.ids)}
        raw = np.stack([self.raw[i] for i in self.ids])
        self._x = unit(raw - raw.mean(axis=0))
        self._seed_history(rng)

    def _seed_history(self, rng: np.random.Generator) -> None:
        now = time.time()

        def pick(genre: int, n: int) -> list[str]:
            pool = [i for i in self.ids if self.genre[i] == genre]
            return [str(i) for i in rng.choice(pool, size=n, replace=False)]

        for g in LIKED_GENRES:
            for tid in pick(g, 8):
                self.tracks[tid].starred = True
                self.tracks[tid].starred_at = now - (1 + zlib.crc32(tid.encode()) % 300) * DAY
            for tid in pick(g, 14):
                self.tracks[tid].play_count = int(rng.integers(2, 25))
                self.tracks[tid].last_played = now - float(rng.uniform(1, 40)) * DAY
        for tid in pick(FORMER_GENRE, 15):
            self.tracks[tid].play_count = int(rng.integers(3, 11))
            self.tracks[tid].last_played = now - float(rng.uniform(100, 200)) * DAY
        for tid in pick(DISLIKED_GENRE, 3):
            self.tracks[tid].rating = 1

        # Отметки целых альбомов и артистов, плейлисты пользователя
        def artist_of_genre(genre: int, n: int = 0) -> str:
            return sorted({self.tracks[i].artist_id for i in self.ids if self.genre[i] == genre})[n]

        self.albums[artist_of_genre(0).replace("artist", "album")].starred = True
        self.albums[artist_of_genre(3, 1).replace("artist", "album")].rating = 5
        self.artists[artist_of_genre(1, 2)].starred = True
        self.artists[artist_of_genre(DISLIKED_GENRE, 0)].rating = 1
        self.playlists = [
            Playlist("demo-pl-evening", "Вечер", pick(3, 10) + pick(7, 8), changed=now - 20 * DAY),
            Playlist("demo-pl-road", "Дорога", pick(1, 10) + pick(4, 6), changed=now - 60 * DAY),
        ]

    def genre_name(self, track_id: str) -> str:
        g = self.genre.get(track_id, -1)
        return GENRES[g] if g >= 0 else "кроссовер"

    # --- Library ---

    def ping(self) -> bool:
        return True

    def server_info(self) -> dict:
        return {"type": "demo", "version": "синтетическая библиотека"}

    def sync(self) -> LibrarySnapshot:
        return LibrarySnapshot(
            songs=[replace(t) for t in self.tracks.values()],
            albums=[replace(a) for a in self.albums.values()],
            artists=[replace(a) for a in self.artists.values()],
            playlists=[replace(p, track_ids=list(p.track_ids)) for p in self.playlists],
            synced_at=time.time(),
        )

    def random_songs(self, n: int = 50) -> list[Track]:
        ids = self._rng.choice(self.ids, size=min(n, len(self.ids)), replace=False)
        return [replace(self.tracks[i]) for i in ids]

    def songs(self, ids: list[str]) -> dict[str, Track]:
        return {i: replace(self.tracks[i]) for i in ids if i in self.tracks}

    def scrobble(self, track_id: str, submission: bool, time_ms: int | None = None) -> None:
        if submission and track_id in self.tracks:
            self.tracks[track_id].play_count += 1
            self.tracks[track_id].last_played = time_ms / 1000 if time_ms else time.time()

    def _item(self, item_id: str):
        return self.tracks.get(item_id) or self.albums.get(item_id) or self.artists.get(item_id)

    def star(self, item_id: str, entity: str = "track") -> None:
        self._item(item_id).starred = True

    def unstar(self, item_id: str, entity: str = "track") -> None:
        self._item(item_id).starred = False

    def star_tracks(self, track_ids: list[str], liked: bool = True) -> None:
        for track_id in track_ids:
            (self.star if liked else self.unstar)(track_id)

    def set_rating(self, item_id: str, rating: int) -> None:
        self._item(item_id).rating = rating

    def playlist(self, playlist_id: str) -> Playlist | None:
        found = next((p for p in self.playlists if p.id == playlist_id), None)
        return replace(found, track_ids=list(found.track_ids)) if found else None

    def create_playlist(self, name: str, track_ids: list[str]) -> Playlist:
        playlist = Playlist(f"demo-pl-{len(self.playlists) + 1}", name, list(track_ids), changed=time.time())
        self.playlists.append(playlist)
        return replace(playlist, track_ids=list(track_ids))

    def stream(self, track_id: str, range_header: str | None = None, max_bitrate: int = 0) -> Media:
        if track_id not in self.tracks:
            return Media(404, "text/plain", {}, [b"not found"])
        data = self._audio(track_id)
        size = len(data)
        match = re.match(r"bytes=(\d*)-(\d*)", range_header or "")
        if match and (match.group(1) or match.group(2)):
            start = int(match.group(1)) if match.group(1) else size - int(match.group(2))
            end = int(match.group(2)) if match.group(1) and match.group(2) else size - 1
            start, end = max(0, start), min(end, size - 1)
            headers = {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"}
            return Media(206, "audio/wav", headers, [data[start : end + 1]])
        return Media(200, "audio/wav", {"Accept-Ranges": "bytes"}, [data])

    def scanning(self) -> bool:
        return False

    def lyrics(self, track_id: str, artist: str = "", title: str = "") -> dict:
        """Заглушка с синхронизированными строками, чтобы было что показать; у трети треков текста нет."""
        track = self.tracks.get(track_id)
        if track is None or zlib.crc32(track_id.encode()) % 3 == 0:
            return {"synced": False, "lines": []}
        count = max(1, int((track.duration - 12) // 6))
        return {"synced": True, "lines": [{"time": 8.0 + 6 * i, "text": f"{track.title} · строка {i + 1}"} for i in range(count)]}

    def cover(self, cover_id: str, size: int = 600) -> Media:
        rng = np.random.default_rng(zlib.crc32(cover_id.encode()))
        cx, cy = rng.uniform(110, 190, 2)
        paths = []
        for ring in range(10):
            radius = 14 + ring * 15
            lobes = int(rng.integers(2, 5))
            phase = rng.uniform(0, 2 * math.pi)
            amp = 2 + ring * 0.9
            points = []
            for a in np.linspace(0, 2 * math.pi, 97):
                r = radius + amp * math.sin(lobes * a + phase)
                points.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
            paths.append(f'<path d="M{" L".join(points)}Z"/>')
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300">'
            '<rect width="300" height="300" fill="#000"/>'
            f'<g fill="none" stroke="#fff" stroke-width="1.1" opacity="0.8">{"".join(paths)}</g></svg>'
        )
        return Media(200, "image/svg+xml", {"Cache-Control": "public, max-age=86400"}, [svg.encode()])

    def _audio(self, track_id: str) -> bytes:
        with self._lock:
            if track_id in self._audio_cache:
                self._audio_cache.move_to_end(track_id)
                return self._audio_cache[track_id]
        data = synth_track(track_id, self.tracks[track_id].duration, *self.sound[track_id])
        with self._lock:
            self._audio_cache[track_id] = data
            while len(self._audio_cache) > 8:
                self._audio_cache.popitem(last=False)
        return data

    # --- Similarity ---

    def _candidate(self, j: int) -> Candidate:
        t = self.tracks[self.ids[j]]
        return Candidate(id=t.id, title=t.title, artist=t.artist)

    def embedding(self, track_id: str) -> np.ndarray | None:
        return self.raw.get(track_id)

    def features(self, track_id: str) -> dict | None:
        return self.features_map.get(track_id)

    def similar(self, track_id: str, n: int = 20) -> list[Candidate]:
        i = self._index.get(track_id)
        if i is None:
            return []
        order = np.argsort(-(self._x @ self._x[i]))
        return [self._candidate(j) for j in order if j != i][:n]

    def alchemy(self, add: list[str], subtract: list[str] = (), n: int = 30, temperature: float = 0.6) -> list[Candidate]:
        """Упрощённая копия Song Alchemy: равная квота на каждую точку, вычитание, температура."""
        add_idx = [self._index[i] for i in add if i in self._index]
        if not add_idx:
            return []
        quota = max(1, (3 * n) // len(add_idx))
        pool: dict[int, float] = {}
        for a in add_idx:
            sims = self._x @ self._x[a]
            for j in np.argsort(-sims)[: quota + len(add_idx)]:
                if j not in add_idx:
                    pool[int(j)] = max(pool.get(int(j), -1.0), float(sims[j]))
        sub_idx = [self._index[i] for i in subtract if i in self._index]
        if sub_idx:
            sub_center = unit(self._x[sub_idx].mean(axis=0))
            pool = {j: s for j, s in pool.items() if float(self._x[j] @ sub_center) < 0.5}
        if not pool:
            return []
        js = list(pool)
        scores = np.array([pool[j] for j in js])
        k = min(n, len(js))
        if temperature <= 0:
            chosen = [js[i] for i in np.argsort(-scores)[:k]]
        else:
            p = np.exp((scores - scores.max()) / temperature)
            chosen = list(self._rng.choice(js, size=k, replace=False, p=p / p.sum()))
        return [self._candidate(j) for j in chosen]


def _jitter(rng: np.random.Generator, value: float) -> float:
    return float(np.clip(value + rng.normal(0, 0.08), 0.0, 1.0))


def synth_track(track_id: str, duration: float, root: int, tempo: float, sr: int = 22050) -> bytes:
    """Мягкий аккордовый пэд с пульсом по темпу: у каждого жанра своя тоника и скорость."""
    rng = np.random.default_rng(zlib.crc32(track_id.encode()))
    progressions = [[0, 5, 7, 3], [0, 7, 9, 5], [0, 3, 5, 10], [0, 8, 5, 7]]
    progression = np.array(progressions[int(rng.integers(len(progressions)))])
    t = np.arange(int(duration * sr)) / sr
    chord = progression[(t // 4).astype(int) % len(progression)]
    freq = 440.0 * 2 ** ((root + chord - 69) / 12)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    minor = rng.random() < 0.5
    voices = [(1.0, 0.5), (2 ** ((3 if minor else 4) / 12), 0.3), (2 ** (7 / 12), 0.3), (2.0, 0.12)]
    signal = sum(np.sin(phase * ratio) * amp for ratio, amp in voices)
    pulse = 0.6 + 0.4 * np.sin(np.pi * t * tempo / 60) ** 2
    envelope = np.minimum(1.0, t / 1.5) * np.minimum(1.0, (duration - t) / 2.0)
    pcm = (np.clip(signal * pulse * envelope * 0.22, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
