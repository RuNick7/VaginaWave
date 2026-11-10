"""Клиент Navidrome (Subsonic / OpenSubsonic API)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx

from .config import normalize_url
from .models import Entity, LibrarySnapshot, Media, Playlist, Track
from .store import Store

log = logging.getLogger(__name__)

DAY = 86400.0
NOT_FOUND = 70  # код ошибки Subsonic: таких данных нет
PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


class SubsonicError(RuntimeError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_song(s: dict) -> Track:
    return Track(
        id=str(s["id"]),
        title=s.get("title", ""),
        artist=s.get("artist", ""),
        album=s.get("album", ""),
        duration=float(s.get("duration") or 0),
        artist_id=str(s.get("artistId", "")),
        album_id=str(s.get("albumId", "")),
        cover_id=str(s.get("coverArt", "")),
        play_count=int(s.get("playCount") or 0),
        last_played=_iso_ts(s.get("played")),
        starred=bool(s.get("starred")),
        rating=int(s.get("userRating") or 0),
        starred_at=_iso_ts(s.get("starred")),
        track_no=int(s.get("track") or 0),
        disc=int(s.get("discNumber") or 0),
        year=int(s.get("year") or 0),
        genre=s.get("genre") or "",
    )


def parse_entity(d: dict) -> Entity:
    return Entity(
        id=str(d["id"]),
        name=d.get("name") or d.get("title") or "",
        artist=d.get("artist", ""),
        starred=bool(d.get("starred")),
        rating=int(d.get("userRating") or 0),
        play_count=int(d.get("playCount") or 0),
        artist_id=str(d.get("artistId", "")),
        cover_id=str(d.get("coverArt", "")),
        year=int(d.get("year") or 0),
        song_count=int(d.get("songCount") or 0),
        album_count=int(d.get("albumCount") or 0),
        created=_iso_ts(d.get("created")),
    )


class Navidrome:
    API_VERSION = "1.16.1"
    PAGE = 500

    def __init__(self, url: str, user: str, password: str, store: Store, timeout: float = 15.0):
        self.base = normalize_url(url)
        self.user = user
        self.password = password
        self.store = store
        self.http = httpx.Client(timeout=timeout)
        self.pool = ThreadPoolExecutor(max_workers=8)

    def _auth(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        return {"u": self.user, "t": token, "s": salt, "v": self.API_VERSION, "c": "volna", "f": "json"}

    def call(self, endpoint: str, **params) -> dict:
        query = {**self._auth(), **{k: v for k, v in params.items() if v is not None}}
        r = self.http.get(f"{self.base}/rest/{endpoint}", params=query)
        r.raise_for_status()
        body = r.json().get("subsonic-response", {})
        if body.get("status") != "ok":
            error = body.get("error", {})
            raise SubsonicError(error.get("message", f"{endpoint} failed"), error.get("code", 0))
        return body

    def ping(self) -> bool:
        self.call("ping")
        return True

    def scanning(self) -> bool:
        """Navidrome сейчас сканирует библиотеку (после докачки новые файлы появятся, когда он закончит)."""
        return bool(self.call("getScanStatus").get("scanStatus", {}).get("scanning"))

    def is_admin(self) -> bool:
        """Администратор Navidrome может менять настройки сервера и в Volna."""
        return bool(self.call("getUser", username=self.user).get("user", {}).get("adminRole"))

    def top_songs(self, artist: str, count: int = 50) -> list[str]:
        """Хиты артиста из внешних сервисов Navidrome (Last.fm и др.), найденные в библиотеке: id по порядку."""
        songs = self.call("getTopSongs", artist=artist, count=count).get("topSongs", {}).get("song", [])
        return [s["id"] for s in songs if s.get("id")]

    def server_info(self) -> dict:
        body = self.call("ping")
        return {"type": body.get("type", "subsonic"), "version": body.get("serverVersion") or body.get("version", "")}

    # --- история и вкус ---

    def sync(self) -> LibrarySnapshot:
        """Вся библиотека с прослушиваниями, звёздами и оценками: песни, альбомы, артисты, плейлисты."""
        started = time.time()
        songs = self._all_songs()
        snapshot = LibrarySnapshot(
            songs=songs,
            albums=self._all_albums(),
            artists=self._all_artists(),
            playlists=self._own_playlists(),
            synced_at=time.time(),
        )
        log.info(
            "Библиотека синхронизирована за %.1f с: %d треков, %d альбомов, %d артистов, %d плейлистов",
            time.time() - started, len(songs), len(snapshot.albums), len(snapshot.artists), len(snapshot.playlists),
        )
        return snapshot

    def _all_songs(self) -> list[Track]:
        songs: list[Track] = []
        offset = 0
        while True:
            body = self.call("search3", query="", songCount=self.PAGE, songOffset=offset, artistCount=0, albumCount=0)
            chunk = body.get("searchResult3", {}).get("song", [])
            songs += [parse_song(s) for s in chunk]
            if len(chunk) < self.PAGE:
                break
            offset += self.PAGE
        if not songs:
            # старые сервера не отдают всё по пустому запросу: берём хотя бы историю и звёзды
            log.warning("search3 с пустым запросом ничего не вернул, беру частые альбомы и звёзды")
            merged = {t.id: t for t in self.frequent_songs(albums=100) + self.starred_songs()}
            songs = list(merged.values())
        return self._remember(songs)

    def _all_albums(self) -> list[Entity]:
        albums: list[Entity] = []
        offset = 0
        while True:
            body = self.call("getAlbumList2", type="alphabeticalByName", size=self.PAGE, offset=offset)
            chunk = body.get("albumList2", {}).get("album", [])
            albums += [parse_entity(a) for a in chunk]
            if len(chunk) < self.PAGE:
                return albums
            offset += self.PAGE

    def _all_artists(self) -> list[Entity]:
        index = self.call("getArtists").get("artists", {}).get("index", [])
        return [parse_entity(a) for group in index for a in group.get("artist", [])]

    def _own_playlists(self, limit: int = 100) -> list[Playlist]:
        listed = self.call("getPlaylists").get("playlists", {}).get("playlist", [])
        own = [p for p in listed if p.get("owner") in (None, self.user)][:limit]

        def load(p: dict) -> Playlist | None:
            try:
                return self.playlist(str(p["id"]))
            except (httpx.HTTPError, SubsonicError):
                log.warning("Не удалось загрузить плейлист %s", p.get("name"), exc_info=True)
                return None

        return [p for p in self.pool.map(load, own) if p is not None]

    def playlist(self, playlist_id: str) -> Playlist | None:
        """Плейлист как он есть сейчас; None — такого больше нет."""
        try:
            body = self.call("getPlaylist", id=playlist_id).get("playlist", {})
        except SubsonicError as exc:
            if exc.code == NOT_FOUND:
                return None
            raise
        return Playlist(
            id=str(body.get("id", playlist_id)),
            name=body.get("name", ""),
            track_ids=[str(s["id"]) for s in body.get("entry", [])],
            changed=_iso_ts(body.get("changed")),
        )

    def starred_songs(self) -> list[Track]:
        songs = self.call("getStarred2").get("starred2", {}).get("song", [])
        return self._remember([parse_song(s) for s in songs])

    def frequent_songs(self, albums: int = 30) -> list[Track]:
        """Песни из самых часто и недавно слушаемых альбомов (с playCount)."""
        album_ids: list[str] = []
        for kind in ("frequent", "recent"):
            body = self.call("getAlbumList2", type=kind, size=albums)
            for album in body.get("albumList2", {}).get("album", []):
                if album["id"] not in album_ids:
                    album_ids.append(album["id"])

        def album_songs(album_id: str) -> list[Track]:
            try:
                songs = self.call("getAlbum", id=album_id).get("album", {}).get("song", [])
                return [parse_song(s) for s in songs]
            except (httpx.HTTPError, SubsonicError):
                log.warning("Не удалось загрузить альбом %s", album_id, exc_info=True)
                return []

        tracks = [t for chunk in self.pool.map(album_songs, album_ids) for t in chunk]
        return self._remember(tracks)

    def random_songs(self, n: int = 50) -> list[Track]:
        songs = self.call("getRandomSongs", size=min(n, 500)).get("randomSongs", {}).get("song", [])
        return self._remember([parse_song(s) for s in songs])

    def songs(self, ids: list[str]) -> dict[str, Track]:
        found = self.store.tracks(ids, max_age=DAY)
        missing = [i for i in ids if i not in found]

        def get(track_id: str) -> Track | None:
            try:
                return parse_song(self.call("getSong", id=track_id)["song"])
            except (httpx.HTTPError, SubsonicError, KeyError):
                log.warning("Не удалось загрузить трек %s", track_id, exc_info=True)
                return None

        fetched = [t for t in self.pool.map(get, missing) if t is not None]
        self.store.put_tracks(fetched)
        found.update({t.id: t for t in fetched})
        return found

    def _remember(self, tracks: list[Track]) -> list[Track]:
        self.store.put_tracks(tracks)
        return tracks

    # --- действия, которые синхронизируются со всеми клиентами ---

    def scrobble(self, track_id: str, submission: bool, time_ms: int | None = None) -> None:
        self.call("scrobble", id=track_id, submission=str(submission).lower(), time=time_ms)

    STAR_PARAM = {"track": "id", "album": "albumId", "artist": "artistId"}

    def star(self, item_id: str, entity: str = "track") -> None:
        self.call("star", **{self.STAR_PARAM[entity]: item_id})

    def unstar(self, item_id: str, entity: str = "track") -> None:
        self.call("unstar", **{self.STAR_PARAM[entity]: item_id})

    def star_tracks(self, track_ids: list[str], liked: bool = True) -> None:
        """Отметить сразу много треков: Subsonic принимает несколько id в одном запросе."""
        for i in range(0, len(track_ids), 100):  # чтобы адрес запроса не разрастался
            self.call("star" if liked else "unstar", id=track_ids[i : i + 100])

    def set_rating(self, track_id: str, rating: int) -> None:
        self.call("setRating", id=track_id, rating=rating)

    def create_playlist(self, name: str, track_ids: list[str]) -> Playlist:
        ids = list(track_ids)
        body = self.call("createPlaylist", name=name, songId=ids[: self.PAGE])
        created = body.get("playlist") or {}
        if not created.get("id"):
            # старые сервера не возвращают созданный плейлист: ищем свежий с этим именем
            listed = self.call("getPlaylists").get("playlists", {}).get("playlist", [])
            same = [p for p in listed if p.get("name") == name]
            created = max(same, key=lambda p: p.get("created", ""), default={"id": "", "name": name})
        for i in range(self.PAGE, len(ids), self.PAGE):  # длинный плейлист не влезает в один запрос
            self.call("updatePlaylist", playlistId=created["id"], songIdToAdd=ids[i : i + self.PAGE])
        return Playlist(id=str(created.get("id", "")), name=created.get("name", name),
                        track_ids=ids, changed=time.time())

    def lyrics(self, track_id: str, artist: str = "", title: str = "") -> dict:
        """Текст песни: по строкам с временем, если он синхронизирован, иначе просто строки.

        Navidrome берёт его из .lrc/.ttml рядом с файлом, из тегов или плагина текстов.
        """
        try:
            found = self.call("getLyricsBySongId", id=track_id).get("lyricsList", {}).get("structuredLyrics", [])
        except (SubsonicError, httpx.HTTPStatusError):
            found = []  # сервер без расширений OpenSubsonic
        if found:
            best = next((item for item in found if item.get("synced")), found[0])
            synced = bool(best.get("synced"))
            offset = best.get("offset") or 0  # как в LRC: положительный сдвиг показывает строки раньше
            lines = [
                {"time": max(0.0, (line["start"] - offset) / 1000) if synced and "start" in line else None,
                 "text": line.get("value", "")}
                for line in best.get("line", [])
            ]
            return {"synced": synced, "lines": lines}
        if artist and title:
            text = self.call("getLyrics", artist=artist, title=title).get("lyrics", {}).get("value", "")
            return {"synced": False, "lines": [{"time": None, "text": line} for line in text.splitlines()]}
        return {"synced": False, "lines": []}

    # --- медиа ---

    def stream(self, track_id: str, range_header: str | None = None, max_bitrate: int = 0) -> Media:
        """Поток трека; max_bitrate > 0 просит Navidrome перекодировать в mp3 с этим битрейтом."""
        params = {**self._auth(), "id": track_id}
        if max_bitrate:
            params.update(maxBitRate=max_bitrate, format="mp3")
        request = self.http.build_request(
            "GET",
            f"{self.base}/rest/stream",
            params=params,
            headers={"Range": range_header} if range_header else {},
        )
        resp = self.http.send(request, stream=True)
        headers = {k: v for k, v in resp.headers.items() if k.lower() in PASS_HEADERS}
        return Media(
            status=resp.status_code,
            media_type=resp.headers.get("content-type", "audio/mpeg"),
            headers=headers,
            body=resp.iter_bytes(),
            close=resp.close,
        )

    def cover(self, cover_id: str, size: int = 600) -> Media:
        r = self.http.get(f"{self.base}/rest/getCoverArt", params={**self._auth(), "id": cover_id, "size": size})
        return Media(
            status=r.status_code,
            media_type=r.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
            body=[r.content],
        )
