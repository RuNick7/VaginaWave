from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

import volna.app as app_module
from volna.app import create_app
from volna.config import Settings, normalize_url
from volna.demo import DemoWorld
from volna.services import Deps, NoSimilarity, Vectors
from volna.store import Store


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(Settings(demo=True, db_path=tmp_path / "volna.db")))


def test_home_shelves_and_collection(client):
    shelves = {s["id"]: s for s in client.get("/api/home").json()["shelves"]}
    assert {"playlists", "recent", "forgotten", "newest", "frequent", "artists"} <= set(shelves)
    assert shelves["playlists"]["items"][0]["type"] == "playlist"

    stats = client.get("/api/library").json()
    assert stats["tracks"] > 500 and stats["playlists"] == 2 and stats["liked"] == 24

    newest = client.get("/api/library/albums?sort=newest&limit=5").json()["items"]
    assert len(newest) == 5 and all(a["type"] == "album" for a in newest)
    assert client.get("/api/library/albums?sort=liked").json()["items"][0]["liked"] is True
    assert client.get("/api/library/artists?sort=frequent&limit=3").json()["items"]
    assert len(client.get("/api/library/liked").json()["tracks"]) == 24
    assert client.get("/api/library/recent").json()["tracks"][0]["stream"].startswith("/api/stream/")


def test_entity_pages_and_search(client):
    album = client.get("/api/album/demo-album-3").json()
    assert [t["track_no"] for t in album["tracks"]] == sorted(t["track_no"] for t in album["tracks"])
    artist = client.get(f"/api/artist/{album['artist_id']}").json()
    assert artist["albums"][0]["id"] == "demo-album-3" and artist["top"]
    playlist = client.get("/api/playlist/demo-pl-evening").json()
    assert playlist["title"] == "Вечер" and len(playlist["tracks"]) == 18
    assert client.get("/api/album/nope").status_code == 404

    found = client.get("/api/search", params={"q": artist["title"].split()[0].upper()}).json()
    assert any(a["id"] == artist["id"] for a in found["artists"])
    assert client.get("/api/search?q=").json()["tracks"] == []


def test_like_album_is_visible_immediately(client):
    album = client.get("/api/library/albums?limit=1").json()["items"][0]
    assert client.post("/api/like", json={"type": "album", "id": album["id"], "liked": True}).json()["liked"]
    assert client.get(f"/api/album/{album['id']}").json()["liked"] is True


def test_preferences_validate(client):
    assert client.get("/api/preferences").json() == {"quality": "original", "continue_after_end": True}
    prefs = client.put("/api/preferences", json={"quality": "192", "continue_after_end": False}).json()
    assert prefs == {"quality": "192", "continue_after_end": False}
    assert client.put("/api/preferences", json={"quality": "999"}).json()["quality"] == "192"


def test_connect_and_forget_server_without_leaking_secrets(tmp_path, monkeypatch):
    ok = {"navidrome": {"ok": True, "type": "navidrome", "version": "0.60"}, "audiomuse": {"ok": True}}
    monkeypatch.setattr(app_module, "check_server", lambda server: ok)
    connected = []

    def fake_build_deps(server, data_dir):
        connected.append(server)
        world = DemoWorld()
        store = Store(data_dir / "fake.db")
        return Deps(library=world, sim=world, store=store, vectors=Vectors(world, store), demo=server is None)

    monkeypatch.setattr(app_module, "build_deps", fake_build_deps)
    client = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "volna.db")))

    form = {"navidrome_url": "music.example.com", "navidrome_user": "me", "navidrome_password": "secret-1",
            "audiomuse_url": "192.168.1.50:8000", "audiomuse_token": "tok-xyz-123"}
    saved = client.put("/api/server", json=form).json()
    assert saved["mode"] == "live"
    assert saved["server"]["navidrome_url"] == "https://music.example.com"
    assert saved["server"]["audiomuse_url"] == "http://192.168.1.50:8000"
    assert "secret-1" not in str(saved) and "tok-xyz-123" not in str(client.get("/api/server").json())

    # пустой пароль при том же логине означает «оставить сохранённый»
    client.put("/api/server", json={**form, "navidrome_password": "", "audiomuse_token": ""})
    assert connected[-1].navidrome_password == "secret-1" and connected[-1].audiomuse_token == "tok-xyz-123"

    # после перезапуска сервис поднимается с сохранённым сервером
    restarted = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "volna.db")))
    assert restarted.get("/api/server").json()["source"] == "saved"

    assert client.delete("/api/server").json()["mode"] == "demo"


