"""Graph insights: type-fitting metrics, Meta's metric churn, small posts, account days, summary."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from clinstagram.backends.graph import GraphAPIError, GraphBackend
from clinstagram.cli import app

FEED = "reach,views,likes,comments,saved,shares,total_interactions,profile_visits,follows"
REELS = "reach,views,likes,comments,saved,shares,total_interactions,ig_reels_avg_watch_time,ig_reels_video_view_total_time"
STORY = "reach,views,replies,shares,total_interactions,navigation,follows,profile_visits"


class FakeGraph:
    """Graph API stand-in: answers "METHOD /path" routes and records every request.

    A route's answer is a JSON body, an httpx.Response, a callable taking the request,
    or a list of those served in turn.
    """

    def __init__(self, routes: dict):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path.removeprefix('/v21.0')}"
        if key not in self.routes:
            raise AssertionError(f"unexpected {key}?{request.url.query.decode()}")
        answer = self.routes[key]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if callable(answer):
            answer = answer(request)
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def backend(self, login_type: str = "ig", ig_user_id: str | None = "42") -> GraphBackend:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return GraphBackend(token="tok", login_type=login_type, client=client, ig_user_id=ig_user_id)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.removeprefix("/v21.0") == path]

    def metrics(self, path: str) -> list[str]:
        """The metric= list of each call to `path`, in order."""
        return [r.url.params["metric"] for r in self.calls(path)]


def graph_error(code: int, message: str, status: int = 400, **extra) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "type": "OAuthException",
                                                  "code": code, **extra}})


def insights(**values) -> dict:
    return {"data": [{"name": k, "period": "lifetime", "values": [{"value": v}]} for k, v in values.items()]}


NOT_ENOUGH_VIEWERS = graph_error(10, "(#10) Not enough viewers for the media to show insights")
NO_PERMISSION = graph_error(10, "(#10) Application does not have permission for this action")


# ── Media insights by type ──────────────────────────────────────────


@pytest.mark.parametrize("media_type,product_type,expected", [
    ("IMAGE", "FEED", FEED),
    ("CAROUSEL_ALBUM", "FEED", FEED),
    ("VIDEO", "REELS", REELS),
    ("IMAGE", "STORY", STORY),
    ("VIDEO", "STORY", STORY),
])
def test_insights_ask_for_metrics_that_fit_the_media_type(media_type, product_type, expected):
    fake = FakeGraph({
        "GET /777": {"id": "777", "media_type": media_type, "media_product_type": product_type},
        "GET /777/insights": insights(reach=12, views=40),
    })
    result = fake.backend().analytics_insights("777")
    assert fake.calls("/777")[0].url.params["fields"] == "media_type,media_product_type"
    assert fake.metrics("/777/insights") == [expected]
    assert result == {"media_id": "777", "media_type": media_type, "product_type": product_type,
                      "insights": {"reach": 12, "views": 40}}


def test_latest_takes_the_type_from_the_listing_without_another_call():
    fake = FakeGraph({
        "GET /42/media": {"data": [{"id": "901", "media_type": "VIDEO", "media_product_type": "REELS"}]},
        "GET /901/insights": insights(reach=5, ig_reels_avg_watch_time=4100),
    })
    result = fake.backend().analytics_insights("latest")
    assert [r.url.path for r in fake.requests] == ["/v21.0/42/media", "/v21.0/901/insights"]
    assert fake.metrics("/901/insights") == [REELS]
    assert result["product_type"] == "REELS"
    assert result["insights"]["ig_reels_avg_watch_time"] == 4100


def test_unknown_type_falls_back_to_feed_metrics():
    fake = FakeGraph({
        "GET /777": graph_error(100, "(#100) Tried accessing nonexisting field (media_product_type)"),
        "GET /777/insights": insights(reach=3),
    })
    result = fake.backend().analytics_insights("777")
    assert fake.metrics("/777/insights") == [FEED]
    assert result == {"media_id": "777", "insights": {"reach": 3}}


def test_dead_token_on_the_type_lookup_is_raised_at_once():
    fake = FakeGraph({"GET /777": graph_error(190, "Error validating access token", status=401)})
    with pytest.raises(GraphAPIError) as err:
        fake.backend().analytics_insights("777")
    assert err.value.code == 190
    assert fake.calls("/777/insights") == []


# ── Meta's metric churn ─────────────────────────────────────────────


def test_metrics_meta_names_as_unsupported_are_dropped_and_retried():
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": [
            graph_error(100, "(#100) The following metrics are not supported for this media: profile_visits, follows"),
            insights(reach=9, views=20),
        ],
    })
    result = fake.backend().analytics_insights("777")
    assert fake.metrics("/777/insights") == [FEED, "reach,views,likes,comments,saved,shares,total_interactions"]
    assert result["insights"] == {"reach": 9, "views": 20}
    assert result["dropped_metrics"] == ["profile_visits", "follows"]


def test_allowed_values_error_drops_only_the_metrics_it_leaves_out():
    # This form lists what IS allowed; dropping every metric it mentions would be wrong.
    fake = FakeGraph({
        "GET /777": {"media_type": "VIDEO", "media_product_type": "REELS"},
        "GET /777/insights": [
            graph_error(100, "(#100) metric[8] must be one of the following values: reach, views, likes, "
                             "comments, saved, shares, total_interactions, ig_reels_avg_watch_time"),
            insights(reach=1),
        ],
    })
    result = fake.backend().analytics_insights("777")
    assert result["dropped_metrics"] == ["ig_reels_video_view_total_time"]
    assert fake.metrics("/777/insights")[1] == REELS.removesuffix(",ig_reels_video_view_total_time")


def test_metric_dropping_gives_up_after_three_retries():
    rejections = [graph_error(100, f"(#100) Metric {m} is not supported for this media")
                  for m in ("views", "likes", "comments", "saved")]
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": rejections,
    })
    with pytest.raises(GraphAPIError, match="saved"):
        fake.backend().analytics_insights("777")
    assert len(fake.calls("/777/insights")) == 4  # the first try plus three retries


def test_code_100_that_names_no_requested_metric_is_raised():
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": graph_error(100, "(#100) Invalid parameter"),
    })
    with pytest.raises(GraphAPIError, match="Invalid parameter"):
        fake.backend().analytics_insights("777")
    assert len(fake.calls("/777/insights")) == 1


def test_meta_user_message_is_kept_in_the_error():
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": graph_error(
            100, "Invalid parameter", error_subcode=2108006,
            error_user_title="Media Posted Before Business Account Conversion",
            error_user_msg="The media was posted before the account was converted to a business account."),
    })
    with pytest.raises(GraphAPIError) as err:
        fake.backend().analytics_insights("777")
    assert "posted before the account was converted" in str(err.value)
    assert err.value.subcode == 2108006


# ── Not enough viewers ──────────────────────────────────────────────


def test_not_enough_viewers_is_an_answer_not_an_error():
    fake = FakeGraph({
        "GET /777": {"media_type": "VIDEO", "media_product_type": "REELS"},
        "GET /777/insights": NOT_ENOUGH_VIEWERS,
    })
    assert fake.backend().analytics_insights("777") == {
        "media_id": "777", "media_type": "VIDEO", "product_type": "REELS",
        "insights": {}, "not_enough_viewers": True,
    }


def test_missing_permission_with_the_same_code_still_raises():
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": NO_PERMISSION,
    })
    with pytest.raises(GraphAPIError, match="does not have permission"):
        fake.backend().analytics_insights("777")


# ── Commands ────────────────────────────────────────────────────────


def graph_cli(monkeypatch, tmp_path, backend: GraphBackend, backends=("graph_ig",)):
    """Run clinstagram --json against `backend`; a mock stands in for the private API."""
    from clinstagram.auth.keychain import BACKEND_TOKEN_MAP, SecretsStore

    secrets = SecretsStore(backend="memory")
    for name in backends:
        secrets.set("default", BACKEND_TOKEN_MAP[name], "fake-token")
    private = MagicMock()
    private.name = "private"
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend",
                        lambda ctx, name, feature=None: backend if name.startswith("graph") else private)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, ["--json", *args], env=env)
        return result, (json.loads(result.output) if result.output.strip() else None)

    return run, private


def test_cli_not_enough_viewers_exits_0(monkeypatch, tmp_path):
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "STORY"},
        "GET /777/insights": NOT_ENOUGH_VIEWERS,
    })
    run, _ = graph_cli(monkeypatch, tmp_path, fake.backend())
    result, body = run("analytics", "insights", "777")
    assert result.exit_code == 0, result.output
    assert body["data"] == {"media_id": "777", "media_type": "IMAGE", "product_type": "STORY",
                            "insights": {}, "not_enough_viewers": True}


def test_cli_missing_permission_is_still_exit_7(monkeypatch, tmp_path):
    fake = FakeGraph({
        "GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
        "GET /777/insights": NO_PERMISSION,
    })
    run, _ = graph_cli(monkeypatch, tmp_path, fake.backend())
    result, body = run("analytics", "insights", "777")
    assert result.exit_code == 7
    assert "does not have permission" in body["error"]
