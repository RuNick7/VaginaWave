from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """Читает KEY=VALUE из .env; уже заданные переменные окружения не трогает."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_url(url: str) -> str:
    """Адрес сервера как его ввёл человек: без схемы считаем http для локальной сети, иначе https."""
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        host = url.split("/")[0].split(":")[0]
        local = host == "localhost" or host.endswith(".local") or host.replace(".", "").isdigit()
        url = ("http://" if local else "https://") + url
    return url


@dataclass(frozen=True)
class ServerConfig:
    """Куда подключаться: Navidrome обязателен, AudioMuse нужен для волны."""

    navidrome_url: str
    navidrome_user: str
    navidrome_password: str
    audiomuse_url: str = ""
    audiomuse_token: str = ""
    audiomuse_user: str = ""  # вместо токена можно войти логином и паролем AudioMuse
    audiomuse_password: str = ""

    @property
    def key(self) -> str:
        """Свой файл базы на каждый сервер и логин, чтобы истории не смешивались."""
        return hashlib.sha1(f"{self.navidrome_url}|{self.navidrome_user}".encode()).hexdigest()[:10]

    def to_dict(self) -> dict:
        return asdict(self)

    def public(self) -> dict:
        """То, что можно показать в интерфейсе: без пароля и токена."""
        return {
            "navidrome_url": self.navidrome_url,
            "navidrome_user": self.navidrome_user,
            "audiomuse_url": self.audiomuse_url,
            "has_password": bool(self.navidrome_password),
            "has_token": bool(self.audiomuse_token),
            "audiomuse_user": self.audiomuse_user,
            "has_audiomuse_password": bool(self.audiomuse_password),
        }


@dataclass(frozen=True)
class Settings:
    demo: bool
    navidrome_url: str = ""
    navidrome_user: str = ""
    navidrome_password: str = ""
    audiomuse_url: str = ""
    audiomuse_token: str = ""
    db_path: Path = SERVICE_DIR / "data" / "volna.db"
    host: str = "127.0.0.1"
    port: int = 8765
    force_demo: bool = False  # --demo: не подключаться к сохранённому серверу
    auth: bool = False  # вход аккаунтом Navidrome: обязателен, если сервис открыт в интернет
    explo_url: str = ""  # Explo для импорта плейлистов по ссылке (Spotify, Apple Music)
    explo_user: str = ""
    explo_password: str = ""
    lidarr_url: str = ""  # Lidarr: пользователи ставят исполнителей на докачку дискографий
    lidarr_api_key: str = ""
    lastfm_api_key: str = ""  # популярные треки артиста по всему миру (Last.fm), а не по прослушиваниям здесь

    @property
    def env_server(self) -> ServerConfig | None:
        if self.demo or not self.navidrome_url:
            return None
        return ServerConfig(
            self.navidrome_url, self.navidrome_user, self.navidrome_password, self.audiomuse_url, self.audiomuse_token,
            os.environ.get("AUDIOMUSE_USER", ""), os.environ.get("AUDIOMUSE_PASSWORD", ""),
        )

    @classmethod
    def from_env(cls, force_demo: bool = False) -> "Settings":
        load_env_file(Path.cwd() / ".env")
        load_env_file(SERVICE_DIR / ".env")
        env = os.environ.get
        navidrome_url = env("NAVIDROME_URL", "").rstrip("/")
        demo = force_demo or env("VOLNA_DEMO", "").lower() in ("1", "true", "yes") or not navidrome_url
        # настройки, сохранённый сервер; история каждого сервера лежит рядом в своём файле
        default_db = SERVICE_DIR / "data" / "volna.db"
        return cls(
            force_demo=force_demo,
            demo=demo,
            navidrome_url=navidrome_url,
            navidrome_user=env("NAVIDROME_USER", ""),
            navidrome_password=env("NAVIDROME_PASSWORD", ""),
            audiomuse_url=env("AUDIOMUSE_URL", "").rstrip("/"),
            audiomuse_token=env("AUDIOMUSE_TOKEN", ""),
            db_path=Path(env("VOLNA_DB", "")) if env("VOLNA_DB") else default_db,
            host=env("VOLNA_HOST", "127.0.0.1"),
            port=int(env("VOLNA_PORT", "8765")),
            auth=env("VOLNA_AUTH", "").lower() in ("1", "true", "yes"),
            explo_url=env("EXPLO_URL", "").rstrip("/"),
            explo_user=env("EXPLO_USER", ""),
            explo_password=env("EXPLO_PASSWORD", ""),
            lidarr_url=env("LIDARR_URL", "").rstrip("/"),
            lidarr_api_key=env("LIDARR_API_KEY", ""),
            lastfm_api_key=env("LASTFM_API_KEY", ""),
        )
