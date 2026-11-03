"""Write-safety guard: ledger, caps, cooldowns, the private-backend hook and the CLI."""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from clinstagram.backends.private import PrivateAPIError, PrivateBackend
from clinstagram.cli import app
from clinstagram.config import RateLimits
from clinstagram.safety import (
    DAY,
    HOUR,
    IGNORE_COOLDOWN_ENV,
    POLICY_EXCEPTION_TYPES,
    SafetyBlocked,
    SafetyGuard,
    classify_block,
    guard_from_click_context,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(IGNORE_COOLDOWN_ENV, raising=False)


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _guard(tmp_path, clock=None, **caps) -> SafetyGuard:
    return SafetyGuard("acct", RateLimits(**caps), tmp_path, clock=clock or Clock())


# Fake instagrapi exceptions: classification is by type name, like _dispatch's.
class FeedbackRequired(Exception):
    pass


class ChallengeRequired(Exception):
    pass


class ClientThrottledError(Exception):
    pass


class PleaseWaitFewMinutes(Exception):
    pass


# ── Block classification ───────────────────────────────────────────


@pytest.mark.parametrize("exc,kind", [
    (FeedbackRequired("feedback_required"), "feedback"),
    (Exception("Error: feedback_required, We restrict certain activity"), "feedback"),
    (ChallengeRequired("challenge_required"), "challenge"),
    (Exception("checkpoint_required"), "challenge"),
    (Exception("Action Blocked: try again later"), "spam"),
    (Exception("This looks like spam"), "spam"),
    (ClientThrottledError("429 Client Error: Too Many Requests"), "rate"),
    (PleaseWaitFewMinutes("Please wait a few minutes before you try again."), "rate"),
    (Exception("HTTP 429"), "rate"),
    (Exception("Media not found"), None),
    (ValueError("Nothing to change"), None),
])
def test_classify_block(exc, kind):
    assert classify_block(exc) == kind


def test_classify_walks_the_wrapped_chain():
    try:
        try:
            raise FeedbackRequired("blocked")
        except FeedbackRequired as inner:
            raise PrivateAPIError(f"dm_send failed: {inner}")
    except PrivateAPIError as outer:
        assert classify_block(outer) == "feedback"


def test_safety_blocked_is_never_a_block_signal():
    assert classify_block(SafetyBlocked("paused after spam 429", reason="cooldown")) is None
    assert "SafetyBlocked" in POLICY_EXCEPTION_TYPES


# ── Caps ───────────────────────────────────────────────────────────


def test_dm_cap_uses_config_and_rolls_over(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock, private_dm_per_hour=2)
    g.begin("dm")
    clock.t += 60
    g.begin("dm")
    with pytest.raises(SafetyBlocked) as ei:
        g.begin("dm")
    assert ei.value.reason == "cap"
    assert "private_dm_per_hour" in str(ei.value)
    assert ei.value.retry_after == HOUR - 60 + 1
    clock.t += HOUR - 59  # first DM left the window
    g.begin("dm")


def test_cap_is_per_action_and_negative_disables(tmp_path):
    g = _guard(tmp_path, private_likes_per_hour=1, private_follows_per_day=-1)
    g.begin("like")
    with pytest.raises(SafetyBlocked):
        g.begin("like")
    for _ in range(30):
        g.begin("follow")
    g.begin("moderation")  # uncapped


def test_zero_cap_blocks_everything(tmp_path):
    g = _guard(tmp_path, private_posts_per_day=0)
    with pytest.raises(SafetyBlocked) as ei:
        g.begin("post")
    assert ei.value.retry_after == DAY + 1


def test_new_cap_defaults():
    rl = RateLimits()
    assert rl.private_profile_edits_per_day == 1
    assert rl.private_username_change_days == 14
    assert rl.private_posts_per_day == 3
    assert rl.private_stories_per_day == 8
    assert rl.private_comments_per_hour == 5


def test_profile_edits_one_per_day(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock)
    g.begin("profile_edit")
    clock.t += 23 * HOUR
    with pytest.raises(SafetyBlocked) as ei:
        g.begin("profile_edit")
    assert "private_profile_edits_per_day" in str(ei.value)
    clock.t += HOUR
    g.begin("profile_edit")


def test_username_once_per_14_days(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock, private_profile_edits_per_day=-1)
    g.begin("profile_edit", username_change=True)
    clock.t += 2 * DAY
    g.begin("profile_edit")  # bio edit is fine
    with pytest.raises(SafetyBlocked) as ei:
        g.begin("profile_edit", username_change=True)
    assert ei.value.reason == "username"
    assert ei.value.retry_after == 12 * DAY + 1
    clock.t += 12 * DAY
    g.begin("profile_edit", username_change=True)


def test_release_frees_the_slot(tmp_path):
    g = _guard(tmp_path, private_dm_per_hour=1)
    r = g.begin("dm")
    g.release(r)
    g.begin("dm")


def test_ledger_persists_across_guard_instances(tmp_path):
    clock = Clock()
    _guard(tmp_path, clock, private_dm_per_hour=1).begin("dm")
    with pytest.raises(SafetyBlocked):
        _guard(tmp_path, clock, private_dm_per_hour=1).begin("dm")
    other = SafetyGuard("other", RateLimits(private_dm_per_hour=1), tmp_path, clock=clock)
    other.begin("dm")  # per account


def test_corrupt_ledger_is_set_aside(tmp_path):
    g = _guard(tmp_path)
    g.path.parent.mkdir(parents=True)
    g.path.write_text("{not json")
    g.begin("dm")
    assert list(g.path.parent.glob("safety.corrupt-*.json"))


def _take_dm_slot(config_dir: str) -> bool:
    from pathlib import Path

    g = SafetyGuard("acct", RateLimits(private_dm_per_hour=5), Path(config_dir))
    try:
        g.begin("dm")
        return True
    except SafetyBlocked:
        return False


def test_cap_holds_across_processes(tmp_path):
    with ProcessPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_take_dm_slot, [str(tmp_path)] * 24))
    assert results.count(True) == 5


