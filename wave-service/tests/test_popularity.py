from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from volna.app import create_app
from volna.config import Settings
from volna.models import Track
from volna.popularity import TopTracks, rank_tracks


def lastfm(tracks: list[tuple[str, int]], calls: list | None = None, corrected: dict[str, tuple[str, list]] | None = None):
    """Last.fm: точное имя знает с tracks; с автоисправлением отдаёт corrected[имя] = (исправленное, треки)."""
    def handler(request: httpx.Request) -> httpx.Response:
        artist = request.url.params["artist"]
        if calls is not None:
            calls.append(artist)
        if request.url.params["autocorrect"] == "1" and corrected and artist in corrected:
            name, items = corrected[artist]
        elif corrected and artist in corrected:
            return httpx.Response(200, json={"error": 6, "message": "The artist you supplied could not be found"})
        else:
            name, items = artist, tracks
        return httpx.Response(200, json={"toptracks": {"track": [{"name": n, "listeners": str(l)} for n, l in items],
                                                       "@attr": {"artist": name}}})
    return httpx.MockTransport(handler)


def test_autocorrect_is_used_only_for_the_same_name():
    top = TopTracks("key", transport=lastfm([], corrected={"Bowie David": ("David Bowie", [("Heroes", 5)]),
                                                         "Ye": ("Yes", [("Roundabout", 9)])}))
    assert top.top("Bowie David") == [("Heroes", 5)]
    assert top.top("Ye") == []  # не группа Yes


def test_redirect_to_another_artist_is_ignored_and_other_spelling_tried():
    """Last.fm уводит «MORGENSHTERN» на Morgenstern, а «Morgenshtern» отвечает правильно."""
    def handler(request: httpx.Request) -> httpx.Response:
        artist = request.url.params["artist"]
        name, items = ("Morgenstern", [("Asche", 1)]) if artist == "MORGENSHTERN" else ("Morgenshtern", [("Cadillac", 2)])
        return httpx.Response(200, json={"toptracks": {"track": [{"name": n, "listeners": str(l)} for n, l in items],
                                                       "@attr": {"artist": name}}})
    assert TopTracks("key", transport=httpx.MockTransport(handler)).top("MORGENSHTERN") == [("Cadillac", 2)]


def test_world_hits_come_first_with_transliteration_and_one_version_per_song():
    top = [("Oschuschenya", 42370), ("Искала", 34238), ("Numb", 3174134), ("Part 2", 10), ("My Way", 9),
           ("Чистый", 8), ("Numb - Live in Texas", 7)]
    tracks = [
        Track("a", "Ромашки", play_count=50),
        Track("b", "Ощущения", play_count=1),
        Track("c", "Искала (feat. Кто-то)"),
        Track("d", "Numb (Live)", play_count=9),
        Track("e", "Numb"),
        Track("f", "Part 1"),
        Track("g", "My Way - 2008 Remastered"),
        Track("h", "My Way"),
        Track("i", "Чистый (Remix)"),  # ремикс не занимает место оригинала
    ]
    # «Part 1» — не «Part 2»; live — на своём месте, ниже студийной записи
    assert rank_tracks(top, tracks) == {"b": 0, "c": 1, "e": 2, "h": 4, "d": 6}


def test_top_is_cached_and_failures_do_not_break_the_page():
    calls: list = []
    top = TopTracks("key", transport=lastfm([("Numb", 1)], calls))
    assert top.top("Linkin Park") == [("Numb", 1)]
    assert top.top("linkin park") == [("Numb", 1)] and calls == ["Linkin Park"]

    broken = TopTracks("key", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert broken.top("Linkin Park") == [] and broken.ranking("Linkin Park", [Track("x", "Numb")]) == {}


def test_artist_page_orders_tracks_by_world_popularity(tmp_path):
    client = TestClient(create_app(Settings(demo=True, db_path=tmp_path / "test.db")))
    artist = client.get("/api/library/artists?limit=1").json()["items"][0]
    before = client.get(f"/api/artist/{artist['id']}").json()["tracks"]
    assert len(before) >= 2

    hit = before[-1]["title"]  # самый непопулярный здесь — хит в мире, хоть и не в первой сотне
    filler = [(f"Песня, которой нет в библиотеке {i}", 10) for i in range(200)]
    client.app.state.top_tracks = TopTracks("key", transport=lastfm(filler + [(hit, 5)]))
    after = client.get(f"/api/artist/{artist['id']}").json()
    assert after["tracks"][0]["title"] == hit and after["top"][0]["title"] == hit
    assert [t["id"] for t in after["tracks"][1:]] == [t["id"] for t in before if t["title"] != hit]
