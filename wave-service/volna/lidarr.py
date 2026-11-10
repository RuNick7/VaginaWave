"""Lidarr — список исполнителей, чьи дискографии докачивает Soularr из того же стека.

Отсюда любой пользователь может поставить исполнителя на докачку: со страницы артиста
(«Скачать дискографию») или найти в MusicBrainz того, кого в библиотеке ещё нет. Слежение
включается за всеми альбомами и за новыми релизами; какие альбомы считать (студийные,
без сборников), решает профиль метаданных Lidarr.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from collections import Counter

import httpx

from .library import _plain, skeleton

CACHE_SECONDS = 60
# метаданные, которыми пользуется сам Lidarr: псевдонимы и полная дискография исполнителя
METADATA_URL = "https://api.lidarr.audio/api/v0.4/"
_FILLER = {"the", "a", "an", "and", "&", "и", "+"}
_COLLAB = re.compile(
    r"\s+(?:featuring|feat\.?|ft\.?|vs\.?|versus|meets|presents|with)\s+|\s*/\s*|\s+[–—=]\s+"
    r"|\s+(?:and|&)\s+(?:his|her|their)\s+",
    re.IGNORECASE,
)


def name_keys(name: str) -> set[str]:
    """Ключи сравнения имён: целиком, без артикля и «and», с любым порядком слов («Bowie, David»)."""
    words = [w for w in re.split(r"[\s,]+", name.casefold()) if _plain(w)]
    core = [w for w in words if w not in _FILLER] or words
    keys = {_plain(name), "".join(_plain(w) for w in core), "".join(sorted(_plain(w) for w in core))}
    return {k for k in keys if k}


def name_variants(name: str) -> list[str]:
    """Как ещё может называться исполнитель из тегов: без пояснения в скобках, главный из совместных."""
    out = [name]
    bare = " ".join(re.sub(r"[\(\[].*?[\)\]]", " ", name).split())
    if bare and bare != name:
        out.append(bare)
    for v in list(out):
        main = _COLLAB.split(v)[0].strip()
        if main and main != v and len(_plain(main)) >= 2:
            out.append(main)
    return list(dict.fromkeys(out))


def _similar(a: str, b: str) -> bool:
    """Почти то же имя после транслита: «Skryptonite» и «Скриптонит»."""
    return any(difflib.SequenceMatcher(None, x, y).ratio() >= 0.85
               for x in name_keys(a) for y in name_keys(b) if min(len(x), len(y)) >= 5)


class LidarrError(RuntimeError):
    pass


class Lidarr:
    def __init__(self, url: str, api_key: str, timeout: float = 60.0, transport=None, metadata_url: str = METADATA_URL):
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/api/v1", headers={"X-Api-Key": api_key}, timeout=timeout, transport=transport
        )
        self.meta_http = httpx.Client(
            base_url=metadata_url, headers={"User-Agent": "VaginaWave", "Accept": "application/json"},
            timeout=30.0, transport=transport,
        )
        self._meta: dict[str, dict] = {}
        self._artists: list[dict] = []
        self._fetched = 0.0
        self._lock = threading.Lock()

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def artists(self, fresh: bool = False) -> list[dict]:
        with self._lock:
            if fresh or time.time() - self._fetched > CACHE_SECONDS:
                self._artists = self._get("/artist")
                self._fetched = time.time()
            return self._artists

    def find(self, name: str) -> dict | None:
        """Исполнитель из библиотеки в Lidarr: имя с точностью до записи («Земфира» = «Zemfira»)."""
        target = skeleton(name)
        same = [a for a in self.artists() if skeleton(a["artistName"]) == target]
        return same[0] if len(same) == 1 else None

    def progress(self, artist_id: int) -> dict:
        albums = self._get("/album", artistId=artist_id)
        wanted = [a for a in albums if a.get("monitored")]
        have = sum(1 for a in wanted if (a.get("statistics") or {}).get("trackFileCount", 0) > 0)
        return {"albums": len(wanted), "have": have}

    def status(self, artist: dict | None) -> dict:
        if artist is None:
            return {"in_lidarr": False, "following": False}
        following = bool(artist.get("monitored")) and artist.get("monitorNewItems") == "all"
        return {"in_lidarr": True, "following": following, "name": artist["artistName"], **self.progress(artist["id"])}

    def search(self, term: str) -> list[dict]:
        """Исполнители MusicBrainz по имени — чтобы добавить и тех, кого в библиотеке ещё нет."""
        known = {a["foreignArtistId"]: a for a in self.artists()}
        found = []
        for a in self._get("/artist/lookup", term=term)[:20]:
            poster = next((i.get("remoteUrl") for i in a.get("images", []) if i.get("coverType") == "poster"), "")
            existing = known.get(a.get("foreignArtistId", ""))
            found.append({
                "mbid": a.get("foreignArtistId", ""),
                "name": a.get("artistName", ""),
                "disambiguation": a.get("disambiguation", ""),
                "type": a.get("artistType", ""),
                "overview": (a.get("overview") or "")[:300],
                "image": poster or next((i.get("remoteUrl") for i in a.get("images", [])), ""),
                "following": bool(existing and existing.get("monitored") and existing.get("monitorNewItems") == "all"),
            })
        return found

    def metadata(self, mbid: str) -> dict:
        """Карточка исполнителя из метаданных Lidarr: псевдонимы (artistaliases) и релизы (Albums)."""
        if mbid not in self._meta:
            try:
                r = self.meta_http.get(f"artist/{mbid}")
                self._meta[mbid] = r.json() if r.status_code == 200 else {}
            except (httpx.HTTPError, ValueError):
                self._meta[mbid] = {}
        return self._meta[mbid]

    def _no_releases(self, mbid: str) -> bool:
        """Метаданные точно знают исполнителя, и релизов у него нет (тёзка-сессионщик). Нет метаданных — не знаем."""
        m = self.metadata(mbid)
        return "Albums" in m and not m["Albums"]

    def _alias_match(self, name: str, mbid: str) -> bool:
        m = self.metadata(mbid)
        names = [m.get("artistname") or "", m.get("sortname") or ""]
        names += [a if isinstance(a, str) else (a.get("Name") or a.get("name") or "") for a in m.get("artistaliases") or []]
        return bool(name_keys(name) & set().union(*(name_keys(n) for n in names if n)))

    def _album_hits(self, albums: list[str]) -> tuple[Counter, dict[str, str]]:
        """Чьи это альбомы из библиотеки: Lidarr ищет альбом по названию и называет исполнителя релиза."""
        hits: Counter = Counter()
        names: dict[str, str] = {}
        for album in albums[:4]:
            try:  # без «(Deluxe)» и подобного: поиск Lidarr точнее по основному названию
                found = self._get("/album/lookup", term=" ".join(re.sub(r"[\(\[].*?[\)\]]", " ", album).split()) or album)
            except httpx.HTTPError:
                continue
            seen = set()
            for r in found:
                artist = (r.get("artist") or {}) if isinstance(r, dict) else {}
                if artist.get("foreignArtistId") and skeleton(r.get("title", "")) == skeleton(album):
                    seen.add(artist["foreignArtistId"])
                    names[artist["foreignArtistId"]] = artist.get("artistName", "")
            hits.update(seen)
        return hits, names

    def _elsewhere(self, name: str, exact: list[str], evidence: Counter, names: dict[str, str]) -> list[tuple[int, str]]:
        """Авторы альбомов из библиотеки, которые могут быть этим исполнителем под другим именем: почти то
        же имя, имя с прозвищем внутри («David Newman» и «David “Fathead” Newman») или псевдоним."""
        words = {_plain(w) for w in re.split(r"[\s,]+", name.casefold()) if _plain(w) and w not in _FILLER}

        def by_name(other: str) -> bool:
            theirs = {_plain(w) for w in re.split(r"[\s,]+", other.casefold()) if _plain(w)}
            return _similar(name, other) or (len(words) >= 2 and words <= theirs)

        out = [(n, i) for i, n in evidence.items() if i not in exact and by_name(names.get(i, ""))]
        out += [(n, i) for i, n in evidence.most_common(5)
                if i not in exact and (n, i) not in out and self._alias_match(name, i)]
        return sorted(out, reverse=True)

    def resolve(self, name: str, albums: list[str] | tuple = ()) -> str:
        """MusicBrainz ID исполнителя из библиотеки.

        Один исполнитель с таким именем (с точностью до транслита, порядка слов и артикля) и хоть с
        одним релизом — он. Среди тёзок: тот, кто уже есть в Lidarr; тот, чьи альбомы из библиотеки
        знает Lidarr; иначе тот, у кого в разы больше релизов и кто первый в поиске, если альбомы не
        указывают на кого-то с похожим именем. Имя записано иначе (Splean = Сплин) — нужен псевдоним,
        почти то же имя или имя с прозвищем у автора альбомов. Без уверенности не гадает.
        """
        known_ids = {a["foreignArtistId"] for a in self.artists()}
        ambiguous, evidence, authors = False, None, {}
        for variant in name_variants(name):
            found = [c for c in self._get("/artist/lookup", term=variant) if c.get("foreignArtistId")]
            want = name_keys(variant)
            exact = list(dict.fromkeys(c["foreignArtistId"] for c in found
                                       if want & (name_keys(c.get("artistName", "")) | name_keys(c.get("sortName") or ""))))
            known = [i for i in exact if i in known_ids]
            if len(known) == 1:
                return known[0]
            if len(exact) == 1 and not self._no_releases(exact[0]):
                return exact[0]
            if evidence is None:
                evidence, authors = self._album_hits(list(albums)) if albums else (Counter(), {})
            elsewhere = self._elsewhere(variant, exact, evidence, authors)
            if len(exact) > 1:
                ambiguous = True
                ranked = sorted(((evidence[i], i) for i in exact), reverse=True)
                if ranked[0][0] > ranked[1][0]:
                    return ranked[0][1]
                sizes = sorted(((len(self.metadata(i).get("Albums") or []), i) for i in exact[:6]), reverse=True)
                if (not elsewhere and sizes[0][1] == exact[0] and sizes[0][0] >= 5
                        and sizes[0][0] >= 4 * max(sizes[1][0], 1)):
                    return sizes[0][1]
            if not exact:
                aliased = list(dict.fromkeys(c["foreignArtistId"] for c in found[:5]
                                             if self._alias_match(variant, c["foreignArtistId"])))
                if len(aliased) == 1 and not self._no_releases(aliased[0]):
                    return aliased[0]
            if elsewhere and (len(elsewhere) == 1 or elsewhere[0][0] > elsewhere[1][0]):
                return elsewhere[0][1]
        if ambiguous:
            raise LidarrError("В MusicBrainz несколько исполнителей с таким именем: найдите нужного в «Добавить исполнителя»")
        raise LidarrError("Не нашёл исполнителя в MusicBrainz под таким именем: попробуйте «Добавить исполнителя»")

    def follow(self, mbid: str) -> dict:
        """Поставить исполнителя на докачку: добавить в Lidarr или включить слежение за всеми альбомами."""
        existing = next((a for a in self.artists(fresh=True) if a["foreignArtistId"] == mbid), None)
        if existing is None:
            found = self._get("/artist/lookup", term=f"lidarr:{mbid}")
            if not found:
                raise LidarrError("Lidarr не нашёл этого исполнителя в MusicBrainz")
            root = self._get("/rootfolder")[0]
            body = {
                **found[0],
                "qualityProfileId": root.get("defaultQualityProfileId") or 1,
                "metadataProfileId": root.get("defaultMetadataProfileId") or 1,
                "rootFolderPath": root["path"],
                "monitored": True,
                "monitorNewItems": "all",
                "addOptions": {"monitor": "all", "searchForMissingAlbums": False},  # ищет и качает Soularr
            }
            r = self.http.post("/artist", json=body)
            if r.status_code >= 400:
                raise LidarrError(f"Lidarr не добавил исполнителя: {r.text.strip()[:200]}")
            artist = r.json()
        else:
            artist = {**existing, "monitored": True, "monitorNewItems": "all"}
            self.http.put(f"/artist/{artist['id']}", json=artist).raise_for_status()
            albums = [a["id"] for a in self._get("/album", artistId=artist["id"]) if not a.get("monitored")]
            if albums:
                self.http.put("/album/monitor", json={"albumIds": albums, "monitored": True}).raise_for_status()
        self.artists(fresh=True)
        return artist