# ── Cooldowns ──────────────────────────────────────────────────────


def test_feedback_cooldown_blocks_all_writes_for_24h(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock)
    cd = g.record_block(FeedbackRequired("feedback_required"), "dm_send")
    assert cd["kind"] == "feedback" and cd["until"] == clock.t + DAY
    for action in ("post", "like", "moderation"):
        with pytest.raises(SafetyBlocked) as ei:
            g.begin(action)
        assert ei.value.reason == "cooldown"
        assert "dm_send" in str(ei.value) and "feedback" in str(ei.value)
    clock.t += DAY
    g.begin("post")


def test_plain_429_cools_down_for_an_hour(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock)
    g.record_block(ClientThrottledError("429"), "like_post")
    with pytest.raises(SafetyBlocked) as ei:
        g.begin("dm")
    assert ei.value.retry_after == HOUR + 1
    clock.t += HOUR
    g.begin("dm")


def test_a_later_429_does_not_shorten_a_24h_cooldown(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock)
    g.record_block(ChallengeRequired("challenge_required"), "post_photo")
    clock.t += 60
    g.record_block(ClientThrottledError("429"), "dm_inbox")
    st = g.status()
    assert st["cooldown"]["kind"] == "challenge"
    assert g.record_block(ValueError("bad input")) is None


def test_ignore_cooldown_env_lets_owner_through(tmp_path, monkeypatch, capsys):
    g = _guard(tmp_path)
    g.record_block(FeedbackRequired("x"), "dm_send")
    monkeypatch.setenv(IGNORE_COOLDOWN_ENV, "1")
    g.begin("post")
    assert IGNORE_COOLDOWN_ENV in capsys.readouterr().err
    assert g.status()["caps"][0]["used"] == 1  # still recorded


