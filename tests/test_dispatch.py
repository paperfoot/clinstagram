"""Tests for the dispatch layer and all command modules."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clinstagram.cli import app

runner = CliRunner()


def _setup_all_backends():
    """Pre-populate secrets for all three backends."""
    from clinstagram.auth.keychain import BACKEND_TOKEN_MAP, SecretsStore

    secrets = SecretsStore(backend="memory")
    for backend_name, token_key in BACKEND_TOKEN_MAP.items():
        secrets.set("default", token_key, "fake-token-12345")
    return secrets


# ── Dispatch error handling ──────────────────────────────────────────


class TestDispatchNoBackend:
    """When no backends are configured, commands should fail gracefully."""

    def test_post_photo_no_backend(self, tmp_path):
        result = runner.invoke(
            app, ["--json", "post", "photo", "img.jpg"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 7  # CAPABILITY_UNAVAILABLE

    def test_dm_inbox_no_backend(self, tmp_path):
        result = runner.invoke(
            app, ["--json", "dm", "inbox"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 7

    def test_user_info_no_backend(self, tmp_path):
        result = runner.invoke(
            app, ["--json", "user", "info", "alice"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 7


class TestDispatchDryRun:
    """--dry-run should show routing without executing."""

    def test_dry_run_shows_routing(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        result = runner.invoke(
            app, ["--dry-run", "--json", "post", "photo", "img.jpg"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["dry_run"] is True
        assert data["data"]["feature"] == "post_photo"
        assert data["data"]["backend"] == "graph_ig"
        assert data["backend_used"] == "graph_ig"

    def test_local_media_prefers_private_in_private_enabled_mode(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        runner.invoke(
            app, ["config", "mode", "private-enabled"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app, ["--dry-run", "--json", "post", "photo", "img.jpg"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "private"
        assert data["backend_used"] == "private"

    def test_dry_run_honours_forced_backend(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        result = runner.invoke(
            app, ["--dry-run", "--json", "--backend", "graph_ig", "post", "photo", "https://example.com/img.jpg"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "graph_ig"
        assert data["backend_used"] == "graph_ig"

    def test_dm_send_username_routes_private(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        runner.invoke(
            app, ["config", "mode", "private-enabled"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app, ["--dry-run", "--json", "dm", "send", "@alice", "hi"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "private"

    def test_dm_send_numeric_routes_reply_backend(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        result = runner.invoke(
            app, ["--dry-run", "--json", "dm", "send", "123456789", "hi"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "graph_fb"

    def test_comments_reply_composite_ref_routes_private(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        runner.invoke(
            app, ["config", "mode", "private-enabled"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app,
            ["--dry-run", "--json", "--enable-growth-actions", "comments", "reply", "media123:456", "hello"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "private"

    def test_story_link_prefers_private(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        runner.invoke(
            app, ["config", "mode", "private-enabled"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app,
            ["--dry-run", "--json", "story", "post-photo", "https://example.com/photo.jpg", "--link", "https://example.com"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["backend"] == "private"


class TestDispatchPolicyBlocked:
    """official-only mode should block private-only features."""

    def test_followers_list_official_only(self, tmp_path):
        # Set mode to official-only
        runner.invoke(
            app, ["config", "mode", "official-only"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app, ["--json", "followers", "list"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code in (6, 7)

    def test_forced_private_backend_is_still_blocked(self, monkeypatch, tmp_path):
        secrets = _setup_all_backends()
        monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
        runner.invoke(
            app, ["config", "mode", "official-only"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        result = runner.invoke(
            app, ["--json", "--backend", "private", "dm", "inbox"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 6
        data = json.loads(result.output)
        assert "blocked by compliance mode" in data["error"]


# ── Growth actions gate ──────────────────────────────────────────────


class TestGrowthActionsGate:
    """follow/unfollow require --enable-growth-actions."""

    def test_follow_blocked_without_flag(self, tmp_path):
        result = runner.invoke(
            app, ["--json", "followers", "follow", "alice"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 6  # POLICY_BLOCKED

    def test_unfollow_blocked_without_flag(self, tmp_path):
        result = runner.invoke(
            app, ["--json", "followers", "unfollow", "bob"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 6


# ── Command help text ────────────────────────────────────────────────


class TestCommandHelp:
    """All command groups should show their subcommands in help."""

    def test_post_help(self):
        result = runner.invoke(app, ["post", "--help"])
        assert result.exit_code == 0
        assert "photo" in result.output
        assert "video" in result.output
        assert "reel" in result.output

    def test_dm_help(self):
        result = runner.invoke(app, ["dm", "--help"])
        assert result.exit_code == 0
        assert "inbox" in result.output
        assert "send" in result.output

    def test_story_help(self):
        result = runner.invoke(app, ["story", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "post-photo" in result.output

    def test_comments_help(self):
        result = runner.invoke(app, ["comments", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "reply" in result.output

    def test_analytics_help(self):
        result = runner.invoke(app, ["analytics", "--help"])
        assert result.exit_code == 0
        assert "profile" in result.output
        assert "hashtag" in result.output

    def test_followers_help(self):
        result = runner.invoke(app, ["followers", "--help"])
        assert result.exit_code == 0
        assert "follow" in result.output
        assert "unfollow" in result.output

    def test_user_help(self):
        result = runner.invoke(app, ["user", "--help"])
        assert result.exit_code == 0
        assert "info" in result.output
        assert "search" in result.output

    def test_like_help(self):
        result = runner.invoke(app, ["like", "--help"])
        assert result.exit_code == 0
        assert "post" in result.output
        assert "undo" in result.output

    def test_hashtag_help(self):
        result = runner.invoke(app, ["hashtag", "--help"])
        assert result.exit_code == 0
        assert "top" in result.output
        assert "recent" in result.output

    def test_comments_add_help(self):
        result = runner.invoke(app, ["comments", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output


# ── Mocked backend execution ────────────────────────────────────────


class TestMockedExecution:
    """Test commands with mocked backend instances."""

    def _mock_dispatch(self, monkeypatch, backend_result):
        """Patch _instantiate_backend to return a mock backend."""
        mock_backend = MagicMock()
        mock_backend.name = "graph_ig"
        for method in [
            "post_photo", "post_video", "post_reel",
            "dm_inbox", "dm_thread", "dm_send", "dm_send_media",
            "story_list", "story_post_photo", "story_post_video", "story_viewers",
            "comments_list", "comments_reply", "comments_delete",
            "analytics_profile", "analytics_post", "analytics_hashtag",
            "followers_list", "followers_following", "follow", "unfollow",
            "user_info", "user_search", "user_posts",
            "like_post", "unlike_post", "comments_add",
            "hashtag_top", "hashtag_recent",
        ]:
            getattr(mock_backend, method).return_value = backend_result

        secrets = _setup_all_backends()
        monkeypatch.setattr(
            "clinstagram.commands._dispatch._get_secrets",
            lambda ctx: secrets,
        )
        monkeypatch.setattr(
            "clinstagram.commands._dispatch._instantiate_backend",
            lambda ctx, name, feature=None: mock_backend,
        )
        return mock_backend

    def test_post_photo_success(self, monkeypatch, tmp_path):
        self._mock_dispatch(monkeypatch, {"id": "123", "status": "published"})
        # stage() calls resolve_media which needs a real file for private, or URL for graph
        monkeypatch.setattr("clinstagram.commands._dispatch.resolve_media", lambda src, needs_url: src)
        result = runner.invoke(
            app, ["--json", "post", "photo", "img.jpg", "--caption", "hello"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["id"] == "123"
        assert data["backend_used"] == "graph_ig"

    def test_dm_inbox_success(self, monkeypatch, tmp_path):
        threads = [{"thread_id": "t1", "username": "alice"}]
        self._mock_dispatch(monkeypatch, threads)
        result = runner.invoke(
            app, ["--json", "dm", "inbox"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["thread_id"] == "t1"

    def test_analytics_profile_success(self, monkeypatch, tmp_path):
        profile = {"username": "testuser", "followers_count": 1000}
        self._mock_dispatch(monkeypatch, profile)
        result = runner.invoke(
            app, ["--json", "analytics", "profile"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["followers_count"] == 1000

    def test_user_info_success(self, monkeypatch, tmp_path):
        info = {"username": "alice", "full_name": "Alice Smith", "followers_count": 500}
        self._mock_dispatch(monkeypatch, info)
        result = runner.invoke(
            app, ["--json", "user", "info", "alice"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["username"] == "alice"

    def test_story_list_success(self, monkeypatch, tmp_path):
        stories = [{"id": "s1", "media_type": "photo"}]
        self._mock_dispatch(monkeypatch, stories)
        result = runner.invoke(
            app, ["--json", "story", "list"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["id"] == "s1"

    def test_comments_list_success(self, monkeypatch, tmp_path):
        comments = [{"id": "c1", "text": "great!"}]
        self._mock_dispatch(monkeypatch, comments)
        result = runner.invoke(
            app, ["--json", "comments", "list", "media123"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["text"] == "great!"

    def test_user_search_respects_limit(self, monkeypatch, tmp_path):
        users = [{"username": f"u{i}"} for i in range(5)]
        self._mock_dispatch(monkeypatch, users)
        result = runner.invoke(
            app, ["--json", "user", "search", "longevity", "--limit", "2"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert [u["username"] for u in data["data"]] == ["u0", "u1"]

    def test_followers_list_success(self, monkeypatch, tmp_path):
        followers = [{"username": "fan1"}, {"username": "fan2"}]
        self._mock_dispatch(monkeypatch, followers)
        result = runner.invoke(
            app, ["--json", "followers", "list"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["data"]) == 2

    def test_backend_error_mapped(self, monkeypatch, tmp_path):
        """Backend exception should map to API_ERROR exit code."""
        mock = self._mock_dispatch(monkeypatch, None)
        mock.analytics_profile.side_effect = RuntimeError("something broke")
        result = runner.invoke(
            app, ["--json", "analytics", "profile"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 4  # API_ERROR

    def test_rate_limit_error(self, monkeypatch, tmp_path):
        """Rate limit errors should map to exit code 3."""
        mock = self._mock_dispatch(monkeypatch, None)
        mock.dm_inbox.side_effect = RuntimeError("rate limit exceeded")
        result = runner.invoke(
            app, ["--json", "dm", "inbox"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 3  # RATE_LIMITED

    def test_post_video_success(self, monkeypatch, tmp_path):
        """Video posting should route and return result."""
        self._mock_dispatch(monkeypatch, {"id": "456", "status": "published"})
        monkeypatch.setattr("clinstagram.commands._dispatch.resolve_media", lambda src, needs_url: src)
        result = runner.invoke(
            app, ["--json", "post", "video", "clip.mp4"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["id"] == "456"

    def test_like_post_success(self, monkeypatch, tmp_path):
        self._mock_dispatch(monkeypatch, {"media_id": "m1", "status": "liked"})
        env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
        # like requires private-enabled (private-only write feature)
        runner.invoke(app, ["config", "mode", "private-enabled"], env=env)
        result = runner.invoke(app, ["--enable-growth-actions", "--json", "like", "post", "m1"], env=env)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "liked"

    def test_unlike_post_success(self, monkeypatch, tmp_path):
        self._mock_dispatch(monkeypatch, {"media_id": "m1", "status": "unliked"})
        env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
        runner.invoke(app, ["config", "mode", "private-enabled"], env=env)
        result = runner.invoke(app, ["--enable-growth-actions", "--json", "like", "undo", "m1"], env=env)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "unliked"

    def test_comments_add_success(self, monkeypatch, tmp_path):
        self._mock_dispatch(monkeypatch, {"id": "c99", "text": "nice post!"})
        result = runner.invoke(
            app, ["--enable-growth-actions", "--json", "comments", "add", "media123", "nice post!"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["text"] == "nice post!"

    def test_hashtag_top_success(self, monkeypatch, tmp_path):
        posts = [{"id": "p1", "caption": "tagged"}]
        self._mock_dispatch(monkeypatch, posts)
        result = runner.invoke(
            app, ["--json", "hashtag", "top", "longevity"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["id"] == "p1"

    def test_hashtag_recent_success(self, monkeypatch, tmp_path):
        posts = [{"id": "p2", "caption": "fresh"}]
        self._mock_dispatch(monkeypatch, posts)
        result = runner.invoke(
            app, ["--json", "hashtag", "recent", "biotech"],
            env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"][0]["id"] == "p2"


class TestErrorClassification:
    def test_instagrapi_types_through_wrapping(self):
        from instagrapi.exceptions import ChallengeRequired, LoginRequired, PleaseWaitFewMinutes
        from clinstagram.commands._dispatch import _classify_error

        def wrapped(inner):
            try:
                try:
                    raise inner
                except Exception as e:
                    raise RuntimeError(f"op failed: {e}")
            except RuntimeError as outer:
                return outer

        assert _classify_error(wrapped(LoginRequired("x"))) == "auth"
        assert _classify_error(wrapped(ChallengeRequired("x"))) == "challenge"
        assert _classify_error(wrapped(PleaseWaitFewMinutes("x"))) == "rate"

    def test_graph_codes(self):
        from clinstagram.backends.graph import GraphAPIError
        from clinstagram.commands._dispatch import _classify_error

        assert _classify_error(GraphAPIError(400, "OAuthException", "Error validating access token", code=190)) == "auth"
        assert _classify_error(GraphAPIError(400, "OAuthException", "Application request limit reached", code=4)) == "rate"

    def test_incidental_words_are_not_misclassified(self):
        from clinstagram.commands._dispatch import _classify_error

        assert _classify_error(RuntimeError("withholding feeds from this session; log in elsewhere")) == "api"
        assert _classify_error(RuntimeError("failed to generate thumbnail")) == "api"
        assert _classify_error(RuntimeError("rate limit exceeded")) == "rate"
