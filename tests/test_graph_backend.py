from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

import pytest
from clinstagram.backends.graph import GraphAPIError, GraphBackend


def test_analytics_profile_is_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        assert request.url.path == "/v21.0/42"
        return httpx.Response(
            200,
            json={
                "id": "42",
                "username": "testaccount",
                "name": "Test Account",
                "followers_count": 120,
                "follows_count": 45,
                "media_count": 9,
                "profile_picture_url": "https://example.com/pic.jpg",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GraphBackend(token="tok", login_type="ig", client=client)

    result = backend.analytics_profile()

    assert result["id"] == "42"
    assert result["full_name"] == "Test Account"
    assert result["followers_count"] == 120
    assert result["following_count"] == 45
    assert result["profile_picture_url"] == "https://example.com/pic.jpg"


def test_analytics_post_latest_resolves_latest_media_before_fetch():
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.url.path == "/v21.0/42/media":
            return httpx.Response(200, json={"data": [{"id": "media123"}]})
        if request.url.path == "/v21.0/media123":
            return httpx.Response(
                200,
                json={
                    "id": "media123",
                    "caption": "hello",
                    "shortcode": "ABC123",
                    "media_type": "IMAGE",
                    "timestamp": "2026-03-12T12:00:00+0000",
                    "like_count": 7,
                    "comments_count": 2,
                    "media_url": "https://example.com/photo.jpg",
                    "permalink": "https://instagram.com/p/ABC123/",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GraphBackend(token="tok", login_type="ig", client=client)

    result = backend.analytics_post("latest")

    assert requests == [
        ("GET", "/v21.0/me"),
        ("GET", "/v21.0/42/media"),
        ("GET", "/v21.0/media123"),
    ]
    assert result["id"] == "media123"
    assert result["code"] == "ABC123"
    assert result["comment_count"] == 2


def test_dm_send_media_marks_video_attachments_correctly():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.method == "POST" and request.url.path == "/v21.0/42/messages":
            body = parse_qs(request.content.decode())
            message = json.loads(body["message"][0])
            assert message["attachment"]["type"] == "video"
            assert message["attachment"]["payload"]["url"] == "https://example.com/clip.mp4"
            recipient = json.loads(body["recipient"][0])
            assert recipient["id"] == "123456"
            return httpx.Response(200, json={"message_id": "mid.1"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GraphBackend(token="tok", login_type="fb", client=client)

    result = backend.dm_send_media("123456", "https://example.com/clip.mp4")

    assert result["message_id"] == "mid.1"
    assert result["status"] == "sent"


def _publishing_backend(statuses):
    """A Graph backend whose container reports `statuses` in turn on each status poll."""
    calls: list[str] = []
    polls = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.method == "POST" and path == "/v21.0/42/media":
            return httpx.Response(200, json={"id": "c1"})
        if request.method == "GET" and path == "/v21.0/c1":
            calls.append("poll")
            return httpx.Response(200, json={"status_code": next(polls)})
        if request.method == "POST" and path == "/v21.0/42/media_publish":
            calls.append("publish")
            return httpx.Response(200, json={"id": "m1"})
        if request.method == "GET" and path == "/v21.0/m1":
            calls.append("links")
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/DQx1/",
                                             "shortcode": "DQx1", "id": "m1"})
        raise AssertionError(f"unexpected {request.method} {path}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GraphBackend(token="tok", login_type="ig", client=client)
    backend._sleep = lambda s: None
    return backend, calls


def test_reel_waits_for_container_to_finish_before_publishing():
    backend, calls = _publishing_backend(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    result = backend.post_reel("https://example.com/r.mp4", caption="hi")
    assert result == {"id": "m1", "status": "published",
                      "permalink": "https://www.instagram.com/reel/DQx1/", "code": "DQx1"}
    assert calls == ["poll", "poll", "poll", "publish", "links"]


def test_reel_is_shared_to_feed():
    from urllib.parse import parse_qs

    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.method == "POST" and path == "/v21.0/42/media":
            sent.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"id": "c1"})
        if request.method == "GET" and path == "/v21.0/c1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and path == "/v21.0/42/media_publish":
            return httpx.Response(200, json={"id": "m1"})
        raise AssertionError(f"unexpected {request.method} {path}")

    backend = GraphBackend(token="tok", login_type="ig", client=httpx.Client(transport=httpx.MockTransport(handler)))
    backend._sleep = lambda s: None
    backend.post_reel("https://example.com/r.mp4", caption="hi", audio="Original")
    assert sent["media_type"] == "REELS"
    assert sent["share_to_feed"] == "true"
    assert sent["audio_name"] == "Original"


def _publish_server(links):
    """Graph fake for every publish path. `links` answers GET /m1: a JSON body, an
    httpx.Response, or an exception to raise."""
    log: list[tuple[str, str, str]] = []
    containers = iter(range(1, 100))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        log.append((request.method, path, request.url.params.get("fields", "")))
        if path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.method == "POST" and path == "/v21.0/42/media":
            return httpx.Response(200, json={"id": f"c{next(containers)}"})
        if request.method == "GET" and path.startswith("/v21.0/c"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and path == "/v21.0/42/media_publish":
            return httpx.Response(200, json={"id": "m1"})
        if request.method == "GET" and path == "/v21.0/m1":
            if isinstance(links, Exception):
                raise links
            return links if isinstance(links, httpx.Response) else httpx.Response(200, json=links)
        raise AssertionError(f"unexpected {request.method} {path}")

    return handler, log


_PUBLISHERS = [
    ("post_photo", ("https://example.com/p.jpg",), "ig"),
    ("post_video", ("https://example.com/v.mp4",), "ig"),
    ("post_reel", ("https://example.com/r.mp4",), "ig"),
    ("post_carousel", (["https://example.com/a.jpg", "https://example.com/b.mp4"],), "ig"),
    ("story_post_photo", ("https://example.com/s.jpg",), "fb"),
    ("story_post_video", ("https://example.com/s.mp4",), "fb"),
]


def _publisher(handler, login_type):
    backend = GraphBackend(token="tok", login_type=login_type,
                           client=httpx.Client(transport=httpx.MockTransport(handler)),
                           ig_user_id="42" if login_type == "fb" else None)
    backend._sleep = lambda s: None
    return backend


@pytest.mark.parametrize("method,args,login_type", _PUBLISHERS)
def test_every_publish_returns_permalink_and_code(method, args, login_type):
    handler, log = _publish_server({"id": "m1", "permalink": "https://www.instagram.com/p/DQx1/",
                                    "shortcode": "DQx1"})
    result = getattr(_publisher(handler, login_type), method)(*args)
    assert result == {"id": "m1", "status": "published",
                      "permalink": "https://www.instagram.com/p/DQx1/", "code": "DQx1"}
    assert [entry for entry in log if entry[1] == "/v21.0/m1"] == [("GET", "/v21.0/m1", "permalink,shortcode")]


@pytest.mark.parametrize("method,args,login_type", _PUBLISHERS)
@pytest.mark.parametrize("links", [
    httpx.Response(400, json={"error": {"message": "(#10) Application does not have permission for this action",
                                        "type": "OAuthException", "code": 10}}),
    httpx.Response(500, text="upstream hiccup"),
    httpx.ConnectError("connection reset"),
])
def test_publish_survives_a_failed_permalink_lookup(method, args, login_type, links):
    handler, log = _publish_server(links)
    result = getattr(_publisher(handler, login_type), method)(*args)
    assert result["id"] == "m1"
    assert result["status"] == "published"
    assert "permalink" not in result and "code" not in result
    assert result["permalink_error"]
    assert sum(1 for entry in log if entry[1] == "/v21.0/42/media_publish") == 1


def test_cli_failed_permalink_lookup_never_reposts_on_private(monkeypatch, tmp_path):
    # A Meta permission error after publishing must not trigger the private-API fallback:
    # that would put the same post up twice.
    handler, log = _publish_server(httpx.Response(400, json={"error": {
        "message": "(#10) Application does not have permission for this action", "code": 10}}))
    run, private = _cli(monkeypatch, tmp_path, graph=_publisher(handler, "ig"),
                        backends=("graph_ig", "private"), mode="private-enabled")
    result, body = run("post", "photo", "https://example.com/p.jpg", "--caption", "hi")
    assert result.exit_code == 0, result.output
    assert body["backend_used"] == "graph_ig"
    assert body["data"]["id"] == "m1"
    assert "(#10)" in body["data"]["permalink_error"]
    private.post_photo.assert_not_called()


def test_failed_container_is_never_published():
    import pytest
    from clinstagram.backends.graph import GraphAPIError

    backend, calls = _publishing_backend(["IN_PROGRESS", "ERROR"])
    with pytest.raises(GraphAPIError, match="failed"):
        backend.post_reel("https://example.com/r.mp4")
    assert "publish" not in calls


def test_fb_token_uses_linked_instagram_account():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/me":
            return httpx.Response(200, json={"id": "page1", "instagram_business_account": {"id": "ig9"}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    backend = GraphBackend(token="tok", login_type="fb", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert backend._me_id() == "ig9"


def test_system_user_token_needs_stored_ig_user_id():
    # Meta's unified-login system-user token: /me has no id at all.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/me":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(GraphAPIError, match="--ig-user-id"):
        GraphBackend(token="tok", login_type="fb", client=client)._me_id()
    assert GraphBackend(token="tok", login_type="fb", client=client, ig_user_id="1784")._me_id() == "1784"


# ── Reels: cover image, frame offset, feed ──────────────────────────


def _reel_backend(published_id: str = "m1"):
    """A Graph backend that records each POST /media payload and publishes as `published_id`."""
    sent: list[dict] = []
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if path == "/v21.0/me":
            return httpx.Response(200, json={"id": "42"})
        if request.method == "POST" and path == "/v21.0/42/media":
            sent.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"id": "c1"})
        if request.method == "GET" and path == "/v21.0/c1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and path == "/v21.0/42/media_publish":
            return httpx.Response(200, json={"id": published_id})
        if request.method == "GET" and path == f"/v21.0/{published_id}":
            return httpx.Response(200, json={
                "id": published_id, "shortcode": "DQx1",
                "permalink": "https://www.instagram.com/reel/DQx1/",
            })
        raise AssertionError(f"unexpected {request.method} {path}")

    backend = GraphBackend(token="tok", login_type="ig", client=httpx.Client(transport=httpx.MockTransport(handler)))
    backend._sleep = lambda s: None
    return backend, sent, seen


def test_reel_cover_url_is_sent_as_cover_url():
    backend, sent, _ = _reel_backend()
    backend.post_reel("https://example.com/r.mp4", caption="hi", cover="https://cdn.example.com/cover.jpg")
    assert sent[0]["cover_url"] == "https://cdn.example.com/cover.jpg"
    assert "thumb_offset" not in sent[0]
    assert sent[0]["share_to_feed"] == "true"


def test_reel_thumbnail_offset_still_maps_to_thumb_offset():
    backend, sent, _ = _reel_backend()
    backend.post_reel("https://example.com/r.mp4", thumbnail="1500")
    assert sent[0]["thumb_offset"] == "1500"
    assert "cover_url" not in sent[0]


def test_reel_can_stay_out_of_the_feed():
    backend, sent, _ = _reel_backend()
    backend.post_reel("https://example.com/r.mp4", share_to_feed=False)
    assert sent[0]["share_to_feed"] == "false"


@pytest.mark.parametrize("kwargs,match", [
    ({"cover": "https://cdn.example.com/c.jpg", "thumbnail": "1500"}, "not both"),
    ({"cover": "/tmp/cover.jpg"}, "public image URL"),
    ({"thumbnail": "/tmp/cover.jpg"}, "--cover"),
])
def test_reel_refuses_cover_options_graph_cannot_honour(kwargs, match):
    backend, sent, seen = _reel_backend()
    with pytest.raises(ValueError, match=match):
        backend.post_reel("https://example.com/r.mp4", **kwargs)
    assert sent == []
    assert ("POST", "/v21.0/42/media_publish") not in seen


# ── `post reel` command ──────────────────────────────────────────────


def _cli(monkeypatch, tmp_path, graph=None, backends=("graph_ig",), mode=None):
    """Run clinstagram against `graph` (a GraphBackend over MockTransport) and a mock private backend."""
    from unittest.mock import MagicMock

    from typer.testing import CliRunner

    from clinstagram.auth.keychain import BACKEND_TOKEN_MAP, SecretsStore
    from clinstagram.cli import app

    secrets = SecretsStore(backend="memory")
    for name in backends:
        secrets.set("default", BACKEND_TOKEN_MAP[name], "fake-token")
    private = MagicMock()
    private.name = "private"
    private.post_reel.return_value = {"id": "p1", "status": "published"}

    def instantiate(ctx, name, feature=None):
        if name.startswith("graph"):
            assert graph is not None, f"{name} should not have been used"
            return graph
        return private

    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend", instantiate)
    monkeypatch.setattr("clinstagram.commands._dispatch._apply_jitter_delay", lambda ctx, f: None)
    monkeypatch.setattr("clinstagram.commands.post.stage", lambda src, name: src)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    runner = CliRunner()
    if mode:
        runner.invoke(app, ["config", "mode", mode], env=env)

    def run(*args):
        result = runner.invoke(app, ["--json", *args], env=env)
        return result, (json.loads(result.output) if result.output.strip() else None)

    return run, private


def test_cli_reel_cover_and_no_feed_reach_graph(monkeypatch, tmp_path):
    backend, sent, _ = _reel_backend()
    run, _ = _cli(monkeypatch, tmp_path, graph=backend)
    result, body = run("post", "reel", "https://example.com/r.mp4", "--cover",
                       "https://cdn.example.com/cover.jpg", "--no-feed")
    assert result.exit_code == 0, result.output
    assert sent[0]["cover_url"] == "https://cdn.example.com/cover.jpg"
    assert sent[0]["share_to_feed"] == "false"
    assert body["backend_used"] == "graph_ig"


def test_cli_reel_shares_to_feed_by_default(monkeypatch, tmp_path):
    backend, sent, _ = _reel_backend()
    run, _ = _cli(monkeypatch, tmp_path, graph=backend)
    result, _ = run("post", "reel", "https://example.com/r.mp4", "--thumbnail", "2000")
    assert result.exit_code == 0, result.output
    assert sent[0]["share_to_feed"] == "true"
    assert sent[0]["thumb_offset"] == "2000"


def test_cli_reel_refuses_cover_with_thumbnail(monkeypatch, tmp_path):
    run, private = _cli(monkeypatch, tmp_path)
    result, body = run("post", "reel", "https://example.com/r.mp4",
                       "--cover", "https://cdn.example.com/c.jpg", "--thumbnail", "1500")
    assert result.exit_code == 1
    assert "only one" in body["error"]
    private.post_reel.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_reel_local_cover_on_graph_is_a_user_error(monkeypatch, tmp_path, dry_run):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpg")
    run, _ = _cli(monkeypatch, tmp_path)  # graph only: any Graph call would fail the test
    args = ["--dry-run"] if dry_run else []
    result, body = run(*args, "post", "reel", "https://example.com/r.mp4", "--cover", str(cover))
    assert result.exit_code == 1
    assert "public image URL" in body["error"]
    assert "--backend private" in body["remediation"]


def test_cli_reel_image_thumbnail_on_graph_points_to_cover(monkeypatch, tmp_path):
    run, _ = _cli(monkeypatch, tmp_path)
    result, body = run("post", "reel", "https://example.com/r.mp4", "--thumbnail", "https://cdn.example.com/c.jpg")
    assert result.exit_code == 1
    assert "--cover" in body["remediation"]


def test_cli_reel_local_cover_goes_to_private_as_its_thumbnail(monkeypatch, tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpg")
    run, private = _cli(monkeypatch, tmp_path, backends=("graph_ig", "private"), mode="private-enabled")
    result, body = run("post", "reel", "https://example.com/r.mp4", "--cover", str(cover), "--no-feed")
    assert result.exit_code == 0, result.output
    assert body["backend_used"] == "private"
    # Same call shape as before --cover existed: the cover is the private API's thumbnail.
    private.post_reel.assert_called_once_with("https://example.com/r.mp4", "", str(cover), "")