def test_status_reports_caps_usage_and_cooldown(tmp_path):
    clock = Clock()
    g = _guard(tmp_path, clock, private_dm_per_hour=2, private_follows_per_day=-1)
    g.begin("dm")
    g.begin("dm")
    g.begin("profile_edit", username_change=True)
    g.record_block(PleaseWaitFewMinutes("Please wait a few minutes"), "dm_send")
    st = g.status()
    rows = {r["action"]: r for r in st["caps"]}
    assert rows["dm"]["used"] == 2 and rows["dm"]["remaining"] == 0
    assert rows["dm"]["next_slot_at"]
    assert rows["follow"]["limit"] is None
    assert st["cooldown"]["kind"] == "rate"
    assert st["username_change"]["last_change"]
    assert st["username_change"]["next_allowed"] != "now"


# ── Private backend hook ───────────────────────────────────────────


def _backend(tmp_path, clock=None, **caps):
    cl = MagicMock()
    cl.user_id = 99
    cl.media_pk_from_code.return_value = 111
    g = _guard(tmp_path, clock, **caps)
    return PrivateBackend(client=cl, guard=g), cl, g


def test_backend_writes_count_against_caps(tmp_path):
    b, cl, _ = _backend(tmp_path, private_dm_per_hour=2)
    b.dm_send("12345", "hi")
    b.dm_send_media("12345", str(tmp_path / "a.jpg"))
    with pytest.raises(SafetyBlocked):
        b.dm_send("12345", "third")
    assert cl.direct_send.call_count == 1


def test_backend_feedback_starts_cooldown_and_refuses_up_front(tmp_path):
    b, cl, g = _backend(tmp_path)
    cl.media_like.side_effect = FeedbackRequired("feedback_required")
    with pytest.raises(PrivateAPIError):
        b.like_post("123")
    assert g.status()["cooldown"]["source"] == "like_post"
    with pytest.raises(SafetyBlocked):
        b.post_photo(str(tmp_path / "p.jpg"), caption="x")
    cl.photo_upload.assert_not_called()
    # Reads stay allowed during a cooldown.
    cl.direct_threads.return_value = []
    assert b.dm_inbox() == []


def test_backend_read_429_starts_write_cooldown(tmp_path):
    b, cl, g = _backend(tmp_path)
    cl.direct_threads.side_effect = ClientThrottledError("429 Too Many Requests")
    with pytest.raises(PrivateAPIError):
        b.dm_inbox()
    assert g.status()["cooldown"]["kind"] == "rate"
    with pytest.raises(SafetyBlocked):
        b.dm_send("12345", "hi")


def test_backend_failed_validation_does_not_burn_the_daily_edit(tmp_path):
    b, cl, g = _backend(tmp_path)
    with pytest.raises(ValueError):
        b.profile_edit()
    assert next(r for r in g.status()["caps"] if r["action"] == "profile_edit")["used"] == 0
    b.profile_edit(bio="new bio")
    with pytest.raises(SafetyBlocked):
        b.profile_picture(str(tmp_path / "pic.jpg"))
    cl.account_change_picture.assert_not_called()


def test_backend_username_change_is_recorded(tmp_path):
    clock = Clock()
    b, cl, g = _backend(tmp_path, clock, private_profile_edits_per_day=-1)
    b.profile_edit(username="new_name")
    clock.t += 3 * DAY
    b.profile_edit(name="New Name")
    with pytest.raises(SafetyBlocked) as ei:
        b.profile_edit("bio", None, None, "another")  # positional username
    assert ei.value.reason == "username"


def test_backend_without_guard_is_unchecked(tmp_path):
    cl = MagicMock()
    b = PrivateBackend(client=cl)  # no CLI context -> no guard
    assert b._safety is None
    for _ in range(5):
        b.profile_edit(bio="x")
    assert guard_from_click_context() is None


# ── CLI ────────────────────────────────────────────────────────────


