from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import volna.app as app_module
from volna.app import create_app
from volna.config import ServerConfig, Settings
from volna.demo import DemoWorld
from volna.navidrome import Navidrome, SubsonicError
from volna.services import Deps
from volna.store import Store


def make_app(tmp_path, auth=True):
    app = create_app(Settings(demo=True, db_path=tmp_path / "test.db", auth=auth))
    app.state.auth.verify = lambda user, password: password == "secret"
    return app


def signed_in(app, user="owner"):
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"user": user, "password": "secret"}).status_code == 200
    return client


def share_playlist(client):
    playlist = client.get("/api/library/playlists").json()["items"][0]
    return playlist["id"], client.post(f"/api/playlist/{playlist['id']}/share").json()


def test_guest_sees_the_list_but_listens_only_when_allowed(tmp_path):
    app = make_app(tmp_path)
    owner = signed_in(app)
    playlist_id, share = share_playlist(owner)
    assert share["url"].endswith(f"/s/{share['token']}")
    assert share["guests"] is False
    guest = TestClient(app)

    page = guest.get(f"/api/share/{share['token']}").json()
    assert page["signed_in"] is False and page["guests"] is False and page["count"] > 0
    track = page["tracks"][0]
    assert track["stream"] == ""
    assert not {"liked", "album_id", "artist_id"} & set(track)  # чужим — ничего лишнего
    assert "owner" not in page and "playlist_id" not in page
    assert guest.get(page["cover"]).status_code == 200
    assert guest.get(f"/api/share/{share['token']}/cover/not-in-playlist").status_code == 404
    assert guest.get(f"/api/share/{share['token']}/stream/{track['id']}").status_code == 403

    allowed = owner.put(f"/api/playlist/{playlist_id}/share", json={"guests": True}).json()
    assert allowed["guests"] is True and allowed["guests_until"] > time.time() + 29 * 86400
    page = guest.get(f"/api/share/{share['token']}").json()
    assert guest.get(page["tracks"][0]["stream"]).status_code == 200

    # остальное сервис по-прежнему закрывает, чужие треки по ссылке не отдаёт
    other = next(t["id"] for t in owner.get("/api/library/liked").json()["tracks"] if t["id"] not in {x["id"] for x in page["tracks"]})
    assert guest.get(f"/api/share/{share['token']}/stream/{other}").status_code == 404
    assert guest.get(f"/api/stream/{track['id']}").status_code == 401
    assert guest.get("/api/home").status_code == 401
    assert guest.post(f"/api/share/{share['token']}/save").status_code == 401

    owner.put(f"/api/playlist/{playlist_id}/share", json={"guests": False})
    assert guest.get(f"/api/share/{share['token']}/stream/{track['id']}").status_code == 403


def test_guest_access_ends_by_itself(tmp_path):
    app = make_app(tmp_path)
    owner = signed_in(app)
    playlist_id, share = share_playlist(owner)
    owner.put(f"/api/playlist/{playlist_id}/share", json={"guests": True})
    assert app.state.shares.get(share["token"]).guests

    store = app.state.shares.store
    data = store.setting("shares")
    data[share["token"]]["guests_until"] = time.time() - 1
    store.put_setting("shares", data)

    guest = TestClient(app)
    page = guest.get(f"/api/share/{share['token']}").json()
    assert page["guests"] is False and page["tracks"][0]["stream"] == ""
    assert guest.get(f"/api/share/{share['token']}/stream/{page['tracks'][0]['id']}").status_code == 403
    # а сама ссылка для людей с аккаунтом работает дальше
    assert owner.post(f"/api/playlist/{playlist_id}/share").json()["guests"] is False


def test_revoked_link_stops_working_and_new_one_differs(tmp_path):
    app = make_app(tmp_path)
    owner = signed_in(app)
    playlist_id, share = share_playlist(owner)
    assert owner.post(f"/api/playlist/{playlist_id}/share").json()["token"] == share["token"]  # одна ссылка на плейлист

    assert owner.delete(f"/api/playlist/{playlist_id}/share").json() == {"shared": False}
    guest = TestClient(app)
    assert guest.get(f"/api/share/{share['token']}").status_code == 404
    assert guest.get(f"/s/{share['token']}").status_code == 404
    assert owner.post(f"/api/playlist/{playlist_id}/share").json()["token"] != share["token"]


def test_friend_listens_and_saves_a_copy(tmp_path):
    app = make_app(tmp_path)
    _, share = share_playlist(signed_in(app))
    friend = signed_in(app, "friend")

    page = friend.get(f"/api/share/{share['token']}").json()
    assert page["signed_in"] is True and page["own"] is False and page["owner"] == "owner"
    assert page["tracks"][0]["stream"].startswith("/api/stream/")
    assert "liked" in page["tracks"][0]

    assert signed_in(app).get(f"/api/share/{share['token']}").json()["own"] is True
    assert signed_in(app, "OWNER").get(f"/api/share/{share['token']}").json()["own"] is True


