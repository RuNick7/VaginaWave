"""Как волна понимает поведение: пролистывание, серии пропусков, брошенные сессии, лайки пачкой."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate import make_deps  # noqa: E402

from volna.engine import WarmStart, WaveSession, redundant, register_playback, warm_start  # noqa: E402
from volna.profile import BULK_LIKES, build_profile, bulk_likes  # noqa: E402
from volna.store import Event  # noqa: E402


def test_zapping_is_not_a_verdict_on_the_song_or_artist():
    world, deps = make_deps()
    profile = build_profile(deps)
    session = WaveSession(deps, profile, seed=2)
    track = session.start()
    cluster = session.picks[track.id].cluster
    before = session.mult[cluster]
    artist_before = profile.artist_affinity(track.artist)
    session.advance(track.id, "skip", position=1, duration=track.duration)
    assert 0.6 * before < session.mult[cluster] < before  # «не сейчас» мягче раннего пропуска (×0.6)
    assert profile.artist_affinity(track.artist) == artist_before
    assert build_profile(deps).track_weight.get(track.id, 0) > -0.5  # обычный ранний пропуск — −1.5


def test_a_run_of_zaps_turns_back_to_what_was_completed():
    world, deps = make_deps()
    session = WaveSession(deps, build_profile(deps), seed=3)
    track = session.start()
    for _ in range(3):  # что-то дослушали
        track = session.advance(track.id, "complete", track.duration, track.duration)
    liked = {session.profile.nearest(v) for v in session._vecs(session.completed).values()}
    for _ in range(2):  # два пролистывания подряд — уже серия
        track = session.advance(track.id, "skip", position=1, duration=track.duration)
    upcoming = [track, *session.queue[:3]]
    assert all(session.picks[t.id].cluster in liked and not session.picks[t.id].explore for t in upcoming)


def test_two_real_skips_return_to_comfort_instead_of_exploring():
    world, deps = make_deps()
    session = WaveSession(deps, build_profile(deps), seed=4)
    track = session.start()
    for _ in range(2):
        track = session.advance(track.id, "complete", track.duration, track.duration)
    for _ in range(2):
        track = session.advance(track.id, "skip", position=12, duration=track.duration)
    liked = {session.profile.nearest(v) for v in session._vecs(session.completed).values()}
    upcoming = [track, *session.queue[:3]]
    assert all(not session.picks[t.id].explore for t in upcoming)
    assert session.picks[track.id].cluster in liked


def _ev(ts, kind, track, session="", context="wave", position=0.0):
    return Event(ts=ts, track_id=track, kind=kind, session=session, context_type=context, position=position)


def test_abandoned_wave_is_detected_and_next_one_starts_from_comfort():
    now = time.time()
    events = [
        _ev(now - 5000, "complete", "good1", "s1"),
        _ev(now - 4000, "complete", "good2", "s1"),
        _ev(now - 3000, "early_skip", "bad1", "s1", position=12),
        _ev(now - 2990, "early_skip", "bad2", "s1", position=1),
    ]
    warm = warm_start(events, now)
    assert warm.abandoned == ["bad1", "bad2"] and warm.comfort[:2] == ["good2", "good1"]
    # дослушали последний трек — волну не бросали, просто закрыли
    assert warm_start(events + [_ev(now - 2900, "complete", "good3", "s1")], now).abandoned == []
    # давно — не в счёт
    assert warm_start(events, now + 4 * 86400).abandoned == []

    world, deps = make_deps()
    profile = build_profile(deps)
    bad = profile.clusters[0].members[:2]
    good = profile.clusters[1].members[:4]
    session = WaveSession(deps, profile, seed=1, warm=WarmStart(comfort=good, abandoned=bad))
    first = session.start()
    assert session.mult[0] < 1.0
    assert session.picks[first.id].cluster == 1 and not session.picks[first.id].explore


def test_history_navigation_and_duplicates_are_ignored():
    now = time.time()
    events = [_ev(now - 300, "complete", "a"), _ev(now - 5, "early_skip", "b", position=10)]
    assert redundant(events, "a", "early_skip", 0.0, now)  # листает назад-вперёд по уже дослушанному
    assert not redundant(events, "a", "early_skip", 40.0, now)  # послушал и пропустил — это оценка
    assert redundant(events, "b", "early_skip", 10.0, now)  # то же событие повторно
    assert not redundant(events, "c", "early_skip", 1.0, now)


def test_like_all_on_a_big_playlist_is_weak_until_confirmed():
    world, deps = make_deps()
    now = time.time()
    bulk = world.ids[:BULK_LIKES + 5]
    for i, tid in enumerate(bulk):
        deps.store.add_event(Event(ts=now - 100 + i * 0.1, track_id=tid, kind="like"))
    deps.store.add_event(Event(ts=now - 50, track_id=world.ids[0], kind="complete"))  # этот дослушали
    deps.store.add_event(Event(ts=now - 3600, track_id=world.ids[-1], kind="like"))  # отдельный лайк
    found = bulk_likes(deps.store.events())
    assert world.ids[0] not in found and world.ids[1] in found and world.ids[-1] not in found
    profile = build_profile(deps)
    assert world.ids[1] in profile.bulk_liked
    session = WaveSession(deps, profile, seed=0)
    for c in profile.clusters:
        assert not set(session._favorites_pool(c)) & profile.bulk_liked


def test_api_ignores_history_navigation_skip(tmp_path):
    from fastapi.testclient import TestClient

    from volna.app import create_app
    from volna.config import Settings

    client = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "t.db")))
    tid = client.get("/api/library/artists?limit=1").json()["items"][0]["id"]
    track = client.get(f"/api/artist/{tid}").json()["tracks"][0]["id"]
    done = client.post("/api/events", json={"track_id": track, "outcome": "complete", "position": 200, "duration": 200}).json()
    assert done["kind"] == "complete"
    back = client.post("/api/events", json={"track_id": track, "outcome": "skip", "position": 0.5, "duration": 200,
                                            "context_type": "other"}).json()
    assert back["kind"] == "ignored"
    start = client.post("/api/wave/start", json={}).json()  # волна стартует и с историей
    assert start["track"]