@pytest.fixture
def private_cli(monkeypatch, tmp_path):
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    cl = MagicMock()
    cl.direct_threads.return_value = []
    # Plain values: a MagicMock result would recurse forever in the JSON encoder.
    cl.account_edit.return_value = SimpleNamespace(
        pk=1, username="me", full_name="Me", biography="x", external_url=None,
        profile_pic_url=None, is_private=False, is_verified=False, is_business=True,
    )
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    # A real PrivateBackend built inside the command, so it picks the guard up
    # from the CLI context exactly as _dispatch's own construction does.
    monkeypatch.setattr(
        "clinstagram.commands._dispatch._instantiate_backend",
        lambda ctx, name, feature=None: PrivateBackend(client=cl),
    )
    monkeypatch.setattr("clinstagram.commands._dispatch._apply_jitter_delay", lambda ctx, f: None)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    runner.invoke(app, ["config", "mode", "private-enabled"], env=env)

    def run(*args):
        return runner.invoke(app, ["--json", *args], env=env)

    run.client = cl
    run.guard = SafetyGuard("default", RateLimits(), tmp_path)
    return run


def _last_json(output: str) -> dict:
    return json.loads([ln for ln in output.splitlines() if ln.startswith("{")][-1])


def test_cli_cooldown_maps_to_policy_blocked(private_cli):
    private_cli.guard.record_block(FeedbackRequired("feedback_required"), "dm_send")
    result = private_cli("profile", "edit", "--bio", "x")
    assert result.exit_code == 6, result.output
    env = _last_json(result.output)
    assert env["exit_code"] == 6
    assert "paused until" in env["error"]
    assert env["retry_after"] > 23 * HOUR
    assert IGNORE_COOLDOWN_ENV in env["remediation"]
    private_cli.client.account_edit.assert_not_called()
    # Reads still go through.
    assert private_cli("dm", "inbox").exit_code == 0


def test_cli_cap_maps_to_policy_blocked(private_cli):
    assert private_cli("profile", "edit", "--bio", "one").exit_code == 0
    result = private_cli("profile", "edit", "--bio", "two")
    assert result.exit_code == 6, result.output
    assert "private_profile_edits_per_day" in _last_json(result.output)["error"]
    assert private_cli.client.account_edit.call_count == 1


def test_cli_block_from_instagram_records_cooldown(private_cli):
    private_cli.client.account_edit.side_effect = FeedbackRequired("feedback_required")
    result = private_cli("profile", "edit", "--bio", "x")
    assert result.exit_code == 3  # still classified by _dispatch as a rate limit
    assert private_cli.guard.status()["cooldown"]["kind"] == "feedback"


def test_cli_safety_status(private_cli):
    private_cli.guard.begin("dm")
    private_cli.guard.record_block(ClientThrottledError("429"), "dm_send")
    result = private_cli("safety", "status")
    assert result.exit_code == 0, result.output
    data = _last_json(result.output)["data"]
    assert data["account"] == "default"
    assert data["cooldown"]["kind"] == "rate"
    assert next(r for r in data["caps"] if r["action"] == "dm")["used"] == 1


def test_cli_safety_status_other_account(private_cli, tmp_path):
    result = runner.invoke(
        app, ["safety", "status", "--account", "other"],
        env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "other" in result.output


_KNOWN_READS = {
    "dm_inbox", "dm_thread", "story_list", "story_viewers", "comments_list",
    "analytics_profile", "analytics_post", "analytics_hashtag", "followers_list",
    "followers_following", "user_info", "user_search", "user_posts", "hashtag_top",
    "hashtag_recent", "media_download", "profile_show", "dm_pending", "activity",
    "analytics_insights", "analytics_account", "media_likers", "user_tagged",
}


def test_every_private_method_is_classified():
    """A new backend method must be added to _WRITE_ACTIONS (or here, if it is a
    read) so a write can't slip past the guard by default."""
    from clinstagram.backends.private import _WRITE_ACTIONS

    public = {n for n, a in vars(PrivateBackend).items() if not n.startswith("_") and callable(a)}
    assert public == set(_WRITE_ACTIONS) | _KNOWN_READS
