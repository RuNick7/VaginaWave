"""Популярные треки артиста по всему миру, а не внутри сервиса.

На странице артиста ждут хиты, как в Spotify или Яндекс Музыке, а прослушивания одной домашней
библиотеки — это только ваш вкус. Last.fm (artist.getTopTracks) знает, что слушают миллионы людей.
Треки библиотеки сопоставляются с его списком по названию с точностью до записи и транслита
(«Oschuschenya» = «Ощущения», «Song (feat. X)» = «Song»). На каждую песню из топа берётся одна
версия — студийная, а не live или demo; остальное идёт после хитов по вашим прослушиваниям.

Без ключа Last.fm можно взять порядок у самого Navidrome (getTopSongs): он спрашивает те же
внешние сервисы, но находит в библиотеке меньше.
"""

from __future__ import annotations

import difflib
import re
import threading
import time

import httpx

from .library import skeleton
from .lidarr import name_keys
from .models import Track

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
CACHE_SECONDS = 3 * 86400  # топ артиста меняется медленно
RETRY_SECONDS = 600  # Last.fm не ответил: не спрашивать снова на каждом открытии страницы
CACHE_SIZE = 1000  # артистов в кэше: по 500 названий у каждого
# глубже сотни: в библиотеке часто не главные хиты, а то, что ниже (у Скриптонита «Аванс» — 112-й)
TOP_SIZE = 500
# другая запись той же песни: ремикс, live, demo не наследуют место оригинала, только своё
_RECORDINGS = {
    "live": re.compile(r"\b(live|unplugged|concert|session|концерт\w*)\b", re.I),
    "demo": re.compile(r"\b(demo|rehearsal)\b", re.I),
    "remix": re.compile(r"\b(remix|rmx|mix|rework|bootleg|vip|ремикс)\b", re.I),
    "instrumental": re.compile(r"\b(instrumental|karaoke|acapella|a cappella|minus)\b", re.I),
    "acoustic": re.compile(r"\b(acoustic|piano version|orchestral|акустик\w*)\b", re.I),
    "speed": re.compile(r"\b(sped up|speed up|slowed|reverb|nightcore)\b", re.I),
}
# из одинаковых записей в хиты идёт та, что без пометок: «My Way», а не «My Way - 2008 Remastered»
_EXTRAS = re.compile(r"[\(\[\-–—]|\b(remaster\w*|version|edit|mono|stereo|explicit|deluxe)\b", re.I)


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def recording(title: str) -> frozenset[str]:
    """Какая это запись: пусто — основная, иначе {"live"}, {"remix"} и т. п."""
    return frozenset(kind for kind, pattern in _RECORDINGS.items() if pattern.search(title))


def rank_tracks(top: list[tuple[str, int]], tracks: list[Track]) -> dict[str, int]:
    """Место каждого трека библиотеки в мировом топе артиста: track_id → позиция (0 — самый популярный).

    Совпадение — то же название после skeleton() и та же запись (ремикс сопоставляется только с
    ремиксом); если такого нет, почти то же название (транслит, опечатка) при тех же цифрах, чтобы
    «Part 1» не стал «Part 2». Из нескольких копий одной записи место получает одна: без пометок
    вроде «Remastered», потом самая короткая и слушаемая."""
    places: dict[tuple[str, frozenset[str]], int] = {}
    for place, (name, _listeners) in enumerate(top):
        key = (skeleton(name), recording(name))
        if key[0] and key not in places:
            places[key] = place
    if not places:
        return {}
    claims: dict[int, list[Track]] = {}
    for t in tracks:
        title, kind = skeleton(t.title), recording(t.title)
        if not title:
            continue
        place = places.get((title, kind))
        if place is None:
            near = [k for k, r in places if r == kind and abs(len(k) - len(title)) <= 3 and _digits(k) == _digits(title)]
            best = max(near, key=lambda k: difflib.SequenceMatcher(None, title, k).ratio(), default=None)
            if best is not None and difflib.SequenceMatcher(None, title, best).ratio() >= 0.88:
                place = places[(best, kind)]
        if place is not None:
            claims.setdefault(place, []).append(t)
    ranking = {}
    for place, same in claims.items():
        main = min(same, key=lambda t: (bool(_EXTRAS.search(t.title)), len(t.title), -t.play_count))
        ranking[main.id] = place
    return ranking


class TopTracks:
    """Мировой топ треков артиста из Last.fm с кэшем в памяти."""

    def __init__(self, api_key: str, timeout: float = 5.0, transport=None, url: str = LASTFM_URL):
        self.api_key = api_key
        self.url = url
        self.http = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "VaginaWave"})
        self._cache: dict[str, tuple[float, list[tuple[str, int]]]] = {}
        self._lock = threading.Lock()

    def top(self, artist: str) -> list[tuple[str, int]]:
        """(название, слушателей) от самого популярного; пусто, если Last.fm не знает артиста или не ответил."""
        key = artist.casefold().strip()
        if not key:
            return []
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < (CACHE_SECONDS if cached[1] else RETRY_SECONDS):
                return cached[1]
        found = self._fetch(artist)
        with self._lock:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.clear()
            self._cache[key] = (now, found)
        return found

    def _fetch(self, artist: str) -> list[tuple[str, int]]:
        """Топ из Last.fm, только если Last.fm ответил про того же артиста.

        Last.fm сам перенаправляет имена: «MORGENSHTERN» заглавными уводит на немецкую группу
        Morgenstern, автоисправление превращает «Ye» в Yes. Поэтому пробуем имя как есть, «Как в
        заголовке» и строчными, сначала без автоисправления, и берём ответ, только если Last.fm назвал
        то же имя (с точностью до регистра, пунктуации и порядка слов)."""
        want = name_keys(artist)
        for spelling in dict.fromkeys([artist, artist.title(), artist.casefold()]):
            for autocorrect in (0, 1):
                params = {"method": "artist.gettoptracks", "artist": spelling, "autocorrect": autocorrect,
                          "limit": TOP_SIZE, "api_key": self.api_key, "format": "json"}
                try:
                    r = self.http.get(self.url, params=params)
                    body = r.json().get("toptracks") if r.status_code == 200 else None
                except (httpx.HTTPError, ValueError, AttributeError):
                    return []  # Last.fm недоступен: не долбить его остальными написаниями
                if not isinstance(body, dict):
                    continue
                named = (body.get("@attr") or {}).get("artist") or spelling
                if not want & name_keys(named):
                    continue
                items = body.get("track") or []
                found = [(str(t["name"]), int(t.get("listeners") or 0)) for t in items if isinstance(t, dict) and t.get("name")]
                if found:
                    return found
        return []

    def ranking(self, artist: str, tracks: list[Track]) -> dict[str, int]:
        return rank_tracks(self.top(artist), tracks)