def test_library_works_without_audiomuse(tmp_path):
    world = DemoWorld()
    store = Store(tmp_path / "s.db")
    sim = NoSimilarity()
    deps = Deps(library=world, sim=sim, store=store, vectors=Vectors(sim, store))
    client = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "volna.db"), deps=deps))

    assert client.get("/api/home").status_code == 200
    response = client.post("/api/wave/start", json={})
    assert response.status_code == 503 and "AudioMuse" in response.json()["detail"]
    track = client.get("/api/library/recent").json()["tracks"][0]
    now = time.time()
    kind = client.post("/api/events", json={"track_id": track["id"], "outcome": "complete",
                                            "duration": track["duration"], "context_type": "album", "at": now - 1800}).json()["kind"]
    assert kind == "complete" and store.count_events() == 1

    # прослушивание без сети приходит позже, со временем, когда слушали; слишком старое — 30 дней назад,
    # из будущего — сейчас; повтор того же события — не новое прослушивание
    for at in (now - 3600, now - 90 * 86400, now + 3600, now + 3600):
        client.post("/api/events", json={"track_id": track["id"], "outcome": "complete", "duration": track["duration"], "at": at})
    oldest, hour_ago, half_hour, *today = sorted(e.ts for e in store.events())
    assert abs(oldest - (now - 30 * 86400)) < 60 and abs(hour_ago - (now - 3600)) < 1 and abs(half_hour - (now - 1800)) < 1
    assert len(today) == 1 and now - 60 < today[0] <= time.time()


def test_normalize_url():
    assert normalize_url(" music.example.com/ ") == "https://music.example.com"
    assert normalize_url("192.168.1.5:4533") == "http://192.168.1.5:4533"
    assert normalize_url("http://nas.local:4533") == "http://nas.local:4533"


def test_lyrics_and_full_artist_tracks(client):
    track = client.post("/api/wave/start", json={}).json()["track"]
    lyrics = client.get(f"/api/lyrics/{track['id']}").json()
    assert set(lyrics) == {"synced", "lines"}

    artist = client.get(f"/api/artist/{track['artist_id']}").json()
    assert len(artist["tracks"]) >= len(artist["top"]) and len(artist["top"]) <= 10


def test_navidrome_lyrics_prefer_synced(monkeypatch):
    from volna.navidrome import Navidrome
    from volna.store import Store

    structured = {"lyricsList": {"structuredLyrics": [
        {"synced": False, "line": [{"value": "plain"}]},
        {"synced": True, "offset": 500, "line": [{"start": 1500, "value": "first"}, {"start": 4000, "value": "second"}]},
    ]}}
    nd = Navidrome("http://nd", "u", "p", Store(":memory:"))
    monkeypatch.setattr(nd, "call", lambda endpoint, **params: structured)
    assert nd.lyrics("t1") == {"synced": True, "lines": [{"time": 1.0, "text": "first"}, {"time": 3.5, "text": "second"}]}

    plain = {"getLyricsBySongId": {"lyricsList": {}}, "getLyrics": {"lyrics": {"value": "a\nb"}}}
    monkeypatch.setattr(nd, "call", lambda endpoint, **params: plain[endpoint])
    assert nd.lyrics("t1", "Artist", "Song")["lines"] == [{"time": None, "text": "a"}, {"time": None, "text": "b"}]


def test_queue_plays_are_recorded(client):
    track = client.get("/api/library/recent").json()["tracks"][0]
    response = client.post("/api/events", json={"track_id": track["id"], "outcome": "complete",
                                                 "duration": track["duration"], "context_type": "queue"})
    assert response.status_code == 200 and response.json()["kind"] == "complete"


def test_onboarding_picks_artists_suggests_similar_and_saves_preferences(client):
    first = client.get("/api/onboarding").json()
    assert first["done"] is False and len(first["artists"]) > 10
    picked = [a for a in first["artists"] if not a["liked"]][:3]

    similar = client.get(f"/api/onboarding/similar/{picked[0]['id']}").json()["artists"]
    assert similar and picked[0]["id"] not in {a["id"] for a in similar}

    done = client.post("/api/onboarding", json={"artists": [a["id"] for a in picked],
                                                "settings": {"character": "discover", "no_vocals": True}}).json()
    assert done["liked"] == 3 and done["settings"]["character"] == "discover"
    again = client.get("/api/onboarding").json()
    assert again["done"] is True and again["settings"]["no_vocals"] is True
    liked = {a["id"] for a in again["artists"] if a["liked"]}
    assert {a["id"] for a in picked} <= liked  # отмеченные — первыми и со звездой


def test_like_all_tracks_of_a_playlist(client):
    album = client.get("/api/library/albums?sort=alpha&limit=1").json()["items"][0]
    tracks = client.get(f"/api/album/{album['id']}").json()["tracks"]
    ids = [t["id"] for t in tracks]
    done = client.post("/api/like/tracks", json={"track_ids": ids + ids[:1] + ["no-such-track"]}).json()
    assert done == {"ok": True, "count": len(ids), "liked": True}  # повторы и чужие id отброшены
    assert all(t["liked"] for t in client.get(f"/api/album/{album['id']}").json()["tracks"])
    liked = {t["id"] for t in client.get("/api/library/liked").json()["tracks"]}
    assert set(ids) <= liked


def test_offline_reserve_prefers_often_and_recently_played(client):
    reserve = client.get("/api/library/reserve?limit=20").json()["tracks"]
    assert 0 < len(reserve) <= 20 and len({t["id"] for t in reserve}) == len(reserve)
    recent = client.get("/api/library/recent").json()["tracks"]
    assert {t["id"] for t in recent[:5]} & {t["id"] for t in reserve}  # недавно слушанное — в запасе
    assert client.get("/api/library/reserve?limit=0").json()["tracks"] == []