def test_saved_copy_belongs_to_the_friend(tmp_path, monkeypatch):
    def own_world(server, shared, data_dir):
        return Deps(library=DemoWorld(), sim=shared.sim, store=Store(data_dir / f"user-{server.navidrome_user}.db"),
                    vectors=shared.vectors, demo=True)

    monkeypatch.setattr(app_module, "user_deps", own_world)
    monkeypatch.setattr(Navidrome, "ping", lambda self: True)
    monkeypatch.setattr(Navidrome, "is_admin", lambda self: False)
    app = create_app(Settings(demo=True, db_path=tmp_path / "test.db", auth=True))
    app.state.runtime.server = ServerConfig("http://navidrome:4533", "owner", "pw")
    owner, friend = TestClient(app), TestClient(app)
    for client, user in ((owner, "owner"), (friend, "friend")):
        assert client.post("/api/auth/login", json={"user": user, "password": "x"}).status_code == 200

    tracks = [t["id"] for t in owner.get("/api/library/liked").json()["tracks"]][:3]
    created = owner.post("/api/playlists", json={"name": "Только для меня", "track_ids": tracks}).json()
    share = owner.post(f"/api/playlist/{created['id']}/share").json()

    # чужую ссылку нельзя ни поменять, ни отозвать: плейлист для него не существует
    assert friend.put(f"/api/playlist/{created['id']}/share", json={"guests": True}).status_code == 404
    assert friend.delete(f"/api/playlist/{created['id']}/share").status_code == 404
    assert friend.post(f"/api/playlist/{created['id']}/share").status_code == 404

    page = friend.get(f"/api/share/{share['token']}").json()
    assert page["saved_id"] is None
    saved = friend.post(f"/api/share/{share['token']}/save").json()
    assert saved["count"] == 3
    assert friend.get(f"/api/playlist/{saved['id']}").json()["title"] == "Только для меня"
    assert friend.get(f"/api/share/{share['token']}").json()["saved_id"] == saved["id"]


def test_link_shows_current_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "SHARE_FRESH", 0)
    app = make_app(tmp_path)
    owner = signed_in(app)
    playlist_id, share = share_playlist(owner)
    world = app.state.runtime.deps.library
    playlist = next(p for p in world.playlists if p.id == playlist_id)
    guest = TestClient(app)

    playlist.track_ids = playlist.track_ids[:1]
    assert guest.get(f"/api/share/{share['token']}").json()["count"] == 1

    world.playlists.remove(playlist)
    assert guest.get(f"/api/share/{share['token']}").status_code == 404


def test_landing_page_for_guests_previews_and_accounts(tmp_path):
    app = make_app(tmp_path)
    owner = signed_in(app)
    tracks = [t["id"] for t in owner.get("/api/library/liked").json()["tracks"]][:2]
    created = owner.post("/api/playlists", json={"name": '<b>"Ночь" & день</b>', "track_ids": tracks}).json()
    share = owner.post(f"/api/playlist/{created['id']}/share").json()
    token = share["token"]

    behind_caddy = {"x-forwarded-proto": "https", "x-forwarded-host": "music.example", "x-forwarded-prefix": "/volna"}
    guest = TestClient(app)
    page = guest.get(f"/s/{token}", headers=behind_caddy)
    assert page.status_code == 200
    assert "&lt;b&gt;&quot;Ночь&quot; &amp; день&lt;/b&gt;" in page.text and "<b>" not in page.text
    assert "Плейлист · 2 трека" in page.text
    assert f'content="https://music.example/volna/api/share/{token}/cover/' in page.text
    assert page.headers["x-robots-tag"] == "noindex"
    assert owner.post(f"/api/playlist/{created['id']}/share", headers=behind_caddy).json()["url"] == f"https://music.example/volna/s/{token}"

    redirect = owner.get(f"/s/{token}", follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"] == f"../#/shared/{token}"

    missing = guest.get("/s/no-such-link")
    assert missing.status_code == 404 and "Ссылка больше не работает" in missing.text


def test_home_service_without_login_goes_straight_to_the_site(tmp_path):
    app = make_app(tmp_path, auth=False)
    client = TestClient(app)
    _, share = share_playlist(client)
    assert client.get(f"/s/{share['token']}", follow_redirects=False).status_code == 302
    assert client.get(f"/api/share/{share['token']}").json()["own"] is True


def test_wave_by_link(tmp_path):
    app = make_app(tmp_path)
    _, share = share_playlist(signed_in(app))
    friend = signed_in(app, "friend")
    response = friend.post("/api/wave/start", json={"seed": {"type": "share", "id": share["token"]}})
    assert response.status_code == 200
    assert response.json()["track"]["id"]
    assert friend.post("/api/wave/start", json={"seed": {"type": "share", "id": "no-such"}}).status_code == 404


def test_navidrome_playlist_and_long_copy(tmp_path, monkeypatch):
    client = Navidrome("http://navidrome:4533", "user", "pw", Store(":memory:"))
    calls = []

    def call(endpoint, **params):
        calls.append((endpoint, params))
        if endpoint == "getPlaylist":
            if params["id"] == "gone":
                raise SubsonicError("not found", 70)
            if params["id"] == "denied":
                raise SubsonicError("wrong password", 40)
            return {"playlist": {"id": params["id"], "name": "Список", "entry": [{"id": 1}, {"id": 2}]}}
        return {"playlist": {"id": "new", "name": params.get("name")}}

    monkeypatch.setattr(client, "call", call)
    assert client.playlist("p").track_ids == ["1", "2"]
    assert client.playlist("gone") is None
    with pytest.raises(SubsonicError):
        client.playlist("denied")

    calls.clear()
    ids = [str(i) for i in range(client.PAGE * 2 + 1)]
    created = client.create_playlist("Большой", ids)
    assert created.track_ids == ids
    assert [c[0] for c in calls] == ["createPlaylist", "updatePlaylist", "updatePlaylist"]
    assert calls[0][1]["songId"] + calls[1][1]["songIdToAdd"] + calls[2][1]["songIdToAdd"] == ids
