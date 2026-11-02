"""Что играет на других устройствах того же пользователя — для «Играет на X. Слушать здесь?».

Каждое устройство (телефон, браузер) раз в несколько секунд сообщает, что у него играет:
трек, позицию, на паузе ли, и следующие треки. Другие устройства видят это и могут забрать
музыку себе: начинают тот же трек с того же места, а прежнему устройству уходит команда
«пауза», которую оно получит со своим следующим отчётом. Хранится только в памяти: после
перезапуска сервиса устройства просто отчитаются заново.
"""

from __future__ import annotations

import threading
import time

PLAYING_TTL = 45.0  # играющее устройство молчит дольше — считаем, что оно пропало
PAUSED_TTL = 15 * 60.0  # на паузе: показываем, что было, ещё четверть часа
MAX_DEVICES = 20


class Devices:
    def __init__(self):
        self._devices: dict[str, dict] = {}
        self._commands: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def report(self, device_id: str, state: dict, now: float | None = None) -> list[dict]:
        """Запомнить, что играет на устройстве; вернуть команды для него (например, «пауза»)."""
        now = time.time() if now is None else now
        with self._lock:
            self._devices[device_id] = {**state, "device_id": device_id, "updated": now}
            self._forget_stale(now)
            return self._commands.pop(device_id, [])

    def others(self, device_id: str, now: float | None = None) -> list[dict]:
        """Другие устройства пользователя, свежие сначала; у играющих позиция — на сейчас."""
        now = time.time() if now is None else now
        with self._lock:
            self._forget_stale(now)
            found = []
            for d in self._devices.values():
                if d["device_id"] == device_id or not d.get("track_id"):
                    continue
                position = d.get("position", 0.0) + (now - d["updated"] if d.get("playing") else 0.0)
                duration = d.get("duration") or 0.0
                found.append({**d, "position": min(position, duration) if duration else position})
            return sorted(found, key=lambda d: (not d.get("playing"), -d["updated"]))

    def command(self, device_id: str, action: str, by: str = "") -> bool:
        """Передать устройству команду; False — такого устройства нет."""
        with self._lock:
            if device_id not in self._devices:
                return False
            queue = self._commands.setdefault(device_id, [])
            queue[:] = [c for c in queue if c["action"] != action]
            queue.append({"action": action, "by": by})
            if action == "pause":  # пока оно не отчиталось, другие уже не видят его играющим
                self._devices[device_id]["playing"] = False
            return True

    def _forget_stale(self, now: float) -> None:
        for key, d in list(self._devices.items()):
            ttl = PLAYING_TTL if d.get("playing") else PAUSED_TTL
            if now - d["updated"] > ttl:
                self._devices.pop(key, None)
                self._commands.pop(key, None)
        while len(self._devices) > MAX_DEVICES:
            oldest = min(self._devices, key=lambda k: self._devices[k]["updated"])
            self._devices.pop(oldest)
            self._commands.pop(oldest, None)
