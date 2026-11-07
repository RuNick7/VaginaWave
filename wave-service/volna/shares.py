"""Общие ссылки на плейлисты.

Ссылка — случайный токен, который указывает на плейлист владельца; её можно отозвать. С аккаунтом
на этом сервере по ней слушают и сохраняют копию себе. Без аккаунта видно только, что внутри, пока
владелец не включит «Слушать без аккаунта». Такой доступ сам выключается через GUEST_DAYS.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import asdict, dataclass

from .store import Store

SHARES_KEY = "shares"
GUEST_DAYS = 30
GUEST_BITRATE = 192  # посторонним — поток полегче: трафик сервера не резиновый


@dataclass
class Share:
    token: str
    owner: str  # логин владельца; пусто, если сервис работает без входа
    playlist_id: str
    created: float
    guests_until: float = 0.0

    @property
    def guests(self) -> bool:
        return self.guests_until > time.time()


class Shares:
    """Живут в общей базе сервиса: ссылку открывают не в пространстве владельца."""

    def __init__(self, store: Store):
        self.store = store
        self.lock = threading.Lock()

    def _all(self) -> dict[str, dict]:
        return self.store.setting(SHARES_KEY) or {}

    def get(self, token: str) -> Share | None:
        data = self._all().get(token)
        return Share(**data) if data else None

    def of_playlist(self, playlist_id: str) -> Share | None:
        return next((Share(**d) for d in self._all().values() if d["playlist_id"] == playlist_id), None)

    def create(self, owner: str, playlist_id: str) -> Share:
        """У плейлиста одна ссылка: повторное «Поделиться» отдаёт ту же."""
        with self.lock:
            shares = self._all()
            found = next((d for d in shares.values() if d["playlist_id"] == playlist_id), None)
            if found:
                return Share(**found)
            share = Share(secrets.token_urlsafe(12), owner, playlist_id, time.time())
            shares[share.token] = asdict(share)
            self.store.put_setting(SHARES_KEY, shares)
            return share

    def set_guests(self, token: str, on: bool) -> Share | None:
        with self.lock:
            shares = self._all()
            data = shares.get(token)
            if data is None:
                return None
            data["guests_until"] = time.time() + GUEST_DAYS * 86400 if on else 0.0
            self.store.put_setting(SHARES_KEY, shares)
            return Share(**data)

    def revoke(self, token: str) -> None:
        with self.lock:
            shares = self._all()
            if shares.pop(token, None) is not None:
                self.store.put_setting(SHARES_KEY, shares)
