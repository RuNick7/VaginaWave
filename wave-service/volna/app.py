"""HTTP API волны, библиотеки и веб-плеер прототипа.

Сервер сам держит очередь волны: клиент сообщает, чем закончился трек (дослушал,
пропустил на такой-то секунде, «не нравится»), и в ответ получает следующий.
Прослушивания вне волны (альбом, плейлист, поиск) клиент шлёт в /api/events — они
тоже учатся в профиль. Сервер Navidrome подключается из интерфейса, без перезапуска.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import __version__
from .auth import COOKIE, TOKEN_TTL, Auth, LoginError, client_ip, request_token
from .config import ServerConfig, Settings, normalize_url
from .devices import Devices
from .engine import Steer, WaveSession, build_mix, classify, register_playback
from .explo import Explo, source_of
from .imports import Importer
from .lidarr import Lidarr, LidarrError
from .onboarding import ONBOARDED_KEY, ArtistMap, artist_choices
from .library import LibraryIndex, track_json
from .models import LibrarySnapshot
from .profile import TasteProfile, build_context_profile, build_profile
from .services import Deps, NoSimilarity, build_deps, user_deps
from .settings import ACTIVITIES, CHARACTERS, MOODS, WaveSettings
from .store import Event, Store

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
SETTINGS_KEY = "wave_settings"
SERVER_KEY = "server"
PREFS_KEY = "preferences"
USERS_KEY = "users"  # кто входил: логин, пароль Navidrome и администратор ли он
QUALITY = {"original": 0, "320": 320, "192": 192, "128": 128}
DEFAULT_PREFS = {"quality": "original", "continue_after_end": True}
OFFLINE_DAYS = 30  # прослушивания без сети старше этого считаем сегодняшними


class Seed(BaseModel):
    type: Literal["track", "album", "artist", "playlist"]
    id: str


class StartRequest(BaseModel):
    settings: dict | None = None  # поверх сохранённых настроек
    seed: Seed | None = None  # «Моя волна по…» треку, альбому, артисту или плейлисту
    device: str = ""


class AdvanceRequest(BaseModel):
    track_id: str
    outcome: Literal["complete", "skip", "dislike", "error"]
    position: float = Field(0.0, ge=0)
    duration: float = Field(0.0, ge=0)


class LikeRequest(BaseModel):
    track_id: str
    liked: bool = True


class EntityLikeRequest(BaseModel):
    type: Literal["track", "album", "artist"]
    id: str
    liked: bool = True


class PlaybackEvent(BaseModel):
    """Прослушивание вне волны: альбом, плейлист, поиск, библиотека, очередь."""

    track_id: str
    outcome: Literal["complete", "skip", "dislike"]
    position: float = Field(0.0, ge=0)
    duration: float = Field(0.0, ge=0)
    context_type: Literal["album", "playlist", "artist", "search", "library", "queue", "other"] = "other"
    context_id: str = ""
    device: str = ""
    at: float | None = None  # когда слушали: события, накопленные без сети, приходят позже


class DeviceState(BaseModel):
    """Что играет на устройстве — другие устройства того же пользователя предложат забрать музыку."""

    device_id: str = Field(min_length=8, max_length=64)
    name: str = Field("", max_length=80)
    kind: Literal["android", "web"] = "web"
    track_id: str = Field("", max_length=200)
    position: float = Field(0.0, ge=0)
    duration: float = Field(0.0, ge=0)
    playing: bool = False
    mode: Literal["wave", "list", ""] = ""
    source: str = Field("", max_length=200)  # «Моя волна», название альбома
    next_ids: list[str] = Field(default_factory=list, max_length=50)


class ContinueRequest(BaseModel):
    track_ids: list[str] = Field(min_length=1)
    title: str = ""
    settings: dict | None = None
    device: str = ""


class MixRequest(BaseModel):
    seed: Seed
    size: int = Field(25, ge=5, le=100)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    track_ids: list[str] = Field(min_length=1, max_length=500)


class FollowRequest(BaseModel):
    mbid: str = Field(min_length=10, max_length=64)


class TracksLikeRequest(BaseModel):
    track_ids: list[str] = Field(max_length=5000)
    liked: bool = True


class OnboardingForm(BaseModel):
    artists: list[str] = Field(default_factory=list, max_length=200)
    settings: dict = Field(default_factory=dict)


class ImportRequest(BaseModel):
    url: str


class LoginForm(BaseModel):
    user: str
    password: str
    device: str = ""


class ServerForm(BaseModel):
    navidrome_url: str = Field(min_length=1)
    navidrome_user: str = Field(min_length=1)
    navidrome_password: str = ""  # пусто — оставить сохранённый пароль
    audiomuse_url: str = ""
    audiomuse_token: str = ""  # пусто — оставить сохранённый токен
    audiomuse_user: str = ""  # вместо токена: логин и пароль веб-интерфейса AudioMuse
    audiomuse_password: str = ""  # пусто — оставить сохранённый пароль


class ProfileManager:
    """Библиотека и профиль вкуса. Всё долгое — в фоне, пока работает предыдущая версия:

    * библиотека синхронизируется раз в 6 часов или по кнопке;
    * профиль вкуса собирается сразу после подключения и пересобирается после 25 событий;
    * векторы и признаки всей библиотеки один раз прогреваются в кэш, чтобы порции волны
      собирались без сотен запросов к AudioMuse.
    """

    REBUILD_EVENTS = 25
    MAX_AGE = 6 * 3600
    WARM_CHUNK = 200

    def __init__(self, deps: Deps):
        self.deps = deps
        self._profile: TasteProfile | None = None
        self._profile_snapshot: LibrarySnapshot | None = None
        self._snapshot: LibrarySnapshot | None = None
        self._index: LibraryIndex | None = None
        self._events = 0
        self._lock = threading.RLock()  # поля
        self._sync_lock = threading.Lock()  # одна синхронизация за раз
        self._build_lock = threading.RLock()  # одна сборка профиля за раз
        self._jobs: dict[str, threading.Thread] = {}
        self.syncing = False
        self.sync_error: str | None = None
        self.warm = {"done": 0, "total": 0, "running": False}

    @property
    def wave_available(self) -> bool:
        return not isinstance(self.deps.sim, NoSimilarity)

    # --- фоновые задачи ---

    def _start(self, name: str, fn, before=None) -> bool:
        with self._lock:
            job = self._jobs.get(name)
            if job is not None and job.is_alive():
                return False
            if before:
                before()

            def run() -> None:
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("Фоновая задача «%s» не удалась", name)

            self._jobs[name] = threading.Thread(target=run, name=f"volna-{name}", daemon=True)
            self._jobs[name].start()
            return True

    def start(self) -> None:
        """Сразу после подключения: библиотека, профиль и прогрев кэша — заранее, в фоне."""
        self._start("library", self._prepare)

    def _prepare(self) -> None:
        self.index()
        if self.wave_available:
            self.get()
            self._start("warm", self._warm_vectors)

    def refresh_in_background(self) -> None:
        self._start("library", self._refresh, before=lambda: setattr(self, "syncing", True))

    def _refresh(self) -> None:
        self._sync(force=True)
        if self.wave_available:
            self._rebuild()
            self._start("warm", self._warm_vectors)

    # --- библиотека ---

    def _sync(self, force: bool = False) -> None:
        with self._sync_lock:
            if self._snapshot is not None and not force:
                return
            self.syncing = True
            try:
                snapshot = self.deps.library.sync()
                self.sync_error = None
            except Exception as exc:  # noqa: BLE001
                self.sync_error = str(exc)
                log.exception("Синхронизация библиотеки не удалась")
                return
            finally:
                self.syncing = False
            with self._lock:
                self._snapshot = snapshot

    def snapshot(self) -> LibrarySnapshot:
        snapshot = self._snapshot
        if snapshot is None:
            self._sync()  # самой первой синхронизации приходится подождать
            snapshot = self._snapshot
            if snapshot is None:
                raise RuntimeError(self.sync_error or "библиотека не загрузилась")
        elif time.time() - snapshot.synced_at > self.MAX_AGE:
            self._start("library", self._refresh)
        return snapshot

    def index(self) -> LibraryIndex:
        snapshot = self.snapshot()
        with self._lock:
            if self._index is None or self._index.snapshot is not snapshot:
                self._index = LibraryIndex(snapshot)
            return self._index

    def loaded_index(self) -> LibraryIndex | None:
        return self._index

    def rebuild_in_background(self) -> None:
        """Пересобрать вкус сейчас, не дожидаясь 25 событий (например, после выбора любимых артистов)."""
        self._start("profile", self._rebuild)

    def refresh(self) -> LibraryIndex:
        """Синхронизировать библиотеку сейчас и дождаться (после докачки при импорте)."""
        self._sync(force=True)
        return self.index()

    # --- профиль вкуса ---

    def get(self) -> TasteProfile:
        with self._lock:
            profile, built_for = self._profile, self._profile_snapshot
        if profile is None:
            with self._build_lock:
                if self._profile is None:
                    self._rebuild()
            return self._profile
        if self._events >= self.REBUILD_EVENTS or built_for is not self._snapshot:
            self._start("profile", self._rebuild)  # пока пересобирается, волна работает на старом
        return profile

    def _rebuild(self) -> None:
        with self._build_lock:
            snapshot = self.snapshot()
            started = time.time()
            profile = build_profile(self.deps, snapshot)
            with self._lock:
                self._profile, self._profile_snapshot, self._events = profile, snapshot, 0
            log.info("Профиль вкуса собран за %.1f с: %d кластеров", time.time() - started, len(profile.clusters))

    def note_event(self) -> None:
        self._events += 1

    # --- прогрев кэша векторов ---

    def _warm_vectors(self) -> None:
        """Один раз загрузить векторы и признаки всей библиотеки: сначала то, что вы слушаете."""
        songs = sorted(self.snapshot().songs, key=lambda t: (not t.starred, -t.play_count))
        ids = [t.id for t in songs]
        self.warm = {"done": 0, "total": len(ids), "running": True}
        started, fetched = time.time(), 0
        try:
            for i in range(0, len(ids), self.WARM_CHUNK):
                fetched += self.deps.vectors.warm(ids[i : i + self.WARM_CHUNK])
                self.warm["done"] = min(len(ids), i + self.WARM_CHUNK)
        finally:
            self.warm["running"] = False
        if fetched:
            log.info("Кэш волны прогрет за %.0f с: загружено %d записей", time.time() - started, fetched)


class WebFiles(StaticFiles):
    """Браузер сверяет файлы сайта при каждой загрузке: после выкатки новая версия видна сразу."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Space:
    """Всё своё у одного пользователя Navidrome: библиотека его глазами (лайки, прослушивания),
    история волны, профиль вкуса, волны и настройки. Общий у всех только кэш звучания."""

    MAX_SESSIONS = 20

    def __init__(self, deps: Deps, user: str = "", defaults: Store | None = None):
        self.deps = deps
        self.user = user  # для импорта: чьи это плейлисты
        self.defaults = defaults  # у основного пространства — настройки времён, когда пользователь был один
        self.profiles = ProfileManager(deps)
        self.sessions: dict[str, WaveSession] = {}
        self.devices = Devices()  # что играет на телефонах и в браузерах этого пользователя
        self._artist_map: ArtistMap | None = None

    def artist_map(self, index: LibraryIndex) -> ArtistMap:
        """Похожие артисты для первого входа; считается один раз на снимок библиотеки."""
        current = self._artist_map
        if current is None or current.index is not index:
            current = self._artist_map = ArtistMap(index, self.deps.vectors)
        return current

    @property
    def store(self) -> Store:
        return self.deps.store

    def setting(self, key: str):
        value = self.store.setting(key)
        if value is None and self.defaults is not None:
            value = self.defaults.setting(key)
        return value

    def settings(self) -> WaveSettings:
        return WaveSettings.from_dict(self.setting(SETTINGS_KEY))

    # --- для импорта плейлистов ---

    def index(self, refresh: bool) -> LibraryIndex:
        return self.profiles.refresh() if refresh else self.profiles.index()

    def create_playlist(self, name: str, track_ids: list[str]):
        playlist = self.deps.library.create_playlist(name, track_ids)
        index = self.profiles.loaded_index()
        if index is not None and playlist.id:
            index.add_playlist(playlist)
        return playlist

    def scanning(self) -> bool:
        return bool(getattr(self.deps.library, "scanning", lambda: False)())

    def preferences(self) -> dict:
        return {**DEFAULT_PREFS, **(self.setting(PREFS_KEY) or {})}


