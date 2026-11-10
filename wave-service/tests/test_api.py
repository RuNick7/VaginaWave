from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from volna.app import create_app
from volna.config import Settings


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(Settings(demo=True, db_path=tmp_path / "test.db")))


def test_one_button_flow(client):
    assert client.get("/api/health").json()["mode"] == "demo"

    state = client.post("/api/wave/start", json={}).json()
    track = state["track"]
    assert track and track["stream"].startswith("/api/stream/")
    assert len(state["upcoming"]) == 3
    sid = state["session"]

    state = client.post(f"/api/wave/{sid}/advance", json={"track_id": track["id"], "outcome": "skip", "position": 10}).json()
    assert state["track"]["id"] != track["id"]

    liked = client.post(f"/api/wave/{sid}/like", json={"track_id": state["track"]["id"]}).json()
    assert liked["track"]["liked"] is True

    debug = client.get(f"/api/wave/{sid}/debug").json()
    assert debug["skipped"] == 1


def test_settings_apply_on_the_fly_and_persist(client):
    state = client.post("/api/wave/start", json={}).json()
    sid, current = state["session"], state["track"]["id"]

    changed = client.post(
        f"/api/wave/{sid}/settings", json={"character": "discover", "temperature": 1.3, "mood": "calm", "bogus": 1}
    ).json()
    assert changed["track"]["id"] == current  # текущий трек не обрывается
    assert changed["settings"]["character"] == "discover"
    assert changed["settings"]["temperature"] == 1.3

    saved = client.get("/api/settings").json()
    assert saved["settings"]["mood"] == "calm"
    assert "forgotten" in saved["options"]["character"]

    clamped = client.put("/api/settings", json={"temperature": 9, "mood": "nope"}).json()["settings"]
    assert clamped["temperature"] == 1.5 and clamped["mood"] == "any"

    shaken = client.post(f"/api/wave/{sid}/shake").json()
    assert shaken["track"]["id"] == current and len(shaken["upcoming"]) == 3


def test_unknown_session_is_404(client):
    response = client.post("/api/wave/nope/advance", json={"track_id": "x", "outcome": "complete"})
    assert response.status_code == 404


def test_continue_and_seed_waves(client):
    seeds = [client.post("/api/wave/start", json={}).json()["track"]["id"]]
    state = client.post("/api/continue", json={"track_ids": seeds, "title": "Мой список"}).json()
    assert state["kind"] == "steer" and state["track"]["id"] not in seeds
    reasons = [t["reason"] for t in [state["track"], *state["upcoming"]]]
    assert any(r == "Похоже на «Мой список»" for r in reasons)  # часть — похожее на список
    assert any(not r.startswith("Похоже") for r in reasons)  # часть — ваш вкус

    by_playlist = client.post("/api/wave/start", json={"seed": {"type": "playlist", "id": "demo-pl-evening"}}).json()
    assert by_playlist["kind"] == "steer" and by_playlist["title"] == "Вечер"

    track = by_playlist["track"]
    by_track = client.post("/api/wave/start", json={"seed": {"type": "track", "id": track["id"]}}).json()
    assert by_track["title"] == track["title"] and by_track["track"]["id"] != track["id"]
    debug = client.get(f"/api/wave/{by_track['session']}/debug").json()
    assert debug["steer"]["kind"] == "track" and debug["steer"]["strength"] == 0.65

    by_artist = client.post("/api/wave/start", json={"seed": {"type": "artist", "id": track["artist_id"]}}).json()
    assert by_artist["track"] is not None


def test_mix_by_song_and_save_as_playlist(client):
    seed = client.get("/api/library/recent").json()["tracks"][0]
    mix = client.post("/api/mix", json={"seed": {"type": "track", "id": seed["id"]}, "size": 15}).json()
    ids = [t["id"] for t in mix["tracks"]]
    assert ids[0] == seed["id"] and len(ids) == 15 and len(set(ids)) == 15
    assert mix["title"] == f"Похоже на «{seed['title']}»"

    saved = client.post("/api/playlists", json={"name": mix["title"], "track_ids": ids}).json()
    assert saved["count"] == 15
    playlists = client.get("/api/library/playlists").json()["items"]
    assert any(p["id"] == saved["id"] and p["title"] == mix["title"] for p in playlists)
    assert len(client.get(f"/api/playlist/{saved['id']}").json()["tracks"]) == 15


def test_events_outside_wave_and_entity_likes_feed_taste(client):
    track = client.post("/api/wave/start", json={}).json()["track"]
    now = time.time()
    for n in range(3):  # три прослушивания в разное время: одинаковое событие в те же секунды — это повтор
        result = client.post(
            "/api/events",
            json={"track_id": track["id"], "outcome": "skip", "position": 5, "context_type": "album",
                  "context_id": track["album_id"], "at": now - 600 * (n + 1)},
        ).json()
        assert result["kind"] == "early_skip"
    assert client.post("/api/like", json={"type": "album", "id": track["album_id"]}).json()["ok"]

    taste = client.get("/api/taste").json()
    assert taste["signals"]["playlists"] == 2
    assert any(a["artist"] == track["artist"] for a in taste["skipped_artists"])
    assert taste["sources"]["manual"]["skip"] == 3


def test_stream_supports_range_and_static_ui(client):
    track = client.post("/api/wave/start", json={}).json()["track"]
    partial = client.get(track["stream"], headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206 and len(partial.content) == 100
    assert client.get(track["cover"]).headers["content-type"].startswith("image/svg")
    page = client.get("/")
    assert page.status_code == 200 and "Моя волна" in page.text
    assert client.get("/topo.js").status_code == 200


def test_queue_refills_in_background_and_cache_warms_up(client):
    import time

    state = client.post("/api/wave/start", json={}).json()
    sid = state["session"]
    session = client.app.state.runtime.main.sessions[sid]
    assert session.background_refill
    seen = {state["track"]["id"]}
    for _ in range(6):  # быстрые пропуски: ответ берётся из готовой очереди, дозаполнение — в фоне
        state = client.post(f"/api/wave/{sid}/advance", json={"track_id": state["track"]["id"], "outcome": "skip",
                                                                "position": 2}).json()
        assert state["track"] and state["track"]["id"] not in seen
        seen.add(state["track"]["id"])

    deadline = time.time() + 10
    while time.time() < deadline:
        server = client.get("/api/server").json()
        if not server["warm"]["running"] and server["warm"]["total"] and len(session.queue) >= session.LOW_WATER:
            break
        time.sleep(0.1)
    assert server["warm"]["done"] == server["warm"]["total"] == 640
    assert len(session.queue) >= session.LOW_WATER
