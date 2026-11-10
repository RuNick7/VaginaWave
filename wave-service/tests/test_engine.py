from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate import LISTENER, continuation_drift, listen, make_deps  # noqa: E402

from volna.demo import DISLIKED_GENRE, LIKED_GENRES  # noqa: E402
from volna.engine import Steer, WaveSession, classify, register_playback  # noqa: E402
from volna.profile import build_context_profile, build_profile  # noqa: E402
from volna.settings import WaveSettings, track_traits  # noqa: E402


@pytest.fixture()
def world_deps():
    return make_deps()


def test_classify_uses_position_and_fraction():
    assert classify("skip", 10, 200) == "early_skip"
    assert classify("skip", 40, 400) == "early_skip"  # 10 % трека
    assert classify("skip", 120, 200) == "late_skip"
    assert classify("skip", 170, 200) == "complete"  # 85 % — считаем дослушанным
    assert classify("complete", 0, 0) == "complete"
    assert classify("error", 5, 200) == "error"


def test_profile_reflects_history(world_deps):
    world, deps = world_deps
    profile = build_profile(deps)
    assert not profile.cold_start
    top = sorted(profile.clusters, key=lambda c: -c.share)[:3]
    genres = {world.genre[m] for c in top for m in c.members[:5]}
    assert genres <= set(LIKED_GENRES) | {-1}
    assert DISLIKED_GENRE not in genres


def test_tracks_rated_one_star_in_navidrome_never_play(world_deps):
    # Subsonic не умеет искать по оценке, поэтому 1★ отсеивается при загрузке кандидатов
    world, deps = world_deps
    profile = build_profile(deps)
    banned_artist = world.tracks[profile.clusters[0].members[0]].artist
    for t in world.tracks.values():
        if t.artist == banned_artist:
            t.rating = 1
    session = WaveSession(deps, profile, seed=5)
    played = [session.start()]
    for _ in range(30):
        played.append(session.advance(played[-1].id, "complete", played[-1].duration, played[-1].duration))
    assert banned_artist not in {t.artist for t in played}


def test_early_skip_purges_similar_and_weakens_cluster(world_deps):
    _, deps = world_deps
    session = WaveSession(deps, build_profile(deps), seed=0)
    track = session.start()
    cluster = session.picks[track.id].cluster
    before = session.mult[cluster]
    session.advance(track.id, "skip", position=5, duration=track.duration)
    assert session.mult[cluster] < before
    assert all(t.artist != track.artist for t in [session.current, *session.queue])
    assert len(session.queue) >= session.LOW_WATER


def test_two_skips_in_a_row_switch_cluster(world_deps):
    _, deps = world_deps
    session = WaveSession(deps, build_profile(deps), seed=1)
    track = session.start()
    first = session.picks[track.id].cluster
    for _ in range(2):
        cluster = session.picks[track.id].cluster
        track = session.advance(track.id, "skip", position=10, duration=track.duration)
    assert session.picks[track.id].cluster != cluster or cluster != first


def test_dislike_is_synced_and_never_repeats(world_deps):
    world, deps = world_deps
    session = WaveSession(deps, build_profile(deps), seed=2)
    track = session.start()
    session.advance(track.id, "dislike", position=8, duration=track.duration)
    assert world.tracks[track.id].rating == 1
    assert track.id in session.taste.disliked
    fresh = WaveSession(deps, build_profile(deps), seed=3)
    seen = [fresh.start()]
    for _ in range(20):
        seen.append(fresh.advance(seen[-1].id, "complete", seen[-1].duration, seen[-1].duration))
    assert track.id not in {t.id for t in seen}


def test_skips_persist_into_next_profile(world_deps):
    _, deps = world_deps
    session = WaveSession(deps, build_profile(deps), seed=4)
    track = session.start()
    session.advance(track.id, "skip", position=2, duration=track.duration)
    kinds = [e.kind for e in deps.store.events()]
    assert kinds == ["early_skip"]
    assert build_profile(deps).track_skips[track.id] > 0


def test_wave_drops_genre_the_listener_now_skips():
    turned = dict(LISTENER)
    turned[1] = 0.05  # раньше любил Synthwave, теперь пропускает
    early, late = [], []
    for seed in range(6):
        world, deps = make_deps()
        log = listen(world, WaveSession(deps, build_profile(deps), seed=seed), 30, np.random.default_rng(seed), turned)
        genres = [g for g, _ in log]
        early.append(np.mean([g == 1 for g in genres[:10]]))
        late.append(np.mean([g == 1 for g in genres[10:]]))
    assert np.mean(late) < np.mean(early)
    assert np.mean(late) <= 0.08


def test_continuation_keeps_whole_playlist_not_last_song():
    results = [continuation_drift(seed=s) for s in range(4)]
    ours = np.mean([r["volna"]["balance"] for r in results])
    naive = np.mean([r["last_song_radio"]["balance"] for r in results])
    assert np.mean([r["volna"]["in_playlist"] for r in results]) >= 0.9
    assert ours >= 0.4  # обе части плейлиста звучат, хотя часть треков — из вашего вкуса
    assert ours > naive + 0.3


def _steered(genre, strength, seeds=range(3), n=20):
    """Доля жанра трека-источника и любимых жанров в волне по треку."""
    same, liked = [], []
    for seed in seeds:
        world, deps = make_deps()
        taste = build_profile(deps)
        tid = [i for i in world.ids if world.genre[i] == genre][5 + seed]
        steer = Steer(build_context_profile(deps, [tid], taste), "track", tid, "трек")
        session = WaveSession(deps, taste, WaveSettings(steer=strength), steer=steer, exclude=[tid], seed=seed)
        track = session.start()
        for _ in range(n):
            same.append(world.genre[track.id] == genre)
            liked.append(world.genre[track.id] in LIKED_GENRES)
            track = session.advance(track.id, "complete", track.duration, track.duration)
    return np.mean(same), np.mean(liked)


