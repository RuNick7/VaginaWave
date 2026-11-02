from __future__ import annotations

from fastapi.testclient import TestClient

from volna.app import create_app
from volna.config import Settings
from volna.devices import PAUSED_TTL, PLAYING_TTL, Devices


def test_other_devices_with_position_now_and_pause_command():
    d = Devices()
    d.report("phone-0001", {"name": "Pixel", "track_id": "t1", "position": 30.0, "duration": 200.0, "playing": True}, now=100)
    d.report("web-00001", {"name": "Браузер", "track_id": "", "playing": False}, now=101)
    others = d.others("web-00001", now=110)
    assert [o["name"] for o in others] == ["Pixel"] and others[0]["position"] == 40.0  # играет — позиция на сейчас
    assert d.others("phone-0001", now=110) == []  # себя и устройства без трека не показываем

    assert d.command("phone-0001", "pause", by="Браузер") and not d.command("nobody-01", "pause")
    assert d.others("web-00001", now=111)[0]["playing"] is False  # другие сразу видят его на паузе
    assert d.report("phone-0001", {"name": "Pixel", "track_id": "t1", "playing": True}, now=112) == [{"action": "pause", "by": "Браузер"}]
    assert d.report("phone-0001", {"name": "Pixel", "track_id": "t1", "playing": False}, now=113) == []  # команда — один раз


def test_silent_devices_are_forgotten():
    d = Devices()
    d.report("phone-0001", {"track_id": "t1", "playing": True}, now=0)
    d.report("tablet-001", {"track_id": "t2", "playing": False}, now=0)
    assert {o["device_id"] for o in d.others("me-000001", now=PLAYING_TTL + 1)} == {"tablet-001"}
    assert d.others("me-000001", now=PAUSED_TTL + 1) == []


def test_devices_api(tmp_path):
    client = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "volna.db")))
    tracks = client.get("/api/library/recent").json()["tracks"][:3]
    phone = {"device_id": "phone-0001", "name": "Pixel 8", "kind": "android", "track_id": tracks[0]["id"], "position": 12,
             "duration": tracks[0]["duration"], "playing": True, "mode": "wave", "source": "Моя волна",
             "next_ids": [t["id"] for t in tracks[1:]]}
    assert client.put("/api/devices/me", json=phone).json() == {"commands": [], "devices": []}

    web = client.put("/api/devices/me", json={"device_id": "web-000001", "name": "Браузер", "kind": "web"}).json()
    [other] = web["devices"]
    assert other["name"] == "Pixel 8" and other["playing"] and other["mode"] == "wave"
    assert other["track"]["id"] == tracks[0]["id"] and [t["id"] for t in other["next"]] == [t["id"] for t in tracks[1:]]
    assert client.get("/api/devices?device_id=web-000001").json()["devices"][0]["device_id"] == "phone-0001"

    assert client.post("/api/devices/phone-0001/pause?by=Браузер").json() == {"ok": True}
    assert client.post("/api/devices/missing-01/pause").status_code == 404
    assert client.put("/api/devices/me", json=phone).json()["commands"] == [{"action": "pause", "by": "Браузер"}]
    assert client.put("/api/devices/me", json={"device_id": "x"}).status_code == 422
