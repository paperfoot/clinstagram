"""Website edits are verified; picture removal, story delete and pro conversion."""
import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from clinstagram.backends.private import PrivateAPIError, PrivateBackend
from clinstagram.cli import app


def _acc(**kw):
    a = MagicMock()
    a.pk, a.username, a.full_name, a.biography = 1, "me", "Me", "bio"
    a.external_url, a.profile_pic_url = kw.get("external_url", ""), None
    a.is_private = a.is_verified = False
    a.is_business = kw.get("is_business", False)
    return a


def test_website_dropped_by_instagram_is_an_error():
    cl = MagicMock()
    cl.account_edit.return_value = _acc()
    cl.account_info.return_value = _acc(external_url="")
    with pytest.raises(PrivateAPIError, match="didn't apply the website.*Other fields were updated: biography"):
        PrivateBackend(client=cl).profile_edit(bio="new", website="https://agingresearch.news")


def test_website_applied_is_confirmed_loosely():
    cl = MagicMock()
    cl.account_edit.return_value = _acc()
    cl.account_info.return_value = _acc(external_url="http://www.AgingResearch.news/")
    out = PrivateBackend(client=cl).profile_edit(website="https://agingresearch.news")
    assert out["updated"] == ["external_url"]


def test_bio_only_edit_skips_reread():
    cl = MagicMock()
    cl.account_edit.return_value = _acc()
    PrivateBackend(client=cl).profile_edit(bio="x")
    cl.account_info.assert_not_called()


def test_remove_profile_picture_posts_unsigned():
    cl = MagicMock()
    cl.uuid, cl.user_id = "u-u-i-d", 42
    assert PrivateBackend(client=cl).profile_picture_remove() == {"status": "removed"}
    cl.private_request.assert_called_once_with(
        "accounts/remove_profile_picture/", data={"_uuid": "u-u-i-d", "_uid": 42}, with_signature=False
    )


def test_story_delete_accepts_composite_id():
    cl = MagicMock()
    cl.story_delete.return_value = True
    assert PrivateBackend(client=cl).story_delete("3141_42")["story_id"] == "3141"
    cl.story_delete.assert_called_once_with("3141")


def test_story_delete_unconfirmed_raises():
    cl = MagicMock()
    cl.story_delete.return_value = False
    with pytest.raises(PrivateAPIError, match="did not confirm"):
        PrivateBackend(client=cl).story_delete("3141")


@pytest.mark.parametrize("to,call", [("creator", "account_convert_to_creator"),
                                      ("business", "account_convert_to_business")])
def test_profile_convert(to, call):
    cl = MagicMock()
    getattr(cl, call).return_value = _acc(is_business=True)
    out = PrivateBackend(client=cl).profile_convert(to, category_id="123")
    getattr(cl, call).assert_called_once_with(category_id="123")
    assert out["converted_to"] == to and out["profile"]["is_business"] is True


# ── CLI wiring ──────────────────────────────────────────────────────

def _run(monkeypatch, tmp_path, args, mode="private-enabled"):
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    backend = MagicMock()
    backend.name = "private"
    backend.profile_picture_remove.return_value = {"status": "removed"}
    backend.story_delete.return_value = {"story_id": "1", "status": "deleted"}
    backend.profile_convert.return_value = {"converted_to": "creator"}
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend", lambda ctx, n, f=None: backend)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    runner = CliRunner()
    runner.invoke(app, ["config", "mode", mode], env=env)
    return runner.invoke(app, ["--json", *args], env=env), backend


def test_cli_picture_remove(monkeypatch, tmp_path):
    result, backend = _run(monkeypatch, tmp_path, ["profile", "picture", "--remove"])
    assert result.exit_code == 0, result.output
    backend.profile_picture_remove.assert_called_once()


def test_cli_picture_needs_exactly_one_input(monkeypatch, tmp_path):
    result, _ = _run(monkeypatch, tmp_path, ["profile", "picture"])
    assert result.exit_code == 1


def test_cli_story_delete_and_convert(monkeypatch, tmp_path):
    r1, b = _run(monkeypatch, tmp_path, ["story", "delete", "123"])
    assert r1.exit_code == 0 and b.story_delete.called
    r2, b = _run(monkeypatch, tmp_path, ["profile", "convert", "--to", "creator"])
    assert r2.exit_code == 0
    b.profile_convert.assert_called_once_with("creator", None)


def test_cli_convert_is_a_write_blocked_in_hybrid_safe(monkeypatch, tmp_path):
    result, _ = _run(monkeypatch, tmp_path, ["profile", "convert", "--to", "business"], mode="hybrid-safe")
    assert result.exit_code == 6
