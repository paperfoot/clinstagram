"""Multi-account: registry, switching, removal, per-account proxy, isolation."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from clinstagram.auth.keychain import SecretsStore
from clinstagram.cli import app
from clinstagram.config import load_config

runner = CliRunner()


@pytest.fixture
def env(tmp_path):
    return {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}


@pytest.fixture
def secrets(monkeypatch):
    store = SecretsStore(backend="memory")
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: store)
    monkeypatch.setattr("clinstagram.commands.auth._get_secrets", lambda ctx: store)
    return store


def _json(result):
    assert result.stdout, result.output
    return json.loads(result.stdout)


def _login(env, account, username):
    from clinstagram.auth.private_login import LoginResult

    ok = LoginResult(success=True, username=username, session_json=json.dumps({"uuids": {"uuid": username}}))
    with patch("clinstagram.auth.private_login.login_private", return_value=ok):
        r = runner.invoke(app, ["--json", "--account", account, "auth", "login", "-u", username, "-p", "pw"], env=env)
    assert r.exit_code == 0, r.output


def test_login_registers_account_with_username(env, secrets, tmp_path):
    _login(env, "news", "arn_news")
    cfg = load_config(tmp_path)
    assert cfg.accounts["news"].username == "arn_news"
    assert cfg.accounts["news"].backends == ["private"]
    assert secrets.get("news", "private_session")
    assert secrets.get("default", "private_session") is None  # sessions are per account


def test_list_use_and_default_account_applies(env, secrets, tmp_path):
    _login(env, "news", "arn_news")
    _login(env, "lab", "arn_lab")
    rows = _json(runner.invoke(app, ["--json", "account", "list"], env=env))["data"]
    assert {r["name"]: r["username"] for r in rows} == {"news": "arn_news", "lab": "arn_lab"}
    assert _json(runner.invoke(app, ["--json", "account", "use", "lab"], env=env))["data"] == {"default_account": "lab"}
    status = _json(runner.invoke(app, ["--json", "auth", "status"], env=env))["data"]
    assert status["account"] == "lab"  # no --account: config default_account is honoured


def test_env_var_selects_account(env, secrets):
    _login(env, "news", "arn_news")
    status = _json(runner.invoke(app, ["--json", "auth", "status"], env={**env, "CLINSTAGRAM_ACCOUNT": "news"}))
    assert status["data"]["account"] == "news"
    assert status["data"]["backends"]["private"]["configured"] is True


def test_use_unknown_account_is_user_error(env, secrets):
    r = runner.invoke(app, ["--json", "account", "use", "ghost"], env=env)
    assert r.exit_code == 1


def test_remove_requires_yes_in_json_mode_then_clears_everything(env, secrets, tmp_path):
    _login(env, "news", "arn_news")
    runner.invoke(app, ["--json", "account", "use", "news"], env=env)
    assert runner.invoke(app, ["--json", "account", "remove", "news"], env=env).exit_code == 1
    assert secrets.get("news", "private_session")
    out = _json(runner.invoke(app, ["--json", "account", "remove", "news", "--yes"], env=env))["data"]
    assert out["removed"] == "news"
    assert secrets.get("news", "private_session") is None
    cfg = load_config(tmp_path)
    assert "news" not in cfg.accounts and cfg.default_account == out["default_account"] != "news"


def test_logout_drops_registry_entry(env, secrets, tmp_path):
    _login(env, "news", "arn_news")
    r = runner.invoke(app, ["--json", "--account", "news", "auth", "logout", "--yes"], env=env)
    assert r.exit_code == 0
    assert "news" not in load_config(tmp_path).accounts


def test_set_proxy_validates_redacts_and_applies_per_account(env, secrets, tmp_path, monkeypatch):
    _login(env, "news", "arn_news")
    bad = runner.invoke(app, ["--json", "account", "set-proxy", "news", "ftp://x"], env=env)
    assert bad.exit_code == 1
    out = _json(runner.invoke(app, ["--json", "account", "set-proxy", "news", "socks5://u:secret@1.2.3.4:1080"], env=env))
    assert "secret" not in json.dumps(out)
    assert load_config(tmp_path).accounts["news"].proxy == "socks5://u:secret@1.2.3.4:1080"
    shown = runner.invoke(app, ["--json", "config", "show"], env=env).stdout
    assert "secret" not in shown

    seen = {}

    def fake_instantiate(ctx, name, feature=None):
        seen["proxy"] = ctx.obj["proxy"]
        b = MagicMock()
        b.name = "private"
        b.user_info.return_value = {"username": "x"}
        return b

    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend", fake_instantiate)
    runner.invoke(app, ["--json", "--account", "news", "user", "info", "x"], env=env)
    assert seen["proxy"] == "socks5://u:secret@1.2.3.4:1080"
    runner.invoke(app, ["--json", "--account", "news", "--proxy", "http://cli:1", "user", "info", "x"], env=env)
    assert seen["proxy"] == "http://cli:1"  # explicit --proxy wins


def test_unregistered_account_with_creds_is_seeded(env, secrets, tmp_path):
    secrets.set("default", "private_session", "{}")  # e.g. created by an older clinstagram
    rows = _json(runner.invoke(app, ["--json", "account", "list"], env=env))["data"]
    assert rows == [{"name": "default", "username": None, "backends": ["private"], "proxy": None, "is_default": True}]
    assert load_config(tmp_path).accounts["default"].backends == ["private"]


def test_routing_uses_registry_not_keychain(env, secrets, tmp_path):
    _login(env, "news", "arn_news")
    secrets.has_backend = MagicMock(side_effect=AssertionError("keychain probed"))
    r = runner.invoke(app, ["--json", "--account", "news", "--dry-run", "user", "info", "x"], env=env)
    assert r.exit_code == 0, r.output


def test_two_accounts_get_distinct_device_ids():
    from instagrapi import Client

    from clinstagram.auth.private_login import LoginConfig, _configure_client

    ids = []
    for user in ("arn_news", "arn_lab"):
        cl = Client()
        _configure_client(cl, LoginConfig(username=user, password="x"))
        s = cl.get_settings()
        ids.append((s["uuids"]["uuid"], s["uuids"]["phone_id"], s["device_settings"].get("android_device_id")
                    or s["uuids"].get("android_device_id")))
    assert ids[0][0] != ids[1][0] and ids[0][1] != ids[1][1]
