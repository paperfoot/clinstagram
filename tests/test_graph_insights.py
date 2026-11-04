"""Graph insights: type-fitting metrics, Meta's metric churn, small posts, account days, summary."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


def graph_cli(monkeypatch, tmp_path, backend: GraphBackend, backends=("graph_ig",), account="default"):
    """Run clinstagram --json against `backend`; a mock stands in for the private API."""
    from clinstagram.auth.keychain import BACKEND_TOKEN_MAP, SecretsStore

    secrets = SecretsStore(backend="memory")
    for name in backends:
        secrets.set(account, BACKEND_TOKEN_MAP[name], "fake-token")
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
    assert "--backend private" in body["remediation"]


# ── Account insights for a UTC day ──────────────────────────────────

ACCOUNT = ("reach,views,accounts_engaged,total_interactions,profile_views,profile_links_taps,"
           "website_clicks,follows_and_unfollows")
NOV_3_NOON = datetime(2026, 11, 3, 12, 34, 56, tzinfo=timezone.utc).timestamp()


def totals(**values) -> dict:
    return {"data": [{"name": k, "period": "day", "total_value": {"value": v}} for k, v in values.items()]}


def utc(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def account_backend(fake: FakeGraph, now: float = NOV_3_NOON) -> GraphBackend:
    backend = fake.backend()
    backend._now = lambda: now
    return backend


def test_account_day_asks_meta_for_that_utc_day():
    fake = FakeGraph({"GET /42/insights": totals(reach=120, follows_and_unfollows=3)})
    result = account_backend(fake).analytics_account(day="2026-11-01")
    params = fake.calls("/42/insights")[0].url.params
    assert params["metric"] == ACCOUNT
    assert (params["period"], params["metric_type"]) == ("day", "total_value")
    assert (int(params["since"]), int(params["until"])) == (utc(2026, 11, 1), utc(2026, 11, 2))
    assert result == {"period": "day", "day": "2026-11-01",
                      "insights": {"reach": 120, "follows_and_unfollows": 3}}


def test_account_defaults_to_today_so_far():
    fake = FakeGraph({"GET /42/insights": totals(reach=7)})
    result = account_backend(fake).analytics_account()
    params = fake.calls("/42/insights")[0].url.params
    assert (int(params["since"]), int(params["until"])) == (utc(2026, 11, 3), int(NOV_3_NOON))
    assert result["day"] == "2026-11-03"
    assert result["period"] == "day"


def test_account_drops_metrics_meta_no_longer_offers():
    fake = FakeGraph({"GET /42/insights": [
        graph_error(100, "(#100) metric[4] must be one of the following values: reach, views, "
                         "accounts_engaged, total_interactions, profile_links_taps, follows_and_unfollows"),
        totals(reach=5),
    ]})
    result = account_backend(fake).analytics_account(day="2026-11-02")
    assert result["dropped_metrics"] == ["profile_views", "website_clicks"]
    assert fake.metrics("/42/insights")[1] == ("reach,views,accounts_engaged,total_interactions,"
                                               "profile_links_taps,follows_and_unfollows")
    assert result["insights"] == {"reach": 5}


def test_account_refuses_a_day_that_has_not_started():
    fake = FakeGraph({})
    with pytest.raises(ValueError, match="hasn't started"):
        account_backend(fake).analytics_account(day="2026-11-04")
    assert fake.requests == []


@pytest.mark.parametrize("day", ["2026-13-01", "03-11-2026", "yesterday", "2026-11-3", "2999-01-01"])
def test_cli_bad_day_is_a_user_error(monkeypatch, tmp_path, day):
    fake = FakeGraph({})
    run, _ = graph_cli(monkeypatch, tmp_path, account_backend(fake))
    result, body = run("analytics", "account", "--day", day)
    assert result.exit_code == 1
    assert "--day" in body["error"]
    assert fake.requests == []


def test_cli_account_day_reaches_graph(monkeypatch, tmp_path):
    fake = FakeGraph({"GET /42/insights": totals(reach=64)})
    run, _ = graph_cli(monkeypatch, tmp_path, account_backend(fake))
    result, body = run("analytics", "account", "--day", "2025-06-01")
    assert result.exit_code == 0, result.output
    assert body["data"] == {"period": "day", "day": "2025-06-01", "insights": {"reach": 64}}
    assert int(fake.calls("/42/insights")[0].url.params["since"]) == utc(2025, 6, 1)


def test_cli_account_day_on_the_private_api_is_exit_7(monkeypatch, tmp_path):
    run, private = graph_cli(monkeypatch, tmp_path, account_backend(FakeGraph({})), backends=("private",))
    result, body = run("analytics", "account", "--day", "2025-06-01")
    assert result.exit_code == 7
    assert "Graph API" in body["error"]
    private.analytics_account.assert_not_called()


# ── analytics summary ───────────────────────────────────────────────

PROFILE = {"id": "42", "username": "agingresearch.news", "followers_count": 1200,
           "follows_count": 15, "media_count": 57}
LONG_FIRST_LINE = ("Rapamycin extended median lifespan in mice by up to 25 percent, and the dose timing "
                   "mattered more than the dose")


def summary_routes(**overrides) -> dict:
    """A healthy account: 3 posts (reel, carousel, image), 2 stories (one too new)."""
    routes = {
        "GET /me": PROFILE,
        "GET /42": PROFILE,
        "GET /42/insights": totals(reach=900, views=3000, accounts_engaged=40),
        "GET /42/media": {"data": [
            {"id": "m1", "media_type": "VIDEO", "media_product_type": "REELS",
             "timestamp": "2026-11-03T09:15:00+0000", "permalink": "https://www.instagram.com/reel/R1/",
             "caption": f"\n{LONG_FIRST_LINE}\n\nSource: Nature Aging"},
            {"id": "m2", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
             "timestamp": "2026-11-02T18:00:00+0000", "permalink": "https://www.instagram.com/p/C2/",
             "caption": "Five ways to keep muscle after 60"},
            {"id": "m3", "media_type": "IMAGE", "media_product_type": "FEED",
             "timestamp": "2026-11-01T08:00:00+0000", "permalink": "https://www.instagram.com/p/I3/"},
        ]},
        "GET /m1/insights": insights(reach=500, views=1500, ig_reels_avg_watch_time=4200),
        "GET /m2/insights": [
            graph_error(100, "(#100) The following metrics are not supported for this media: profile_visits, follows"),
            insights(reach=80, saved=4),
        ],
        "GET /m3/insights": insights(reach=30),
        "GET /42/stories": {"data": [
            {"id": "s1", "media_type": "IMAGE", "media_product_type": "STORY",
             "timestamp": "2026-11-03T07:00:00+0000", "permalink": "https://www.instagram.com/stories/x/s1/"},
            {"id": "s2", "media_type": "VIDEO", "media_product_type": "STORY",
             "timestamp": "2026-11-03T11:00:00+0000", "permalink": "https://www.instagram.com/stories/x/s2/"},
        ]},
        "GET /s1/insights": insights(reach=60, replies=2),
        "GET /s2/insights": NOT_ENOUGH_VIEWERS,
    }
    routes.update(overrides)
    return routes


def summary_backend(fake: FakeGraph, login_type: str = "ig", ig_user_id: str | None = None) -> GraphBackend:
    backend = fake.backend(login_type=login_type, ig_user_id=ig_user_id)
    backend._now = lambda: NOV_3_NOON
    return backend


@pytest.mark.parametrize("login_type,ig_user_id,lookup", [
    ("ig", None, ["/me"]),         # with Instagram login the profile call doubles as /me
    ("fb", "42", ["/42"]),         # stored IG user id: no lookup
    ("fb", None, ["/me", "/42"]),  # Facebook login, nothing stored: one extra call
])
def test_summary_costs_n_plus_stories_plus_four_calls(login_type, ig_user_id, lookup):
    fake = FakeGraph(summary_routes(**{
        "GET /42/media": {"data": [{"id": "m1", "media_type": "VIDEO", "media_product_type": "REELS"},
                                   {"id": "m3", "media_type": "IMAGE", "media_product_type": "FEED"}]},
        "GET /42/stories": {"data": [{"id": "s1", "media_type": "IMAGE", "media_product_type": "STORY"}]},
    }))
    summary_backend(fake, login_type, ig_user_id).analytics_summary(limit=2)
    n, stories = 2, 1
    assert len(fake.requests) == n + stories + 4 + (len(lookup) - 1)
    assert [r.url.path.removeprefix("/v21.0") for r in fake.requests] == lookup + [
        "/42/insights", "/42/media", "/m1/insights", "/m3/insights", "/42/stories", "/s1/insights",
    ]
    assert fake.calls("/42/media")[0].url.params["limit"] == "2"


def test_summary_reports_each_post_and_story_with_its_own_metrics():
    posts = summary_routes()["GET /42/media"]["data"]
    second_carousel = {"id": "m4", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
                       "timestamp": "2026-11-01T20:00:00+0000", "permalink": "https://www.instagram.com/p/C4/"}
    fake = FakeGraph(summary_routes(**{
        "GET /42/media": {"data": posts[:2] + [second_carousel] + posts[2:]},
        "GET /m4/insights": insights(reach=44),
    }))
    data = summary_backend(fake).analytics_summary(limit=4)

    assert data["profile"] == {"username": "agingresearch.news", "followers_count": 1200,
                               "following_count": 15, "media_count": 57}
    assert data["today"] == {"period": "day", "day": "2026-11-03",
                             "insights": {"reach": 900, "views": 3000, "accounts_engaged": 40}}
    reel, carousel, _, image = data["media"]
    assert reel["media_type"] == "VIDEO" and reel["product_type"] == "REELS"
    assert reel["timestamp"] == "2026-11-03T09:15:00+0000"
    assert reel["permalink"] == "https://www.instagram.com/reel/R1/"
    assert reel["insights"] == {"reach": 500, "views": 1500, "ig_reels_avg_watch_time": 4200}
    assert len(reel["caption"]) == 80 and reel["caption"].endswith("…")
    assert LONG_FIRST_LINE.startswith(reel["caption"][:-1])
    assert carousel["caption"] == "Five ways to keep muscle after 60"
    assert carousel["insights"] == {"reach": 80, "saved": 4}
    assert image["caption"] is None
    assert [s["id"] for s in data["stories"]] == ["s1", "s2"]
    assert data["stories"][0]["insights"] == {"reach": 60, "replies": 2}
    assert data["stories"][1]["insights"] == {} and data["stories"][1]["not_enough_viewers"] is True
    assert "not_enough_viewers" not in data["stories"][0]
    assert data["dropped_metrics"] == {"CAROUSEL_ALBUM": ["profile_visits", "follows"]}
    assert "errors" not in data

    assert fake.metrics("/m1/insights") == [REELS]
    assert fake.metrics("/s1/insights") == [STORY]
    # Learned on the first carousel, so the next one is asked without them: no second retry.
    assert fake.metrics("/m4/insights") == ["reach,views,likes,comments,saved,shares,total_interactions"]
    # An image is its own group: what a carousel lacks may still work there.
    assert fake.metrics("/m3/insights") == [FEED]
    assert len(fake.requests) == 4 + 2 + 4 + 1  # one retry for the carousel metrics


def test_summary_notes_failures_where_they_happen_and_carries_on():
    fake = FakeGraph(summary_routes(**{
        "GET /42/insights": NO_PERMISSION,
        "GET /m2/insights": graph_error(
            100, "Invalid parameter", error_subcode=2108006,
            error_user_msg="The media was posted before the account was converted to a business account."),
        "GET /42/stories": graph_error(10, "(#10) Application does not have permission for this action"),
    }))
    data = summary_backend(fake).analytics_summary(limit=3)
    assert "does not have permission" in data["errors"]["today"]
    assert "does not have permission" in data["errors"]["stories"]
    assert "today" not in data
    assert data["stories"] == []
    assert "converted to a business account" in data["media"][1]["insights_error"]
    assert data["media"][0]["insights"]["reach"] == 500
    assert data["media"][2]["insights"] == {"reach": 30}


def test_summary_explains_a_token_meta_cut_off():
    # 3 Nov 2026: after Boost, profile reads worked but the post list said (#10).
    fake = FakeGraph(summary_routes(**{"GET /42/media": NO_PERMISSION}))
    with pytest.raises(GraphAPIError) as err:
        summary_backend(fake).analytics_summary()
    assert err.value.code == 10 and err.value.needs_new_token
    message = str(err.value)
    assert "refuses its posts" in message and "(#10) Application does not have permission" in message
    assert "cut off on Meta's side" in message
    # The backend doesn't know the account, so it names no command: one without
    # --account would overwrite the default account's token.
    assert "clinstagram" not in message


def test_summary_treats_a_refused_profile_as_a_cut_off_token():
    # 3 Nov 2026, the first break: every call, the profile read included, said (#200).
    fake = FakeGraph(summary_routes(**{"GET /me": graph_error(200, "(#200) API access blocked.")}))
    with pytest.raises(GraphAPIError) as err:
        summary_backend(fake).analytics_summary()
    assert err.value.code == 200 and err.value.needs_new_token
    assert "refuses this account's own profile ((#200) API access blocked.)" in str(err.value)
    assert len(fake.requests) == 1


def test_only_the_summary_marks_a_refusal_as_needing_a_new_token():
    fake = FakeGraph({"GET /777": {"media_type": "IMAGE", "media_product_type": "FEED"},
                      "GET /777/insights": NO_PERMISSION})
    with pytest.raises(GraphAPIError) as err:
        fake.backend().analytics_insights("777")
    assert not err.value.needs_new_token  # could be a missing insights permission


@pytest.mark.parametrize("fatal", [
    graph_error(4, "(#4) Application request limit reached"),
    graph_error(190, "Error validating access token: Session has expired", status=401),
])
def test_summary_stops_on_rate_limits_and_dead_tokens(fatal):
    fake = FakeGraph(summary_routes(**{"GET /m1/insights": fatal}))
    with pytest.raises(GraphAPIError) as err:
        summary_backend(fake).analytics_summary(limit=3)
    assert err.value.code in (4, 190)
    assert fake.calls("/m2/insights") == []  # nothing after the fatal error


def test_first_caption_line():
    from clinstagram.backends.graph import _first_line

    assert _first_line("\n  \n  Hello there  \nsecond") == "Hello there"
    assert _first_line("") is None and _first_line(None) is None
    assert _first_line("x" * 80) == "x" * 80
    assert _first_line("x" * 81) == "x" * 79 + "…"


def test_cli_summary_json(monkeypatch, tmp_path):
    fake = FakeGraph(summary_routes())
    run, _ = graph_cli(monkeypatch, tmp_path, summary_backend(fake))
    result, body = run("analytics", "summary", "--limit", "3")
    assert result.exit_code == 0, result.output
    assert body["backend_used"] == "graph_ig"
    data = body["data"]
    assert set(data) == {"profile", "today", "media", "stories", "dropped_metrics"}
    assert data["media"][2] == {"id": "m3", "media_type": "IMAGE", "product_type": "FEED",
                                "timestamp": "2026-11-01T08:00:00+0000",
                                "permalink": "https://www.instagram.com/p/I3/", "insights": {"reach": 30}}
    assert '": ' not in result.output  # compact separators


def test_cli_summary_token_cut_off_is_exit_7_without_private_rerun(monkeypatch, tmp_path):
    fake = FakeGraph(summary_routes(**{"GET /42/media": NO_PERMISSION}))
    run, private = graph_cli(monkeypatch, tmp_path, summary_backend(fake), backends=("graph_ig", "private"))
    result, body = run("analytics", "summary")
    assert result.exit_code == 7
    # ops/schedule.py in arn-instagram spots a refused token by "permission" in the error.
    assert "(#10) Application does not have permission" in body["error"]
    assert "Generate token on the account's row" in body["remediation"]
    assert "--backend private" not in body["remediation"]  # the summary can't go that way
    assert private.method_calls == []


@pytest.mark.parametrize("backends,login_type,ig_user_id", [
    (("graph_ig",), "ig", None),
    (("graph_fb",), "fb", "42"),
])
def test_cli_summary_cut_off_says_how_to_reconnect_this_account(monkeypatch, tmp_path, backends,
                                                                login_type, ig_user_id):
    # auth connect-* writes to the default account unless told otherwise, so the fix for
    # --account channel must say --account channel, and must not ask for a permission.
    fake = FakeGraph(summary_routes(**{"GET /42/media": NO_PERMISSION}))
    backend = summary_backend(fake, login_type, ig_user_id)
    run, _ = graph_cli(monkeypatch, tmp_path, backend, backends=backends, account="channel")
    result, body = run("--account", "channel", "analytics", "summary")
    assert result.exit_code == 7, result.output
    suffix = backends[0].split("_")[1]
    assert body["error"].startswith(f"Meta refused the {backends[0]} token for 'channel'. Meta reads")
    assert body["remediation"].endswith(
        f"then: clinstagram --account channel auth connect-{suffix} --token <token>")
    assert "instagram_manage_insights" not in body["remediation"]


def test_cli_summary_needs_graph(monkeypatch, tmp_path):
    run, private = graph_cli(monkeypatch, tmp_path, summary_backend(FakeGraph({})), backends=("private",))
    result, body = run("analytics", "summary")
    assert result.exit_code == 7
    assert "Graph API" in body["error"]
    assert "connect-ig" in body["remediation"]
    assert private.method_calls == []


@pytest.mark.parametrize("limit", ["0", "51", "-3"])
def test_cli_summary_limit_bounds(monkeypatch, tmp_path, limit):
    fake = FakeGraph({})
    run, _ = graph_cli(monkeypatch, tmp_path, summary_backend(fake))
    result, body = run("analytics", "summary", "--limit", limit)
    assert result.exit_code == 1
    assert "--limit" in body["error"]
    assert fake.requests == []


def test_cli_summary_dry_run_and_help_state_the_cost(monkeypatch, tmp_path):
    run, _ = graph_cli(monkeypatch, tmp_path, summary_backend(FakeGraph({})))
    result, body = run("--dry-run", "analytics", "summary")
    assert result.exit_code == 0, result.output
    assert body["data"] == {"dry_run": True, "feature": "analytics_summary", "backend": "graph_ig"}
    manifest = json.loads(CliRunner().invoke(app, ["agent-info"]).output)
    assert "N + S + 4" in manifest["commands"]["analytics summary"]["description"]
    assert "N + S + 4" in CliRunner().invoke(app, ["analytics", "summary", "--help"]).output


def test_summary_table_for_people():
    from clinstagram.commands.analytics import summary_lines

    fake = FakeGraph(summary_routes())
    data = summary_backend(fake).analytics_summary(limit=3)
    text = "\n".join(summary_lines(data))
    assert "@agingresearch.news  followers 1,200  following 15  posts 57" in text
    assert "Today (2026-11-03 UTC, so far): reach 900  views 3,000  accounts engaged 40" in text
    assert "2026-11-03 09:15  REEL" in text and "CAROUSEL" in text
    assert "https://www.instagram.com/reel/R1/" in text
    assert "Stories up now (2)" in text and "not enough viewers" in text
    assert "Meta didn't offer: carousel profile_visits, follows" in text


def test_render_hook_is_for_people_only(capsys):
    from types import SimpleNamespace

    from clinstagram.commands._dispatch import output_success

    seen = []
    output_success(SimpleNamespace(obj={"json": False}), {"a": 1, "b": None}, render=seen.append)
    assert seen == [{"a": 1}]  # shaped (compact) data
    output_success(SimpleNamespace(obj={"json": True}), {"a": 1}, render=seen.append)
    assert len(seen) == 1 and json.loads(capsys.readouterr().out)["data"] == {"a": 1}
    output_success(SimpleNamespace(obj={"json": False, "fields": "a"}), {"a": 1}, render=seen.append)
    assert len(seen) == 1 and "a: 1" in capsys.readouterr().out