def test_wave_by_track_shifts_weights_toward_the_track():
    # Неоклассика (7) не во вкусе слушателя: обычная волна её не ставит, волна по треку — ставит,
    # и чем сильнее смещение, тем больше похожего на трек и меньше «своего».
    weak, weak_liked = _steered(7, 0.35)
    default, _ = _steered(7, 0.65)
    strong, strong_liked = _steered(7, 0.9)
    assert weak < default < strong
    assert strong >= 0.8 and weak_liked >= 0.25 and strong_liked < weak_liked


def test_skipping_track_like_songs_weakens_the_shift(world_deps):
    world, deps = world_deps
    taste = build_profile(deps)
    tid = [i for i in world.ids if world.genre[i] == 7][3]
    steer = Steer(build_context_profile(deps, [tid], taste), "track", tid, "трек")
    session = WaveSession(deps, taste, WaveSettings(steer=0.8), steer=steer, exclude=[tid], seed=1)
    track = session.start()
    for _ in range(8):
        pick = session.picks[track.id]
        outcome = "skip" if pick.origin == "steer" else "complete"
        track = session.advance(track.id, outcome, position=10 if outcome == "skip" else track.duration,
                                duration=track.duration)
    assert session.steer_strength < 0.8 * 0.7


def test_similar_only_wave_never_falls_back_to_taste(world_deps):
    world, deps = world_deps
    taste = build_profile(deps)
    tid = next(i for i in world.ids if world.genre[i] == 7)
    steer = Steer(build_context_profile(deps, [tid], taste), "track", tid, "трек")
    session = WaveSession(deps, taste, WaveSettings(similar_only=True), steer=steer, exclude=[tid], seed=1)
    track = session.start()
    origins = []
    for i in range(12):
        origins.append(session.picks[track.id].origin)
        outcome = "skip" if i % 2 else "complete"  # пропуски не возвращают в волну «своё»
        track = session.advance(track.id, outcome, position=4 if outcome == "skip" else track.duration,
                                duration=track.duration)
    assert set(origins) == {"steer"} and session.steer_strength == 1.0


# --- настройки и сигналы ---

def _serve(settings, seeds=(0, 1), n=20, prepare=None):
    """Прогоняет волну с настройками; возвращает (мир, профиль, сессия, выданные треки) последнего прогона и все id."""
    served = []
    for seed in seeds:
        world, deps = make_deps()
        if prepare:
            prepare(world)
        session = WaveSession(deps, build_profile(deps), settings, seed=seed)
        track = session.start()
        for _ in range(n):
            served.append((world, track))
            track = session.advance(track.id, "complete", track.duration, track.duration)
    return served


def _mean_trait(served, key):
    return np.mean([track_traits(world.features_map[t.id]).get(key, 0.5) for world, t in served])


def test_mood_and_activity_shift_the_sound():
    calm = _mean_trait(_serve(WaveSettings(mood="calm")), "energy")
    energetic = _mean_trait(_serve(WaveSettings(mood="energetic")), "energy")
    workout = _mean_trait(_serve(WaveSettings(activity="workout")), "energy")
    assert calm < energetic - 0.25
    assert workout > calm + 0.25


def test_no_vocals_prefers_instrumental():
    base = _mean_trait(_serve(WaveSettings()), "instrumental")
    assert _mean_trait(_serve(WaveSettings(no_vocals=True)), "instrumental") > base + 0.08


def test_character_controls_novelty():
    def new_share(character):
        served = _serve(WaveSettings(character=character))
        return np.mean([not (t.play_count > 0 or t.starred) for _, t in served])

    assert new_share("discover") > new_share("favorite") + 0.3


def test_artists_and_albums_rated_one_star_are_banned():
    banned = {}

    def prepare(world):
        # любимые жанры: Synthwave (артист с 1★) и Пост-рок (альбом с 1★)
        artist_id = next(world.tracks[i].artist_id for i in world.ids if world.genre[i] == 1)
        album_id = next(world.tracks[i].album_id for i in world.ids if world.genre[i] == 0)
        world.set_rating(artist_id, 1)
        world.set_rating(album_id, 1)
        banned.update(artist=artist_id, album=album_id)

    served = _serve(WaveSettings(character="favorite"), n=30, prepare=prepare)
    assert all(t.artist_id != banned["artist"] and t.album_id != banned["album"] for _, t in served)


def test_album_artist_and_playlist_marks_feed_weights(world_deps):
    world, deps = world_deps
    profile = build_profile(deps)
    starred_album = next(a.id for a in world.albums.values() if a.starred)
    album_tracks = [i for i in world.ids if world.tracks[i].album_id == starred_album]
    assert min(profile.track_weight.get(i, 0) for i in album_tracks) >= 1.0
    assert all(profile.track_weight.get(i, 0) > 0 for p in world.playlists for i in p.track_ids)
    starred_artist = next(a for a in world.artists.values() if a.starred)
    assert profile.artist_affinity(starred_artist.name) > 0.2


def test_skips_inside_an_album_lower_its_affinity(world_deps):
    world, deps = world_deps
    profile = build_profile(deps)
    album_id = world.tracks[world.ids[0]].album_id
    for track_id in [i for i in world.ids if world.tracks[i].album_id == album_id][:3]:
        register_playback(deps, profile, track_id, "early_skip", position=10, duration=60,
                          context_type="album", context_id=album_id)
    assert build_profile(deps).album_affinity(album_id) < 0
