"""Channel-operations features: profile, post management, DM requests,
comment moderation, activity, insights and prospecting."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from clinstagram.backends.capabilities import (
    GROWTH_ACTIONS,
    READ_ONLY_FEATURES,
    Feature,
    can_backend_do,
)
from clinstagram.backends.graph import GraphBackend
from clinstagram.backends.private import PrivateAPIError, PrivateBackend, _activity_to_dict
from clinstagram.cli import app

runner = CliRunner()


# ── Capability classification ───────────────────────────────────────


def test_reads_are_read_only():
    for f in (Feature.PROFILE_SHOW, Feature.ACTIVITY, Feature.ANALYTICS_INSIGHTS,
              Feature.ANALYTICS_ACCOUNT, Feature.MEDIA_LIKERS, Feature.USER_TAGGED,
              Feature.DM_PENDING):
        assert f in READ_ONLY_FEATURES


def test_writes_are_not_read_only_and_comment_likes_are_growth():
    for f in (Feature.PROFILE_EDIT, Feature.PROFILE_PICTURE, Feature.POST_DELETE,
              Feature.POST_EDIT, Feature.DM_APPROVE, Feature.DM_SEEN,
              Feature.COMMENTS_PIN, Feature.COMMENTS_UNPIN):
        assert f not in READ_ONLY_FEATURES
        assert f not in GROWTH_ACTIONS
    assert {Feature.COMMENTS_LIKE, Feature.COMMENTS_UNLIKE} <= GROWTH_ACTIONS


def test_graph_supports_only_official_endpoints():
    for b in ("graph_ig", "graph_fb"):
        assert can_backend_do(b, Feature.PROFILE_SHOW)
        assert can_backend_do(b, Feature.ANALYTICS_INSIGHTS)
        assert can_backend_do(b, Feature.ANALYTICS_ACCOUNT)
        for f in (Feature.PROFILE_EDIT, Feature.POST_DELETE, Feature.DM_APPROVE,
                  Feature.COMMENTS_PIN, Feature.ACTIVITY, Feature.MEDIA_LIKERS):
            assert not can_backend_do(b, f)


# ── Private backend ─────────────────────────────────────────────────


def _private():
    cl = MagicMock()
    cl.user_id = 99
    cl.media_pk_from_code.return_value = 111
    cl.media_id.side_effect = lambda pk: f"{pk}_99"
    return PrivateBackend(client=cl), cl


def _account(**kw):
    acc = MagicMock()
    acc.pk = 99
    acc.username = "me"
    acc.full_name = "Me"
    acc.biography = "bio"
    acc.external_url = "https://x.test"
    acc.profile_pic_url = "https://pic.test/a.jpg"
    acc.is_private = False
    acc.is_verified = False
    acc.is_business = False
    acc.email = "secret@example.com"
    acc.phone_number = "+100"
    for k, v in kw.items():
        setattr(acc, k, v)
    return acc


class TestPrivateProfile:
    def test_show_merges_counts_and_hides_contact_details(self):
        b, cl = _private()
        cl.account_info.return_value = _account()
        info = MagicMock(follower_count=5, following_count=6, media_count=7, category_name="News")
        cl.user_info.return_value = info
        out = b.profile_show()
        assert out["website"] == "https://x.test"
        assert (out["followers_count"], out["media_count"], out["category"]) == (5, 7, "News")
        assert "email" not in out and "phone_number" not in out
        assert "secret@example.com" not in json.dumps(out)

    def test_edit_sends_only_given_fields(self):
        b, cl = _private()
        cl.account_edit.return_value = _account(biography="new")
        applied = _account(biography="new")
        applied.external_url = "https://n.test"
        cl.account_info.return_value = applied  # re-read confirms the website stuck
        out = b.profile_edit(bio="new", website="https://n.test")
        cl.account_edit.assert_called_once_with(biography="new", external_url="https://n.test")
        assert out["updated"] == ["biography", "external_url"]

    def test_edit_clearing_bio_calls_set_biography(self):
        b, cl = _private()
        cl.account_edit.return_value = _account(biography="")
        b.profile_edit(bio="")
        cl.account_set_biography.assert_called_once_with("")

    def test_edit_username_strips_at(self):
        b, cl = _private()
        cl.account_edit.return_value = _account(biography="x")
        out = b.profile_edit(username="@agingresearch.news")
        cl.account_edit.assert_called_once_with(username="agingresearch.news")
        assert out["updated"] == ["username"]

    def test_edit_with_nothing_raises(self):
        b, _ = _private()
        with pytest.raises(ValueError, match="Nothing to change"):
            b.profile_edit()

    def test_picture(self, tmp_path):
        b, cl = _private()
        cl.account_change_picture.return_value = MagicMock(profile_pic_url="https://pic.test/n.jpg")
        out = b.profile_picture(str(tmp_path / "p.jpg"))
        assert out == {"status": "updated", "profile_picture_url": "https://pic.test/n.jpg"}


class TestPrivatePostManagement:
    def test_delete_resolves_shortcode_to_full_media_id(self):
        b, cl = _private()
        cl.media_delete.return_value = True
        out = b.post_delete("DfPQchkG1tg")
        cl.media_delete.assert_called_once_with("111_99")
        assert out == {"media_id": "111", "status": "deleted"}

    def test_delete_unconfirmed_raises(self):
        b, cl = _private()
        cl.media_delete.return_value = False
        with pytest.raises(PrivateAPIError, match="did not confirm"):
            b.post_delete("123")

    def test_edit(self):
        b, cl = _private()
        out = b.post_edit("123", "new caption")
        cl.media_edit.assert_called_once_with("123_99", "new caption")
        assert out["status"] == "edited"


class TestPrivateDMs:
    def test_pending(self):
        b, cl = _private()
        t = MagicMock(id=7, thread_title="stranger", users=[], last_activity_at=None,
                      last_permanent_item=None, unread_count=1)
        cl.direct_pending_inbox.return_value = [t]
        out = b.dm_pending(5)
        cl.direct_pending_inbox.assert_called_once_with(amount=5)
        assert out[0]["thread_id"] == "7"

    def test_approve_and_seen(self):
        b, cl = _private()
        cl.direct_pending_approve.return_value = True
        cl.direct_send_seen.return_value = True
        assert b.dm_approve("7")["status"] == "approved"
        assert b.dm_seen("7")["status"] == "seen"
        cl.direct_pending_approve.assert_called_once_with(7)
        cl.direct_send_seen.assert_called_once_with(7)


class TestPrivateCommentModeration:
    def test_like_unlike_use_comment_pk(self):
        b, cl = _private()
        b.comments_like("123:456")
        b.comments_unlike("123:456")
        cl.comment_like.assert_called_once_with(456)
        cl.comment_unlike.assert_called_once_with(456)

    def test_pin_unpin_use_media_and_comment(self):
        b, cl = _private()
        b.comments_pin("123:456")
        b.comments_unpin("DfPQchkG1tg:456")
        cl.comment_pin.assert_called_once_with("123", 456)
        cl.comment_unpin.assert_called_once_with("111", 456)

    def test_bad_ref_raises(self):
        b, _ = _private()
        with pytest.raises(PrivateAPIError, match="media_id:comment_id"):
            b.comments_pin("456")


class TestPrivateActivity:
    def test_normalizes_rich_text_type_and_timestamp(self):
        story = {
            "story_type": 101,
            "args": {
                "rich_text": "{alice|000000|1|user?id=1|none} liked your post.",
                "profile_id": 1,
                "profile_name": "alice",
                "timestamp": 1700000000.5,
                "media": [{"id": "321_99", "image": "x"}],
            },
        }
        out = _activity_to_dict(story)
        assert out["type"] == "like"
        assert out["text"] == "alice liked your post."
        assert out["user"]["username"] == "alice"
        assert out["media_id"] == "321"
        assert out["timestamp"].startswith("2023-11-14")

    @pytest.mark.parametrize("text,kind", [
        ("bob started following you.", "follow"),
        ("bob mentioned you in a comment: hi", "mention"),
        ("bob commented: great", "comment"),
        ("bob liked your comment: x", "comment_like"),
        ("bob tagged you in a post.", "tag"),
        ("Follow suggestions: a, b", "suggestion"),
        ("Something new", "other"),
    ])
    def test_types(self, text, kind):
        assert _activity_to_dict({"args": {"text": text}})["type"] == kind

    def test_merges_new_and_old_and_limits(self):
        b, cl = _private()
        cl.news_inbox_v1.return_value = {
            "new_stories": [{"args": {"text": "a started following you"}}],
            "old_stories": [{"args": {"text": "b liked your post"}}, {"args": {"text": "c"}}],
        }
        out = b.activity(limit=2)
        cl.news_inbox_v1.assert_called_once_with(mark_as_seen=False)
        assert [o["type"] for o in out] == ["follow", "like"]


class TestPrivateInsights:
    def test_media_insights(self):
        b, cl = _private()
        cl.insights_media.return_value = {"reach_count": 10}
        assert b.analytics_insights("123") == {"media_id": "123", "insights": {"reach_count": 10}}
        cl.insights_media.assert_called_once_with(123)

    def test_media_insights_explains_professional_requirement(self):
        b, cl = _private()
        cl.insights_media.side_effect = RuntimeError("Instagram returned media metadata without inline insights data")
        with pytest.raises(PrivateAPIError, match="professional"):
            b.analytics_insights("123")

    def test_account_personal_skeleton_raises(self):
        b, cl = _private()
        cl.insights_account.return_value = {"status": {"account_type": "PERSONAL_ACCOUNT"}}
        with pytest.raises(PrivateAPIError, match="professional"):
            b.analytics_account()

    def test_account_business_passes_through(self):
        b, cl = _private()
        data = {"status": {"account_type": "BUSINESS"}, "x": 1}
        cl.insights_account.return_value = data
        assert b.analytics_account() == data


def _user(i):
    u = MagicMock()
    u.pk = i
    u.username = f"u{i}"
    return u


class TestPrivateProspecting:
    def test_followers_of_other_user(self):
        b, cl = _private()
        cl.user_info_by_username.return_value = MagicMock(pk=42)
        cl.user_followers.return_value = {str(i): _user(i) for i in range(5)}
        out = b.followers_list(3, user="nasa")
        cl.user_followers.assert_called_once_with(42, amount=3)
        assert len(out) == 3

    def test_followers_default_is_self(self):
        b, cl = _private()
        cl.user_followers.return_value = {}
        b.followers_list(10)
        cl.user_followers.assert_called_once_with(99, amount=10)
        cl.user_info_by_username.assert_not_called()

    def test_following_of_other_user(self):
        b, cl = _private()
        cl.user_info_by_username.return_value = MagicMock(pk=42)
        cl.user_following.return_value = {"1": _user(1)}
        assert b.followers_following(5, user="nasa")[0]["username"] == "u1"
        cl.user_following.assert_called_once_with(42, amount=5)

    def test_media_likers(self):
        b, cl = _private()
        cl.media_likers.return_value = [_user(i) for i in range(4)]
        out = b.media_likers("DfPQchkG1tg", limit=2)
        cl.media_likers.assert_called_once_with("111")
        assert [u["username"] for u in out] == ["u0", "u1"]

    def test_user_tagged(self):
        b, cl = _private()
        cl.user_info_by_username.return_value = MagicMock(pk=42)
        cl.usertag_medias.return_value = []
        assert b.user_tagged("nasa", limit=3) == []
        cl.usertag_medias.assert_called_once_with(42, amount=3)


# ── Graph backend ───────────────────────────────────────────────────


def _graph(responses):
    import httpx

    def handler(request):
        path = request.url.path
        for key, body in responses.items():
            if path.endswith(key):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    g = GraphBackend(token="t", login_type="ig", client=client)
    g._me_id_cache = "555"
    return g


class TestGraphChannelOps:
    def test_profile_show(self):
        g = _graph({"/555": {"id": "555", "username": "news", "website": "https://w.test",
                             "followers_count": 10}})
        out = g.profile_show()
        assert out["website"] == "https://w.test"
        assert out["followers_count"] == 10

    def test_media_insights_flattens(self):
        g = _graph({"/777/insights": {"data": [
            {"name": "reach", "values": [{"value": 12}]},
            {"name": "saved", "total_value": {"value": 3}},
        ]}})
        assert g.analytics_insights("777") == {"media_id": "777", "insights": {"reach": 12, "saved": 3}}

    def test_media_insights_resolves_own_shortcode(self):
        g = _graph({
            "/555/media": {"data": [{"id": "901", "shortcode": "DfPQchkG1tg",
                                     "permalink": "https://www.instagram.com/p/DfPQchkG1tg/"}]},
            "/901/insights": {"data": [{"name": "reach", "values": [{"value": 5}]}]},
        })
        assert g.analytics_insights("https://www.instagram.com/p/DfPQchkG1tg/")["media_id"] == "901"

    def test_media_ref_outside_own_posts_points_to_private(self):
        g = _graph({"/555/media": {"data": []}})
        with pytest.raises(ValueError, match="--backend private"):
            g.analytics_insights("SomeoneElse1")

    def test_account_insights(self):
        g = _graph({"/555/insights": {"data": [{"name": "reach", "total_value": {"value": 99}}]}})
        assert g.analytics_account()["insights"] == {"reach": 99}

    def test_unsupported_raise_not_implemented(self):
        g = _graph({})
        with pytest.raises(NotImplementedError, match="--backend private"):
            g.post_delete("1")


# ── Commands ────────────────────────────────────────────────────────


@pytest.fixture
def private_cli(monkeypatch, tmp_path):
    """Run commands against a mocked private backend in private-enabled mode."""
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    backend = MagicMock()
    backend.name = "private"
    # JSON-serialisable default for every backend method the commands call
    for m in ("profile_show", "profile_edit", "profile_picture", "post_delete", "post_edit",
              "dm_pending", "dm_approve", "dm_seen", "comments_like", "comments_unlike",
              "comments_pin", "comments_unpin", "activity", "analytics_insights",
              "analytics_account", "media_likers", "followers_list", "followers_following",
              "user_tagged"):
        getattr(backend, m).return_value = {"ok": True}
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr(
        "clinstagram.commands._dispatch._instantiate_backend",
        lambda ctx, name, feature=None: backend,
    )
    monkeypatch.setattr("clinstagram.commands._dispatch._apply_jitter_delay", lambda ctx, f: None)
    monkeypatch.setattr("clinstagram.commands._dispatch.stage", lambda src, name: src)
    monkeypatch.setattr("clinstagram.commands.profile.stage", lambda src, name: src)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    runner.invoke(app, ["config", "mode", "private-enabled"], env=env)

    def run(*args):
        flags = [a for a in args if a == "--enable-growth-actions"]
        rest = [a for a in args if a != "--enable-growth-actions"]
        result = runner.invoke(app, ["--json", *flags, *rest], env=env)
        return result, backend

    return run


@pytest.mark.parametrize("args,method,call", [
    (["profile", "show"], "profile_show", ()),
    (["profile", "edit", "--bio", "Aging research daily"], "profile_edit", None),
    (["profile", "picture", "pic.jpg"], "profile_picture", ("pic.jpg",)),
    (["post", "delete", "DfPQchkG1tg"], "post_delete", ("DfPQchkG1tg",)),
    (["post", "edit", "123", "--caption", "new"], "post_edit", ("123", "new")),
    (["dm", "pending", "--limit", "5"], "dm_pending", (5,)),
    (["dm", "approve", "77"], "dm_approve", ("77",)),
    (["dm", "seen", "77"], "dm_seen", ("77",)),
    (["comments", "pin", "1:2"], "comments_pin", ("1:2",)),
    (["comments", "unpin", "1:2"], "comments_unpin", ("1:2",)),
    (["activity", "--limit", "5"], "activity", (5,)),
    (["analytics", "insights", "123"], "analytics_insights", ("123",)),
    (["analytics", "account"], "analytics_account", ()),
    (["media", "likers", "123", "--limit", "4"], "media_likers", ("123", 4)),
])
def test_commands_dispatch(private_cli, args, method, call):
    result, backend = private_cli(*args)
    assert result.exit_code == 0, result.output
    mock = getattr(backend, method)
    assert mock.called
    if call is not None:
        assert mock.call_args.args == call


def test_profile_edit_passes_only_given_fields(private_cli):
    result, backend = private_cli("profile", "edit", "--website", "https://w.test")
    assert result.exit_code == 0, result.output
    backend.profile_edit.assert_called_with(bio=None, name=None, website="https://w.test", username=None)


def test_followers_user_option(private_cli):
    result, backend = private_cli("followers", "list", "--user", "@nasa", "--limit", "3")
    assert result.exit_code == 0, result.output
    backend.followers_list.assert_called_with(3, user="nasa")
    result, backend = private_cli("followers", "following", "-u", "nasa")
    backend.followers_following.assert_called_with(100, user="nasa")


def test_user_tagged(private_cli):
    result, backend = private_cli("user", "tagged", "@nasa", "--limit", "2")
    assert result.exit_code == 0, result.output
    backend.user_tagged.assert_called_with("nasa", limit=2)


def test_comment_like_requires_growth_flag(private_cli):
    result, backend = private_cli("comments", "like", "1:2")
    assert result.exit_code == 6
    backend.comments_like.assert_not_called()


def test_comment_like_with_growth_flag(private_cli):
    result, backend = private_cli("--enable-growth-actions", "comments", "like", "1:2")
    assert result.exit_code == 0, result.output
    backend.comments_like.assert_called_once_with("1:2")


def test_writes_blocked_in_hybrid_safe(monkeypatch, tmp_path):
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    env = {"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"}
    result = runner.invoke(app, ["--json", "post", "delete", "123"], env=env)
    assert result.exit_code == 6
    assert "private-enabled" in json.loads(result.output)["remediation"]


def test_agent_info_lists_new_commands():
    data = json.loads(runner.invoke(app, ["agent-info"]).output)
    for name in ("profile show", "profile edit", "profile picture", "post delete", "post edit",
                 "dm pending", "dm approve", "dm seen", "comments like", "comments unlike",
                 "comments pin", "comments unpin", "activity", "analytics insights",
                 "analytics account", "media likers", "user tagged"):
        assert name in data["commands"], name