class Runtime:
    """Текущее подключение: демо или сервер. Меняется на лету из настроек.

    Сервис подключается к Navidrome одним аккаунтом (его пространство — основное, с него же
    прогревается общий кэш звучания), а входить может любой пользователь Navidrome: у каждого
    своё пространство, которое заводится при первом обращении.
    """

    def __init__(self, settings: Settings, deps: Deps | None = None):
        self.settings = settings
        self.config = Store(settings.db_path)  # настройки и сохранённый сервер
        self.lock = threading.RLock()
        self.server: ServerConfig | None = None
        self.source = "demo"
        if deps is not None:
            self.install(deps, None)
        else:
            self.connect(*self.initial_server())

    def initial_server(self) -> tuple[ServerConfig | None, str]:
        if self.settings.force_demo:
            return None, "demo"
        saved = self.config.setting(SERVER_KEY)
        if saved:
            return ServerConfig(**saved), "saved"
        if self.settings.env_server:
            return self.settings.env_server, "env"
        return None, "demo"

    def connect(self, server: ServerConfig | None, source: str = "saved") -> None:
        self.install(build_deps(server, self.settings.db_path.parent), server, source if server else "demo")

    def install(self, deps: Deps, server: ServerConfig | None, source: str = "demo") -> None:
        deps.background_io = True  # скробблы и звёзды не задерживают ответы
        with self.lock:
            self.deps = deps
            self.server = server
            self.source = source
            self.main = Space(deps, server.navidrome_user if server else "", defaults=self.config)
            self.spaces: dict[str, Space] = {}
        self.main.profiles.start()

    # --- пользователи ---

    def users(self) -> dict[str, dict]:
        return self.config.setting(USERS_KEY) or {}

    def remember_user(self, user: str, password: str, admin: bool) -> None:
        """Пароль нужен, чтобы ходить в Navidrome от имени пользователя: лежит рядом с паролем сервиса."""
        key = user.casefold()
        with self.lock:
            users = self.users()
            known = users.get(key)
            users[key] = {"user": user, "password": password, "admin": admin}
            self.config.put_setting(USERS_KEY, users)
            if known is not None and known["password"] != password:
                self.spaces.pop(key, None)  # пароль сменился: клиента Navidrome соберём заново

    def is_admin(self, user: str | None) -> bool:
        if not user:
            return False
        server = self.server
        if server is not None and user.casefold() == server.navidrome_user.casefold():
            return True  # кто подключил сервис, тот им и управляет
        return bool(self.users().get(user.casefold(), {}).get("admin"))

    def space(self, user: str | None) -> Space:
        """Пространство пользователя. Без входа (сервис дома или демо) — основное."""
        server = self.server
        if not user or server is None or user.casefold() == server.navidrome_user.casefold():
            return self.main
        key = user.casefold()
        with self.lock:
            space = self.spaces.get(key)
            created = space is None
            if created:
                creds = self.users().get(key)
                if creds is None:
                    raise LookupError(user)
                account = replace(server, navidrome_user=creds["user"], navidrome_password=creds["password"])
                space = Space(user_deps(account, self.deps, self.settings.db_path.parent), creds["user"])
                self.spaces[key] = space
        if created:
            space.profiles.start()
        return space


