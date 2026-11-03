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
        raise AssertionError(f"unexpected {request.method} {path}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GraphBackend(token="tok", login_type="ig", client=client)
    backend._sleep = lambda s: None
    return backend, calls


def test_reel_waits_for_container_to_finish_before_publishing():
    backend, calls = _publishing_backend(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    result = backend.post_reel("https://example.com/r.mp4", caption="hi")
    assert result == {"id": "m1", "status": "published"}
    assert calls == ["poll", "poll", "poll", "publish"]


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
