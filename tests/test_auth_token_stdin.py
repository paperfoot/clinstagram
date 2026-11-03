"""auth connect-ig / connect-fb --token-stdin. Meta is faked; no network, no real tokens."""
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


def _auth(env, *args, stdin=None):
    return runner.invoke(app, ["--json", "--account", "news", "auth", *args], input=stdin, env=env)


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