def check_server(server: ServerConfig) -> dict:
    """Проверить подключение, ничего не сохраняя."""
    from .audiomuse import AudioMuse
    from .navidrome import Navidrome, SubsonicError

    result: dict = {}
    try:
        info = Navidrome(
            server.navidrome_url, server.navidrome_user, server.navidrome_password, Store(":memory:"), timeout=8
        ).server_info()
        result["navidrome"] = {"ok": True, **info}
    except httpx.ConnectError:
        result["navidrome"] = {"ok": False, "error": "Не удалось подключиться: проверьте адрес и что сервер доступен"}
    except httpx.TimeoutException:
        result["navidrome"] = {"ok": False, "error": "Сервер не ответил за 8 секунд"}
    except httpx.HTTPStatusError as exc:
        result["navidrome"] = {"ok": False, "error": f"Сервер ответил {exc.response.status_code}: это точно Navidrome?"}
    except SubsonicError as exc:
        result["navidrome"] = {"ok": False, "error": f"Navidrome: {exc}"}
    except Exception as exc:  # noqa: BLE001
        result["navidrome"] = {"ok": False, "error": str(exc)}

    if not server.audiomuse_url:
        result["audiomuse"] = {"ok": None, "error": "не указан: библиотека будет работать, волна — нет"}
    else:
        client = AudioMuse(
            server.audiomuse_url, server.audiomuse_token, server.audiomuse_user, server.audiomuse_password, timeout=8
        )
        try:
            client.ping()
            result["audiomuse"] = {"ok": True, "auth": {"token": "по токену", "login": "по логину"}.get(client.auth_mode, "")}
        except PermissionError as exc:
            result["audiomuse"] = {"ok": False, "error": str(exc)}
        except httpx.HTTPError:
            result["audiomuse"] = {"ok": False, "error": "Не удалось подключиться к AudioMuse: проверьте адрес"}
        except Exception as exc:  # noqa: BLE001
            result["audiomuse"] = {"ok": False, "error": str(exc)}
    return result


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    rt = Runtime(settings, deps)
    app = FastAPI(title="VaginaWave", version=__version__)
    app.state.runtime = rt
    auth = Auth(rt.config, settings.auth)
    app.state.auth = auth

    def navidrome_login(user: str, password: str) -> bool:
        """Войти может любой пользователь Navidrome: пароль проверяет сам Navidrome."""
        from .navidrome import Navidrome, SubsonicError

        server = rt.server
        if server is None:
            raise LoginError(503, "Сервис ещё не подключён к Navidrome")
        client = Navidrome(server.navidrome_url, user, password, Store(":memory:"), timeout=8)
        try:
            client.ping()
        except SubsonicError:
            return False
        except httpx.HTTPError as exc:
            raise LoginError(503, "Navidrome не отвечает, попробуйте позже") from exc
        try:
            admin = client.is_admin()
        except Exception:  # noqa: BLE001 — сервер без getUser: обычный пользователь
            admin = False
        rt.remember_user(user, password, admin)
        return True

    auth.verify = navidrome_login
    importer = (
        Importer(Explo(settings.explo_url, settings.explo_user, settings.explo_password))
        if settings.explo_url and settings.explo_user
        else None
    )
    app.state.importer = importer
    app.state.lidarr = Lidarr(settings.lidarr_url, settings.lidarr_api_key) if settings.lidarr_url and settings.lidarr_api_key else None
    open_paths = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        user = auth.user(request_token(request)) if auth.enabled else None
        if auth.enabled and path.startswith("/api/") and path not in open_paths and not user:
            return JSONResponse({"detail": "Нужно войти"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    def current_space(request: Request) -> Space:
        try:
            return rt.space(getattr(request.state, "user", None))
        except LookupError as exc:  # токен есть, а учётки нет (например, данные сервиса стёрли)
            raise HTTPException(401, "Войдите снова") from exc

    def require_admin(request: Request) -> None:
        if auth.enabled and not rt.is_admin(getattr(request.state, "user", None)):
            raise HTTPException(403, "Менять сервер может только администратор Navidrome")

    @app.middleware("http")
    async def fresh_ui(request: Request, call_next):
        """Файлы интерфейса всегда перепроверяются, иначе после обновления браузер держит старый JS."""
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def refill_later(session: WaveSession) -> None:
        """Следующую порцию собираем после ответа и без замка на время сетевых запросов:
        «дальше» не ждёт AudioMuse, даже если пропускать трек за треком."""
        if not session.needs_refill() or session.refilling:
            return
        session.refilling = True

        def run() -> None:
            try:
                with session.lock:
                    if not session.needs_refill():
                        return
                    prepared = session.prepare_refill()
                rows = session.fetch_refill(prepared)
                with session.lock:
                    session.finish_refill(prepared, rows)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось дозаполнить очередь волны")
            finally:
                session.refilling = False

        threading.Thread(target=run, name="volna-refill", daemon=True).start()

    def remember(sp: Space, session: WaveSession) -> None:
        session.background_refill = True
        session.low_water = 6
        sp.sessions[session.id] = session
        while len(sp.sessions) > Space.MAX_SESSIONS:
            oldest = min(sp.sessions.values(), key=lambda s: s.touched)
            sp.sessions.pop(oldest.id)

    def session_or_404(sp: Space, session_id: str) -> WaveSession:
        session = sp.sessions.get(session_id)  # чужие волны не видно: у каждого свои
        if session is None:
            raise HTTPException(404, "Сессия не найдена: начните волну заново")
        return session

    def taste_or_503(sp: Space) -> TasteProfile:
        if isinstance(sp.deps.sim, NoSimilarity):
            raise HTTPException(503, NoSimilarity.MESSAGE)
        try:
            return sp.profiles.get()
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось собрать профиль вкуса")
            raise HTTPException(503, f"Не удалось собрать профиль вкуса: {exc}") from exc

    def index_or_503(sp: Space) -> LibraryIndex:
        try:
            return sp.profiles.index()
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось загрузить библиотеку")
            raise HTTPException(503, f"Не удалось загрузить библиотеку: {exc}") from exc

    def state(session: WaveSession) -> dict:
        return {
            "session": session.id,
            "kind": session.kind,
            "title": session.title,
            "settings": session.settings.to_dict(),
            "track": track_json(session.current, session.reason(session.current.id)) if session.current else None,
            "upcoming": [track_json(t, session.reason(t.id)) for t in session.queue[:3]],
        }

    def seed_tracks(sp: Space, seed: Seed) -> tuple[list[str], str]:
        index = index_or_503(sp)
        if seed.type == "track":
            track = index.songs.get(seed.id) or sp.deps.library.songs([seed.id]).get(seed.id)
            return [seed.id], (track.title if track else "трек")
        if seed.type == "album":
            songs = index.by_album.get(seed.id, [])
            return [t.id for t in songs], (songs[0].album if songs else "альбом")
        if seed.type == "artist":
            songs = sorted(index.by_artist.get(seed.id, []), key=lambda t: -t.play_count)
            return [t.id for t in songs[:60]], (songs[0].artist if songs else "артист")
        playlist = index.playlists.get(seed.id)
        return (list(playlist.track_ids), playlist.name) if playlist else ([], "плейлист")

    def make_steer(sp: Space, seed: Seed, taste: TasteProfile) -> tuple[Steer, list[str]]:
        """Источник смещения и что исключить из выдачи (сам трек, альбом или плейлист)."""
        ids, title = seed_tracks(sp, seed)
        if not ids:
            raise HTTPException(404, "Не нашёл треков для этого элемента")
        try:
            profile = build_context_profile(sp.deps, ids, taste)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        exclude = ids if seed.type != "artist" else [seed.id]  # у артиста его же треки звучать могут
        return Steer(profile, seed.type, seed.id, title), exclude

    def mark_starred(sp: Space, item_id: str, entity: str, liked: bool) -> None:
        index = sp.profiles.loaded_index()
        if index is not None:
            index.set_starred(item_id, entity, liked)

    # --- служебное и сервер ---

    @app.get("/api/health")
    def health(request: Request) -> dict:
        status = {"service": "volna", "mode": "demo" if rt.deps.demo else "live", "version": __version__, "auth": auth.enabled}
        if auth.enabled and not auth.user(request_token(request)):
            return status  # до входа не рассказываем, к чему подключён сервис
        for name, service in (("navidrome", rt.deps.library), ("audiomuse", rt.deps.sim)):
            try:
                status[name] = bool(service.ping())
            except Exception as exc:  # noqa: BLE001
                status[name] = False
                status[f"{name}_error"] = str(exc)
        return status

    # --- вход ---

    def cookie_path(request: Request) -> str:
        # за Caddy сервис живёт по адресу /volna/: cookie нужна только ему, а не Navidrome рядом
        return request.headers.get("x-forwarded-prefix", "").rstrip("/") + "/"

    @app.post("/api/auth/login")
    def login(form: LoginForm, request: Request, response: Response) -> dict:
        if not auth.enabled:
            return {"auth": False, "token": "", "user": ""}
        user = form.user.strip()
        try:
            token = auth.login(user, form.password, ip=client_ip(request), device=form.device)
        except LoginError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(
            COOKIE, token, max_age=TOKEN_TTL, httponly=True, samesite="lax", secure=secure, path=cookie_path(request)
        )
        return {"auth": True, "token": token, "user": user}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict:
        auth.logout(request_token(request))
        response.delete_cookie(COOKIE, path=cookie_path(request))
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(request: Request) -> dict:
        return {"auth": auth.enabled, "user": auth.user(request_token(request)) if auth.enabled else None}

    @app.get("/api/server")
    def get_server(request: Request, sp: Space = Depends(current_space)) -> dict:
        index = sp.profiles.loaded_index()
        manage = not auth.enabled or rt.is_admin(getattr(request.state, "user", None))
        server = rt.server.public() if rt.server else None
        if server is not None and not manage:  # остальным незачем видеть учётки сервиса
            server = {"navidrome_url": server["navidrome_url"], "audiomuse_url": server["audiomuse_url"]}
        return {
            "mode": "demo" if rt.deps.demo else "live",
            "source": rt.source,
            "server": server,
            "can_manage": manage,
            "user": sp.user,
            "syncing": sp.profiles.syncing,
            "sync_error": sp.profiles.sync_error,
            "library": index.stats() if index else None,
            "warm": sp.profiles.warm,
        }

    def server_from_form(form: ServerForm) -> ServerConfig:
        url = normalize_url(form.navidrome_url)
        user = form.navidrome_user.strip()
        audiomuse = normalize_url(form.audiomuse_url) if form.audiomuse_url.strip() else ""
        prev = rt.server
        same_login = prev is not None and prev.navidrome_url == url and prev.navidrome_user == user
        password = form.navidrome_password or (prev.navidrome_password if same_login else "")
        same_audiomuse = prev is not None and prev.audiomuse_url == audiomuse
        token = form.audiomuse_token or (prev.audiomuse_token if same_audiomuse else "")
        am_user = form.audiomuse_user.strip()
        am_password = form.audiomuse_password or (
            prev.audiomuse_password if same_audiomuse and prev.audiomuse_user == am_user else ""
        )
        return ServerConfig(url, user, password, audiomuse, token, am_user, am_password)

    @app.post("/api/server/test", dependencies=[Depends(require_admin)])
    def test_server(form: ServerForm) -> dict:
        return check_server(server_from_form(form))

    @app.put("/api/server", dependencies=[Depends(require_admin)])
    def save_server(form: ServerForm, request: Request) -> dict:
        server = server_from_form(form)
        result = check_server(server)
        if not result["navidrome"]["ok"]:
            raise HTTPException(400, result["navidrome"]["error"])
        rt.config.put_setting(SERVER_KEY, server.to_dict())
        rt.connect(server, "saved")
        rt.main.profiles.refresh_in_background()
        return {**result, **get_server(request, current_space(request))}

    @app.delete("/api/server", dependencies=[Depends(require_admin)])
    def forget_server(request: Request) -> dict:
        rt.config.put_setting(SERVER_KEY, None)
        rt.connect(None)
        return get_server(request, current_space(request))

    # --- настройки и предпочтения ---

    @app.get("/api/settings")
    def get_settings(sp: Space = Depends(current_space)) -> dict:
        return {
            "settings": sp.settings().to_dict(),
            "options": {"character": list(CHARACTERS), "mood": list(MOODS), "activity": list(ACTIVITIES)},
        }

    @app.put("/api/settings")
    def put_settings(patch: dict, sp: Space = Depends(current_space)) -> dict:
        updated = sp.settings().merged(patch)
        sp.store.put_setting(SETTINGS_KEY, updated.to_dict())
        return {"settings": updated.to_dict()}

    @app.get("/api/preferences")
    def get_preferences(sp: Space = Depends(current_space)) -> dict:
        return sp.preferences()

    @app.put("/api/preferences")
    def put_preferences(patch: dict, sp: Space = Depends(current_space)) -> dict:
        prefs = sp.preferences()
        if patch.get("quality") in QUALITY:
            prefs["quality"] = patch["quality"]
        if "continue_after_end" in patch:
            prefs["continue_after_end"] = bool(patch["continue_after_end"])
        sp.store.put_setting(PREFS_KEY, prefs)
        return prefs

    # --- волна ---

    @app.post("/api/wave/start")
    def start_wave(body: StartRequest, sp: Space = Depends(current_space)) -> dict:
        taste = taste_or_503(sp)
        wave_settings = sp.settings().merged(body.settings or {})
        if body.seed is None:
            session = WaveSession(sp.deps, taste, wave_settings, device=body.device)
        else:
            # «Моя волна по…»: та же личная волна, только смещённая к треку, альбому, артисту или плейлисту
            steer, exclude = make_steer(sp, body.seed, taste)
            session = WaveSession(sp.deps, taste, wave_settings, steer=steer, exclude=exclude, device=body.device)
        remember(sp, session)
        with session.lock:
            session.start()
            response = state(session)
        refill_later(session)
        return response

    @app.post("/api/wave/{session_id}/advance")
    def advance(session_id: str, body: AdvanceRequest, sp: Space = Depends(current_space)) -> dict:
        session = session_or_404(sp, session_id)
        with session.lock:
            session.advance(body.track_id, body.outcome, body.position, body.duration)
            response = state(session)
        refill_later(session)
        sp.profiles.note_event()
        return response

    @app.post("/api/wave/{session_id}/like")
    def like(session_id: str, body: LikeRequest, sp: Space = Depends(current_space)) -> dict:
        session = session_or_404(sp, session_id)
        with session.lock:
            session.like(body.track_id, body.liked)
        mark_starred(sp, body.track_id, "track", body.liked)
        sp.profiles.note_event()
        return state(session)

    @app.post("/api/wave/{session_id}/settings")
    def session_settings(session_id: str, patch: dict, sp: Space = Depends(current_space)) -> dict:
        """Поменять настройки на ходу; они же сохраняются как настройки по умолчанию."""
        session = session_or_404(sp, session_id)
        updated = session.settings.merged(patch)
        sp.store.put_setting(SETTINGS_KEY, updated.to_dict())
        with session.lock:
            session.set_settings(updated)
        return state(session)

    @app.post("/api/wave/{session_id}/shake")
    def shake(session_id: str, sp: Space = Depends(current_space)) -> dict:
        session = session_or_404(sp, session_id)
        with session.lock:
            session.shake()
        return state(session)

    @app.get("/api/wave/{session_id}/debug")
    def debug(session_id: str, sp: Space = Depends(current_space)) -> dict:
        return session_or_404(sp, session_id).snapshot()

    @app.post("/api/continue")
    def continue_playlist(body: ContinueRequest, sp: Space = Depends(current_space)) -> dict:
        """Альбом или плейлист закончился: дальше ваша волна, смещённая к нему целиком,
        а не «похожее на последнюю песню»."""
        taste = taste_or_503(sp)
        try:
            context = build_context_profile(sp.deps, body.track_ids, taste)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        session = WaveSession(
            sp.deps, taste, sp.settings().merged(body.settings or {}),
            steer=Steer(context, "list", "", body.title), exclude=body.track_ids, device=body.device,
        )
        remember(sp, session)
        with session.lock:
            session.start()
            response = state(session)
        refill_later(session)
        return response

    # --- плейлисты по песне ---

    @app.post("/api/mix")
    def mix(body: MixRequest, sp: Space = Depends(current_space)) -> dict:
        """Собрать плейлист по треку (или альбому, артисту, плейлисту): сохранить или сразу слушать."""
        taste = taste_or_503(sp)
        steer, exclude = make_steer(sp, body.seed, taste)
        tracks = build_mix(sp.deps, taste, steer, sp.settings(), size=body.size, exclude=exclude)
        if body.seed.type == "track":
            first = (sp.profiles.loaded_index() or index_or_503(sp)).songs.get(body.seed.id)
            first = first or sp.deps.library.songs([body.seed.id]).get(body.seed.id)
            if first is not None:
                tracks = [first, *tracks[: body.size - 1]]
        reason = f"Похоже на «{steer.title}»"
        return {
            "title": reason,
            "seed": body.seed.model_dump(),
            "source": steer.title,
            "tracks": [track_json(t) for t in tracks],  # в подборке у строк артист, а не одинаковая причина
        }

    @app.post("/api/playlists")
    def create_playlist(body: PlaylistCreate, sp: Space = Depends(current_space)) -> dict:
        """Сохранить подборку плейлистом в Navidrome: он появится во всех клиентах."""
        try:
            playlist = sp.deps.library.create_playlist(body.name.strip(), body.track_ids)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Navidrome не создал плейлист: {exc}") from exc
        index = sp.profiles.loaded_index()
        if index is not None and playlist.id:
            index.add_playlist(playlist)
        return {"id": playlist.id, "name": playlist.name, "count": len(playlist.track_ids)}

    # --- первый вход: любимые артисты и предпочтения ---

    @app.get("/api/onboarding")
    def onboarding(sp: Space = Depends(current_space)) -> dict:
        return {
            "done": bool(sp.setting(ONBOARDED_KEY)),
            "artists": artist_choices(index_or_503(sp)),
            "settings": sp.settings().to_dict(),
        }

    @app.get("/api/onboarding/similar/{artist_id}")
    def onboarding_similar(artist_id: str, sp: Space = Depends(current_space)) -> dict:
        """Похожие по звучанию: появляются рядом с отмеченным артистом, как в Яндексе."""
        index = index_or_503(sp)
        if isinstance(sp.deps.sim, NoSimilarity):
            return {"artists": []}
        ids = sp.artist_map(index).similar(artist_id)
        return {"artists": [index.artist_json(index.artists[i]) for i in ids if i in index.artists]}

    @app.post("/api/onboarding")
    def finish_onboarding(body: OnboardingForm, sp: Space = Depends(current_space)) -> dict:
        index = index_or_503(sp)
        liked = 0
        for artist_id in body.artists:
            artist = index.artists.get(artist_id)
            if artist is None or artist.starred:
                continue
            sp.deps.run_io(sp.deps.library.star, artist_id, "artist")  # звезда видна во всех клиентах
            sp.store.add_event(Event(ts=time.time(), track_id=artist_id, kind="like", entity="artist"))
            mark_starred(sp, artist_id, "artist", True)
            liked += 1
        settings = sp.settings().merged(body.settings)
        sp.store.put_setting(SETTINGS_KEY, settings.to_dict())
        sp.store.put_setting(ONBOARDED_KEY, True)
        if liked and not isinstance(sp.deps.sim, NoSimilarity):
            sp.profiles.rebuild_in_background()  # первая волна — уже вокруг выбранных
        return {"done": True, "liked": liked, "settings": settings.to_dict()}

    # --- дискографии: исполнители на докачку через Lidarr ---

    def lidarr_or_503() -> Lidarr:
        if app.state.lidarr is None:
            raise HTTPException(503, "Докачка дискографий не настроена: нужен доступ к Lidarr")
        return app.state.lidarr

    def lidarr_call(fn, *args):
        try:
            return fn(*args)
        except LidarrError as exc:
            raise HTTPException(409, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Lidarr не отвечает: {exc}") from exc

    @app.get("/api/lidarr/artist/{artist_id}")
    def lidarr_artist(artist_id: str, sp: Space = Depends(current_space)) -> dict:
        """Качается ли дискография артиста из библиотеки и сколько альбомов уже есть."""
        if app.state.lidarr is None:
            return {"available": False}
        artist = index_or_503(sp).artists.get(artist_id)
        if artist is None:
            raise HTTPException(404, "Артист не найден")
        lidarr = app.state.lidarr
        return {"available": True, **lidarr_call(lambda: lidarr.status(lidarr.find(artist.name)))}

    @app.post("/api/lidarr/artist/{artist_id}")
    def lidarr_follow_artist(artist_id: str, sp: Space = Depends(current_space)) -> dict:
        """«Скачать дискографию» со страницы артиста."""
        lidarr = lidarr_or_503()
        artist = index_or_503(sp).artists.get(artist_id)
        if artist is None:
            raise HTTPException(404, "Артист не найден")
        known = lidarr_call(lidarr.find, artist.name)
        mbid = known["foreignArtistId"] if known else lidarr_call(lidarr.resolve, artist.name)
        followed = lidarr_call(lidarr.follow, mbid)
        return {"available": True, **lidarr_call(lidarr.status, followed)}

    @app.get("/api/lidarr/search")
    def lidarr_search(q: str = "", sp: Space = Depends(current_space)) -> dict:
        lidarr = lidarr_or_503()
        return {"artists": lidarr_call(lidarr.search, q.strip()) if q.strip() else []}

    @app.post("/api/lidarr/follow")
    def lidarr_follow(body: FollowRequest, sp: Space = Depends(current_space)) -> dict:
        """Исполнитель из поиска MusicBrainz: Soularr докачает его студийные альбомы и новые релизы."""
        lidarr = lidarr_or_503()
        followed = lidarr_call(lidarr.follow, body.mbid)
        return {"ok": True, "name": followed.get("artistName", "")}

    @app.get("/api/lidarr")
    def lidarr_available(sp: Space = Depends(current_space)) -> dict:
        return {"available": app.state.lidarr is not None}

    # --- импорт плейлистов по ссылке ---

    @app.get("/api/imports")
    def list_imports(sp: Space = Depends(current_space)) -> dict:
        importer = app.state.importer
        return {"available": importer is not None, "items": importer.list(sp) if importer else []}

    @app.post("/api/imports")
    def start_import(body: ImportRequest, sp: Space = Depends(current_space)) -> dict:
        """Плейлист или альбом Spotify, Apple Music: Explo докачает недостающее, плейлист появится у вас."""
        importer = app.state.importer
        if importer is None:
            raise HTTPException(503, "Импорт не настроен: VaginaWave нужен доступ к Explo")
        url = body.url.strip()
        if source_of(url) is None:
            raise HTTPException(422, "Нужна ссылка на плейлист или альбом Spotify или Apple Music")
        return importer.start(url, sp).to_dict()

    # --- сигналы вне волны ---

    @app.post("/api/events")
    def playback_event(body: PlaybackEvent, sp: Space = Depends(current_space)) -> dict:
        """Прослушивание в альбоме, плейлисте или поиске: тоже учит профиль."""
        index = sp.profiles.loaded_index()
        track = (index.songs.get(body.track_id) if index else None) or sp.deps.library.songs([body.track_id]).get(body.track_id)
        kind = classify(body.outcome, body.position, body.duration or (track.duration if track else 0.0))
        try:
            taste = None if isinstance(sp.deps.sim, NoSimilarity) else sp.profiles.get()
        except Exception:  # noqa: BLE001 — профиля нет, но событие и скроббл всё равно сохраняем
            taste = None
        now = time.time()
        register_playback(
            sp.deps, taste, body.track_id, kind, track=track, position=body.position, duration=body.duration,
            context_type=body.context_type, context_id=body.context_id, device=body.device,
            ts=min(now, max(now - OFFLINE_DAYS * 86400, body.at)) if body.at else now,
        )
        sp.profiles.note_event()
        return {"kind": kind}

    # --- другие устройства: «Играет на X. Слушать здесь?» ---

    def device_json(sp: Space, d: dict) -> dict:
        ids = [d["track_id"], *d.get("next_ids", [])]
        index = sp.profiles.loaded_index()
        known = {i: index.songs[i] for i in ids if index is not None and i in index.songs}
        missing = [i for i in ids if i not in known]
        if missing:
            try:
                known.update(sp.deps.library.songs(missing))
            except Exception:  # noqa: BLE001 — без названий устройство всё равно видно
                log.warning("Не удалось получить треки для устройства %s", d.get("name"))
        track = known.get(d["track_id"])
        return {
            "device_id": d["device_id"],
            "name": d.get("name") or "Другое устройство",
            "kind": d.get("kind", ""),
            "playing": bool(d.get("playing")),
            "position": round(d.get("position", 0.0), 1),
            "mode": d.get("mode", ""),
            "source": d.get("source", ""),
            "track": track_json(track) if track else None,
            "next": [track_json(known[i]) for i in d.get("next_ids", []) if i in known],
            "seconds_ago": round(max(0.0, time.time() - d["updated"]), 1),
        }

    @app.put("/api/devices/me")
    def device_report(body: DeviceState, sp: Space = Depends(current_space)) -> dict:
        """Устройство сообщает, что у него играет; в ответ — команды ему и другие устройства."""
        commands = sp.devices.report(body.device_id, body.model_dump())
        return {"commands": commands, "devices": [device_json(sp, d) for d in sp.devices.others(body.device_id)]}

    @app.get("/api/devices")
    def devices_list(device_id: str = "", sp: Space = Depends(current_space)) -> dict:
        return {"devices": [device_json(sp, d) for d in sp.devices.others(device_id)]}

    @app.post("/api/devices/{device_id}/pause")
    def device_pause(device_id: str, by: str = "", sp: Space = Depends(current_space)) -> dict:
        """Музыку забрали на другое устройство: прежнее встанет на паузу со следующим отчётом."""
        if not sp.devices.command(device_id, "pause", by=by[:80]):
            raise HTTPException(404, "Устройство больше не на связи")
        return {"ok": True}

    @app.post("/api/like")
    def like_entity(body: EntityLikeRequest, sp: Space = Depends(current_space)) -> dict:
        """Лайк трека, альбома или артиста: звезда в Navidrome и событие в профиль."""
        try:
            (sp.deps.library.star if body.liked else sp.deps.library.unstar)(body.id, body.type)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Navidrome не принял отметку: {exc}") from exc
        sp.deps.store.add_event(
            Event(ts=time.time(), track_id=body.id, kind="like" if body.liked else "unlike", entity=body.type)
        )
        mark_starred(sp, body.id, body.type, body.liked)
        sp.profiles.note_event()
        return {"ok": True, "liked": body.liked}

    @app.post("/api/like/tracks")
    def like_tracks(body: TracksLikeRequest, sp: Space = Depends(current_space)) -> dict:
        """«Лайкнуть все» в плейлисте или альбоме: одной пачкой в Navidrome и событиями во вкус."""
        index = sp.profiles.loaded_index()
        ids = [i for i in dict.fromkeys(body.track_ids) if index is None or i in index.songs]
        try:
            sp.deps.library.star_tracks(ids, body.liked)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Navidrome не принял отметки: {exc}") from exc
        now = time.time()
        for track_id in ids:
            sp.store.add_event(Event(ts=now, track_id=track_id, kind="like" if body.liked else "unlike", entity="track"))
            mark_starred(sp, track_id, "track", body.liked)
        sp.profiles.note_event()
        return {"ok": True, "count": len(ids), "liked": body.liked}

    # --- библиотека ---

    @app.get("/api/home")
    def home(sp: Space = Depends(current_space)) -> dict:
        return {"shelves": index_or_503(sp).home()}

    @app.get("/api/library")
    def library_stats(sp: Space = Depends(current_space)) -> dict:
        return index_or_503(sp).stats()

    @app.post("/api/library/sync")
    def library_sync(sp: Space = Depends(current_space)) -> dict:
        sp.profiles.refresh_in_background()
        return {"syncing": True}

    @app.get("/api/library/albums")
    def library_albums(sort: str = "alpha", offset: int = 0, limit: int = 60, sp: Space = Depends(current_space)) -> dict:
        return {"items": index_or_503(sp).albums_sorted(sort, offset, min(limit, 5000))}  # приложение берёт всё и ищет у себя

    @app.get("/api/library/artists")
    def library_artists(sort: str = "alpha", offset: int = 0, limit: int = 100, sp: Space = Depends(current_space)) -> dict:
        return {"items": index_or_503(sp).artists_sorted(sort, offset, min(limit, 5000))}

    @app.get("/api/library/playlists")
    def library_playlists(sp: Space = Depends(current_space)) -> dict:
        return {"items": index_or_503(sp).playlists_list()}

    @app.get("/api/library/liked")
    def library_liked(sp: Space = Depends(current_space)) -> dict:
        return {"tracks": [track_json(t) for t in index_or_503(sp).liked_tracks()]}

    @app.get("/api/library/reserve")
    def library_reserve(limit: int = 100, sp: Space = Depends(current_space)) -> dict:
        """Что держать скачанным на случай без сети: часто и недавно слушаемое."""
        events = sp.store.events(since=time.time() - 120 * 86400)
        return {"tracks": [track_json(t) for t in index_or_503(sp).reserve_tracks(min(max(limit, 0), 1000), events)]}

    @app.get("/api/library/recent")
    def library_recent(sp: Space = Depends(current_space)) -> dict:
        return {"tracks": [track_json(t) for t in index_or_503(sp).recent_tracks()]}

    @app.get("/api/album/{album_id}")
    def album_page(album_id: str, sp: Space = Depends(current_space)) -> dict:
        page = index_or_503(sp).album_page(album_id)
        if page is None:
            raise HTTPException(404, "Альбом не найден")
        return page

    @app.get("/api/artist/{artist_id}")
    def artist_page(artist_id: str, sp: Space = Depends(current_space)) -> dict:
        page = index_or_503(sp).artist_page(artist_id)
        if page is None:
            raise HTTPException(404, "Артист не найден")
        return page

    @app.get("/api/playlist/{playlist_id}")
    def playlist_page(playlist_id: str, sp: Space = Depends(current_space)) -> dict:
        page = index_or_503(sp).playlist_page(playlist_id)
        if page is None:
            raise HTTPException(404, "Плейлист не найден")
        return page

    @app.get("/api/search")
    def search(q: str = "", sp: Space = Depends(current_space)) -> dict:
        return index_or_503(sp).search(q)

    # --- что система знает о вкусе ---

    @app.get("/api/taste")
    def taste_summary(sp: Space = Depends(current_space)) -> dict:
        """«Ваш вкус»: кого дослушиваете, кого пропускаете, откуда треки заходят лучше."""
        p = taste_or_503(sp)
        artists = []
        names = {t.artist.lower(): t.artist for t in p.track_info.values()}
        for name, stats in p.artist_stats.items():
            total = stats.completes + stats.skips
            if total >= 2:
                artists.append({"artist": names.get(name, name), "completes": round(stats.completes, 1),
                                "skips": round(stats.skips, 1), "rate": round(stats.completes / total, 2)})
        events = sp.deps.store.events(since=time.time() - 90 * 86400)
        by_source: dict[str, Counter] = defaultdict(Counter)
        for e in events:
            if e.entity == "track" and e.kind in ("complete", "early_skip", "late_skip", "dislike"):
                by_source[e.source or "manual"]["complete" if e.kind == "complete" else "skip"] += 1
        return {
            "signals": p.signals,
            "cold_start": p.cold_start,
            "clusters": [
                {"label": c.label, "share": round(c.share, 3), "tracks": len(c.members),
                 "completion_rate": c.completion_rate}
                for c in sorted(p.clusters, key=lambda c: -c.share)
            ],
            "loved_artists": sorted(artists, key=lambda a: (-a["rate"], -a["completes"]))[:8],
            "skipped_artists": sorted(
                (a for a in artists if a["skips"] >= 1.5), key=lambda a: (a["rate"], -a["skips"])
            )[:8],
            "sources": {
                s: {"complete": c["complete"], "skip": c["skip"],
                    "rate": round(c["complete"] / max(1, c["complete"] + c["skip"]), 2)}
                for s, c in by_source.items()
            },
        }

    # --- медиа и веб-плеер ---

    @app.get("/api/lyrics/{track_id}")
    def lyrics(track_id: str, sp: Space = Depends(current_space)) -> dict:
        index = sp.profiles.loaded_index()
        track = index.songs.get(track_id) if index else None
        try:
            return sp.deps.library.lyrics(track_id, track.artist if track else "", track.title if track else "")
        except Exception as exc:  # noqa: BLE001 — без текста плеер работает как обычно
            log.warning("Текст для %s не получен: %s", track_id, exc)
            return {"synced": False, "lines": []}

    @app.get("/api/stream/{track_id}")
    def stream(track_id: str, request: Request, quality: str = "", sp: Space = Depends(current_space)):
        """Поток трека; quality — для загрузок, у которых в приложении своё качество."""
        bitrate = QUALITY.get(quality if quality in QUALITY else sp.preferences()["quality"], 0)
        media = sp.deps.library.stream(track_id, request.headers.get("range"), max_bitrate=bitrate)
        return StreamingResponse(
            iter(media.body),
            status_code=media.status,
            media_type=media.media_type,
            headers=media.headers,
            background=BackgroundTask(media.close),
        )

    @app.get("/api/cover/{cover_id}")
    def cover(cover_id: str, sp: Space = Depends(current_space)):
        media = sp.deps.library.cover(cover_id)
        return StreamingResponse(iter(media.body), status_code=media.status, media_type=media.media_type, headers=media.headers)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", WebFiles(directory=WEB_DIR), name="web")
    return app
