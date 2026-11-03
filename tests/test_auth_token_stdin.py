"""auth connect-ig / connect-fb --token-stdin, the live token check after storing,
and auth probe's media-access check.

Meta is faked with httpx.MockTransport; no network, no real tokens.
"""
from __future__ import annotations

import io
import json

import httpx
import pytest
from typer.testing import CliRunner

from clinstagram.auth.keychain import SecretsStore
from clinstagram.cli import app
from clinstagram.config import load_config

runner = CliRunner()
TOKEN = "EAAfakeTOKENforTESTS0123456789"
IG = "17841400000000001"


class FakeMeta:
    """Graph API stand-in: answers by URL path and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, object] = {
            "/v21.0/me": (200, {"id": "sys-1", "name": "ARN agent"}),
            f"/v21.0/{IG}": (200, {"id": IG, "username": "agingresearch.news", "media_count": 12}),
            f"/v21.0/{IG}/media": (200, {"data": [{"id": "m1"}]}),
        }

    def refuse(self, path: str, message: str, code: int, status: int = 400) -> None:
        self.routes[path] = (status, {"error": {"message": message, "type": "OAuthException", "code": code}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(request.url.path)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return httpx.Response(400, json={"error": {"message": "Unsupported get request", "code": 100}})
        status, body = answer
        return httpx.Response(status, json=body)


@pytest.fixture
def env(tmp_path):
    return {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}


@pytest.fixture
def secrets(monkeypatch):
    store = SecretsStore(backend="memory")
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: store)
    monkeypatch.setattr("clinstagram.commands.auth._get_secrets", lambda ctx: store)
    return store


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(
        "clinstagram.commands.auth._graph_client",
        lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    return fake


def _auth(env, *args, stdin=None, full=False):
    flags = ["--json", "--full"] if full else ["--json"]
    return runner.invoke(app, [*flags, "--account", "news", "auth", *args], input=stdin, env=env)


def _connect_fb(env, full=False):
    return _auth(env, "connect-fb", "--token-stdin", "--ig-user-id", IG, stdin=TOKEN + "\n", full=full)


def _data(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def test_stdin_token_is_stored_stripped_and_never_echoed(env, secrets, meta, tmp_path):
    r = _auth(env, "connect-fb", "--token-stdin", "--ig-user-id", IG, stdin=f"  {TOKEN}\r\n\n")
    assert r.exit_code == 0, r.output
    assert secrets.get("news", "graph_fb_token") == TOKEN
    assert TOKEN not in r.output  # stdout and stderr
    assert TOKEN not in (tmp_path / "config.toml").read_text()
    data = json.loads(r.stdout)["data"]
    assert data["connected"] is True
    entry = load_config(tmp_path).accounts["news"]
    assert entry.ig_user_id == IG and entry.backends == ["graph_fb"]
    # The token travels in the Authorization header only, never in a URL.
    assert meta.requests
    for request in meta.requests:
        assert TOKEN not in str(request.url)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_connect_ig_reads_stdin_too(env, secrets, meta):
    r = _auth(env, "connect-ig", "--token-stdin", stdin=TOKEN + "\n")
    assert r.exit_code == 0, r.output
    assert secrets.get("news", "graph_ig_token") == TOKEN
    assert TOKEN not in r.output


@pytest.mark.parametrize("command,extra", [("connect-ig", []), ("connect-fb", ["--ig-user-id", IG])])
def test_token_and_token_stdin_together_are_refused(env, secrets, meta, tmp_path, command, extra):
    r = _auth(env, command, "--token", TOKEN, "--token-stdin", *extra, stdin="EAAother\n")
    assert r.exit_code == 1
    err = json.loads(r.stderr)
    assert "not both" in err["error"]
    assert f"auth {command} --token-stdin" in err["remediation"]
    assert TOKEN not in r.output
    assert secrets.list_keys("news") == []
    assert "news" not in load_config(tmp_path).accounts  # --ig-user-id not half-applied
    assert not meta.requests


@pytest.mark.parametrize("stdin", [None, "", "  \n\t\r\n"])
def test_empty_stdin_is_refused(env, secrets, meta, tmp_path, stdin):
    r = _auth(env, "connect-fb", "--token-stdin", "--ig-user-id", IG, stdin=stdin)
    assert r.exit_code == 1
    err = json.loads(r.stderr)
    assert err["error"] == "No token on stdin"
    assert "pbpaste | clinstagram --account news auth connect-fb --token-stdin --ig-user-id " + IG in err["remediation"]
    assert secrets.get("news", "graph_fb_token") is None
    assert "news" not in load_config(tmp_path).accounts
    assert not meta.requests


@pytest.mark.parametrize("stdin", [f"Bearer {TOKEN}\n", f"{TOKEN}\nsecond line\n"])
def test_stdin_with_more_than_a_token_is_refused(env, secrets, meta, stdin):
    secrets.set("news", "graph_fb_token", "EAAworking")
    r = _auth(env, "connect-fb", "--token-stdin", stdin=stdin)
    assert r.exit_code == 1
    assert "isn't a single token" in json.loads(r.stderr)["error"]
    assert TOKEN not in r.output
    assert secrets.get("news", "graph_fb_token") == "EAAworking"  # the working token survives


def test_stdin_from_a_terminal_is_refused_instead_of_waiting(env, secrets, meta, monkeypatch):
    class Terminal(io.BytesIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("typer.testing.make_input_stream", lambda input, charset: Terminal(b""))
    r = _auth(env, "connect-ig", "--token-stdin")
    assert r.exit_code == 1
    err = json.loads(r.stderr)
    assert "nothing is piped in" in err["error"]
    assert err["remediation"] == "Pipe it in: pbpaste | clinstagram --account news auth connect-ig --token-stdin"
    assert secrets.list_keys("news") == []


def test_missing_token_in_json_mode_points_at_stdin(env, secrets, meta):
    r = _auth(env, "connect-ig")
    assert r.exit_code == 1
    err = json.loads(r.stderr)
    assert err["error"] == "Instagram Login access token is required"
    assert "--token-stdin" in err["remediation"]


def test_token_flag_still_works(env, secrets, meta):
    r = _auth(env, "connect-fb", "--token", TOKEN, "--ig-user-id", IG)
    assert r.exit_code == 0, r.output
    assert secrets.get("news", "graph_fb_token") == TOKEN
    assert json.loads(r.stdout)["data"]["username"] == "agingresearch.news"


# --- live check after storing ----------------------------------------------------

NEW_TOKEN_STEPS = (
    "Meta app dashboard → Instagram API → API setup with Unified login → "
    "2. Generate access tokens → Generate token on the account's row"
)
RECONNECT = f"pbpaste | clinstagram --account news auth connect-fb --token-stdin --ig-user-id {IG}"


def test_connect_reports_identity_and_media_access(env, secrets, meta):
    data = _data(_connect_fb(env))
    assert data["verified"] is True
    assert data["identity"] == {"id": "sys-1", "name": "ARN agent"}
    assert data["ig_user_id"] == IG
    assert data["username"] == "agingresearch.news"
    assert data["media_count"] == 12
    assert data["media_access"] is True
    assert "remediation" not in data and "note" not in data
    assert [q.url.path for q in meta.requests] == ["/v21.0/me", f"/v21.0/{IG}", f"/v21.0/{IG}/media"]
    assert meta.requests[0].url.params["fields"] == "id,name"
    assert meta.requests[-1].url.params["limit"] == "1"


def test_connect_ig_checks_the_instagram_account_behind_me(env, secrets, meta):
    meta.routes["/v21.0/me"] = (200, {"id": IG, "name": "AgingResearch.News"})
    data = _data(_auth(env, "connect-ig", "--token-stdin", stdin=TOKEN))
    assert data["verified"] is True and data["ig_user_id"] == IG and data["media_access"] is True
    assert [q.url.host for q in meta.requests] == ["graph.instagram.com"] * 3  # /me read once


@pytest.mark.parametrize("message,code", [
    ("(#200) API access blocked", 200),
    ("(#10) Application does not have permission for this action", 10),
    ("Cannot call API for app 1234567890 on behalf of user 9876543210", 200),
])
def test_media_refused_after_boost_is_flagged_and_the_token_kept(env, secrets, meta, tmp_path, message, code):
    # 3 Nov 2026: the profile kept reading (media_count 0) while the media list failed.
    meta.routes[f"/v21.0/{IG}"] = (200, {"id": IG, "username": "agingresearch.news", "media_count": 0})
    meta.refuse(f"/v21.0/{IG}/media", message, code)
    data = _data(_connect_fb(env))
    assert data["connected"] is True
    assert data["verified"] is False
    assert data["media_access"] is False
    assert data["media_count"] == 0
    assert data["verify_error"] == message
    fix = data["remediation"]
    assert "authorization of the Meta app is broken" in fix and "Boost flow" in fix
    assert NEW_TOKEN_STEPS in fix
    assert fix.endswith(RECONNECT)
    assert secrets.get("news", "graph_fb_token") == TOKEN  # never deleted
    assert load_config(tmp_path).accounts["news"].backends == ["graph_fb"]


def test_network_failure_during_the_check_still_stores(env, secrets, meta, tmp_path):
    meta.routes["/v21.0/me"] = httpx.ConnectError("[Errno 8] nodename nor servname provided")
    data = _data(_connect_fb(env, full=True))
    assert data["connected"] is True
    assert "verified" in data and data["verified"] is None
    assert data["verify_error"].startswith("ConnectError: ")
    assert "token is stored" in data["note"] and "clinstagram --account news auth probe" in data["note"]
    assert "remediation" not in data and "media_access" not in data
    assert secrets.get("news", "graph_fb_token") == TOKEN
    assert load_config(tmp_path).accounts["news"].backends == ["graph_fb"]
    # Compact output drops nulls: no `verified` key means it couldn't be checked.
    compact = _data(_connect_fb(env))
    assert "verified" not in compact and compact["note"]


@pytest.mark.parametrize("status,code,message", [
    (400, 4, "(#4) Application request limit reached"),
    (500, 1, "An unknown error occurred"),
    (503, 2, "Service temporarily unavailable"),
])
def test_rate_limits_and_meta_outages_are_not_blamed_on_the_token(env, secrets, meta, status, code, message):
    meta.refuse(f"/v21.0/{IG}/media", message, code, status)
    data = _data(_connect_fb(env, full=True))
    assert data["verified"] is None
    assert "media_access" not in data and "remediation" not in data
    assert data["verify_error"] == message
    assert "couldn't answer the check" in data["note"]


def test_invalid_token_gets_a_new_token_fix(env, secrets, meta):
    meta.refuse("/v21.0/me", "Error validating access token: Session has expired", 190)
    data = _data(_connect_fb(env))
    assert data["verified"] is False and "media_access" not in data
    assert data["remediation"].startswith("Meta rejected the token as invalid or expired.")
    assert NEW_TOKEN_STEPS in data["remediation"] and data["remediation"].endswith(RECONNECT)
    assert secrets.get("news", "graph_fb_token") == TOKEN


def test_fb_token_without_an_instagram_account_asks_for_ig_user_id(env, secrets, meta):
    # /me has no instagram_business_account, so commands would act on /me itself.
    data = _data(_auth(env, "connect-fb", "--token-stdin", stdin=TOKEN))
    assert data["verified"] is False
    assert data["remediation"] == (
        "This token isn't linked to an Instagram account. Store it with the ID of the Instagram "
        "account it acts on: pbpaste | clinstagram --account news auth connect-fb --token-stdin --ig-user-id <id>"
    )


def test_a_token_echoed_back_in_an_error_is_redacted(env, secrets, meta):
    meta.refuse("/v21.0/me", f"Malformed access token {TOKEN}", 190)
    r = _connect_fb(env)
    assert TOKEN not in r.output
    assert _data(r)["verify_error"] == "Malformed access token <token>"
    # A one-letter "token" isn't a secret; replacing it everywhere would garble Meta's message.
    meta.refuse("/v21.0/me", "Invalid OAuth access token - Cannot parse access token", 190)
    data = _data(_auth(env, "connect-ig", "--token-stdin", stdin="a"))
    assert data["verify_error"] == "Invalid OAuth access token - Cannot parse access token"


def test_human_output_reports_each_outcome(capsys):
    from clinstagram.commands.auth import _print_check

    _print_check({"verified": True, "ig_user_id": IG, "media_count": 12}, "agingresearch.news")
    _print_check({"verified": False, "verify_error": "(#10) No [media]", "remediation": "New token"}, None)
    _print_check({"verified": None, "note": "Couldn't check"}, None)
    out = capsys.readouterr().out
    assert "Verified: reads @agingresearch.news and its media (12 posts)" in out
    assert "Warning: Meta refused the token: (#10) No [media]" in out and "Fix: New token" in out
    assert "Not verified: Couldn't check" in out


# --- auth probe --------------------------------------------------------------------

def test_probe_reports_a_healthy_graph_token(env, secrets, meta):
    _data(_connect_fb(env))
    meta.requests.clear()
    backends = _data(_auth(env, "probe"))["backends"]
    fb = backends["graph_fb"]
    assert fb["configured"] is True and fb["reachable"] is True and fb["verified"] is True
    assert fb["account_id"] == IG and fb["username"] == "agingresearch.news"
    assert fb["media_access"] is True and fb["media_count"] == 12
    assert "error" not in fb and "remediation" not in fb
    # It probes the account commands act on (the stored --ig-user-id).
    assert [q.url.path for q in meta.requests] == ["/v21.0/me", f"/v21.0/{IG}", f"/v21.0/{IG}/media"]
    assert backends["graph_ig"]["configured"] is False and backends["private"]["configured"] is False


def test_probe_reports_a_profile_only_token_as_broken(env, secrets, meta):
    _data(_connect_fb(env))
    meta.routes[f"/v21.0/{IG}"] = (200, {"id": IG, "username": "agingresearch.news", "media_count": 0})
    meta.refuse(f"/v21.0/{IG}/media", "(#10) Application does not have permission for this action", 10)
    r = _auth(env, "probe")
    fb = _data(r)["backends"]["graph_fb"]
    assert fb["configured"] is True
    assert fb["reachable"] is False and fb["verified"] is False and fb["media_access"] is False
    assert fb["media_count"] == 0
    assert fb["error"] == "(#10) Application does not have permission for this action"
    assert "authorization of the Meta app is broken" in fb["remediation"]
    assert NEW_TOKEN_STEPS in fb["remediation"] and fb["remediation"].endswith(RECONNECT)
    assert TOKEN not in r.output
    assert secrets.get("news", "graph_fb_token") == TOKEN


def test_probe_human_output_shows_the_error_and_the_fix(env, secrets, meta, monkeypatch):
    _data(_connect_fb(env))
    meta.refuse(f"/v21.0/{IG}/media", "(#200) API access blocked", 200)
    monkeypatch.setattr("typer.testing._NamedTextIOWrapper.isatty", lambda self: True)  # a terminal: no JSON
    r = runner.invoke(app, ["--account", "news", "auth", "probe"], env=env)
    assert r.exit_code == 0, r.output
    lines = r.stdout.splitlines()
    assert "  graph_fb: Configured but failing" in lines
    assert "    error: (#200) API access blocked" in lines
    assert any(line.startswith("    fix: This account's authorization of the Meta app is broken") for line in lines)
    assert "  graph_ig: Not configured" in lines
    assert TOKEN not in r.output


def test_probe_network_failure_is_failing_but_unjudged(env, secrets, meta):
    _data(_connect_fb(env))
    meta.routes["/v21.0/me"] = httpx.ConnectError("[Errno 8] nodename nor servname provided")
    fb = _data(_auth(env, "probe", full=True))["backends"]["graph_fb"]
    assert fb["reachable"] is False and fb["verified"] is None
    assert fb["error"].startswith("ConnectError: ") and "auth probe" in fb["note"]
    assert "remediation" not in fb
