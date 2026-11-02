"""Verify both backends implement the Backend ABC."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import httpx
import pytest

from clinstagram.backends.base import Backend
from clinstagram.backends.graph import GraphBackend
from clinstagram.backends.private import PrivateBackend


def _abstract_method_names() -> set[str]:
    """Return the names of all abstract methods (and abstract properties) on Backend."""
    names = set()
    for name, _ in inspect.getmembers(Backend):
        if name.startswith("_"):
            continue
        # Check if it's declared abstract
        obj = getattr(Backend, name, None)
        if obj is None:
            continue
        # abstractmethod or abstractproperty (via @property @abstractmethod)
        if getattr(obj, "__isabstractmethod__", False):
            names.add(name)
        # Handle @property wrapping @abstractmethod
        if isinstance(obj, property) and getattr(obj.fget, "__isabstractmethod__", False):
            names.add(name)
    return names


class TestGraphBackendImplementsInterface:
    def test_is_subclass(self):
        assert issubclass(GraphBackend, Backend)

    def test_can_instantiate(self):
        client = httpx.Client()
        try:
            backend = GraphBackend(token="test-token", login_type="ig", client=client)
            assert isinstance(backend, Backend)
        finally:
            client.close()

    def test_all_abstract_methods_implemented(self):
        abstract = _abstract_method_names()
        assert abstract, "Expected at least one abstract method on Backend"
        for method_name in abstract:
            impl = getattr(GraphBackend, method_name, None)
            assert impl is not None, f"GraphBackend missing: {method_name}"
            if isinstance(impl, property):
                assert not getattr(impl.fget, "__isabstractmethod__", False), (
                    f"GraphBackend.{method_name} is still abstract"
                )
            else:
                assert not getattr(impl, "__isabstractmethod__", False), (
                    f"GraphBackend.{method_name} is still abstract"
                )

    def test_name_property(self):
        client = httpx.Client()
        try:
            ig = GraphBackend(token="t", login_type="ig", client=client)
            assert ig.name == "graph_ig"
        finally:
            client.close()

        client2 = httpx.Client()
        try:
            fb = GraphBackend(token="t", login_type="fb", client=client2)
            assert fb.name == "graph_fb"
        finally:
            client2.close()

    def test_invalid_login_type_raises(self):
        client = httpx.Client()
        try:
            with pytest.raises(ValueError, match="login_type must be"):
                GraphBackend(token="t", login_type="bad", client=client)
        finally:
            client.close()


class TestPrivateBackendImplementsInterface:
    def test_is_subclass(self):
        assert issubclass(PrivateBackend, Backend)

    def test_can_instantiate(self):
        mock_client = MagicMock()
        backend = PrivateBackend(client=mock_client)
        assert isinstance(backend, Backend)

    def test_all_abstract_methods_implemented(self):
        abstract = _abstract_method_names()
        for method_name in abstract:
            impl = getattr(PrivateBackend, method_name, None)
            assert impl is not None, f"PrivateBackend missing: {method_name}"
            if isinstance(impl, property):
                assert not getattr(impl.fget, "__isabstractmethod__", False), (
                    f"PrivateBackend.{method_name} is still abstract"
                )
            else:
                assert not getattr(impl, "__isabstractmethod__", False), (
                    f"PrivateBackend.{method_name} is still abstract"
                )

    def test_name_property(self):
        mock_client = MagicMock()
        backend = PrivateBackend(client=mock_client)
        assert backend.name == "private"


class TestPrivateBackendAnalyticsProfile:
    """Regression: analytics_profile must use user_info() for counts, not account_info()."""

    def test_analytics_profile_uses_user_info_for_counts(self):
        mock_client = MagicMock()
        account = MagicMock()
        account.pk = 12345
        mock_client.account_info.return_value = account

        user = MagicMock()
        user.username = "testuser"
        user.full_name = "Test User"
        user.follower_count = 1000
        user.following_count = 500
        user.media_count = 50
        user.biography = "Bio"
        mock_client.user_info.return_value = user

        backend = PrivateBackend(client=mock_client)
        result = backend.analytics_profile()

        mock_client.account_info.assert_called_once()
        mock_client.user_info.assert_called_once_with(12345)
        assert result["followers_count"] == 1000
        assert result["following_count"] == 500
        assert result["media_count"] == 50


class TestPrivateBackendUserPosts:
    """Test user_posts calls user_medias with correct args."""

    def test_user_posts_returns_media_list(self):
        mock_client = MagicMock()
        user_info = MagicMock()
        user_info.pk = "99999"
        mock_client.user_info_by_username.return_value = user_info

        media1 = MagicMock()
        media1.pk = 111
        media1.code = "ABC"
        media1.media_type = 1
        media1.caption_text = "Test caption"
        media1.taken_at = None
        media1.like_count = 10
        media1.comment_count = 5
        mock_client.user_medias.return_value = [media1]

        backend = PrivateBackend(client=mock_client)
        result = backend.user_posts("testuser", limit=10)

        mock_client.user_info_by_username.assert_called_once_with("testuser")
        mock_client.user_medias.assert_called_once_with(99999, amount=10)
        assert len(result) == 1
        assert result[0]["code"] == "ABC"
        assert result[0]["like_count"] == 10


class TestPrivateBackendLikePost:
    def test_like_post_calls_media_like(self):
        mock_client = MagicMock()
        backend = PrivateBackend(client=mock_client)
        result = backend.like_post("3141592653")
        mock_client.media_like.assert_called_once_with("3141592653")
        assert result["status"] == "liked"

    def test_unlike_post_calls_media_unlike(self):
        mock_client = MagicMock()
        backend = PrivateBackend(client=mock_client)
        result = backend.unlike_post("3141592653")
        mock_client.media_unlike.assert_called_once_with("3141592653")
        assert result["status"] == "unliked"


class TestPrivateBackendCommentsAdd:
    def test_comments_add_calls_media_comment(self):
        mock_client = MagicMock()
        comment = MagicMock()
        comment.pk = 555
        comment.text = "great post"
        comment.user = None
        comment.created_at_utc = None
        mock_client.media_comment.return_value = comment
        backend = PrivateBackend(client=mock_client)
        result = backend.comments_add("3141592653", "great post")
        mock_client.media_comment.assert_called_once_with("3141592653", "great post")
        assert result["text"] == "great post"


class TestPrivateBackendCommentsList:
    def test_comments_list_returns_composite_ids(self):
        mock_client = MagicMock()
        comment = MagicMock()
        comment.pk = 123
        comment.text = "hello"
        comment.user = None
        comment.created_at_utc = None
        mock_client.media_comments.return_value = [comment]
        
        backend = PrivateBackend(client=mock_client)
        result = backend.comments_list("2718281828", limit=10)
        
        mock_client.media_comments.assert_called_once_with("2718281828", amount=10)
        assert len(result) == 1
        assert result[0]["id"] == "2718281828:123"


class TestPrivateBackendCommentsReply:
    def test_comments_reply_uses_media_comment_with_replied_to(self):
        mock_client = MagicMock()
        comment = MagicMock()
        comment.pk = 456
        comment.text = "reply text"
        comment.user = None
        comment.created_at_utc = None
        mock_client.media_comment.return_value = comment

        backend = PrivateBackend(client=mock_client)
        result = backend.comments_reply("2718281828:123", "reply text")

        mock_client.media_comment.assert_called_once_with(
            "2718281828", "reply text", replied_to_comment_id=123
        )
        assert result["text"] == "reply text"



class TestPrivateBackendHashtagBrowsing:
    def test_hashtag_top_calls_hashtag_medias_top(self):
        mock_client = MagicMock()
        media = MagicMock()
        media.pk = 777
        media.code = "XYZ"
        media.media_type = 1
        media.caption_text = "tagged"
        media.taken_at = None
        media.like_count = 50
        media.comment_count = 3
        mock_client.hashtag_medias_top.return_value = [media]
        backend = PrivateBackend(client=mock_client)
        result = backend.hashtag_top("longevity", limit=10)
        mock_client.hashtag_medias_top.assert_called_once_with("longevity", amount=10)
        assert len(result) == 1
        assert result[0]["code"] == "XYZ"

    def test_hashtag_recent_calls_hashtag_medias_recent(self):
        mock_client = MagicMock()
        mock_client.hashtag_medias_recent.return_value = []
        backend = PrivateBackend(client=mock_client)
        result = backend.hashtag_recent("biotech", limit=5)
        mock_client.hashtag_medias_recent.assert_called_once_with("biotech", amount=5)
        assert result == []


class TestBackendCannotBeInstantiated:
    def test_abstract_class_raises(self):
        with pytest.raises(TypeError):
            Backend()  # type: ignore[abstract]


class TestPrivateBackendMediaRefResolution:
    """Commands accept numeric ids, "pk_userid" ids, shortcodes, and post URLs."""

    def _backend(self):
        mock_client = MagicMock()
        mock_client.media_pk_from_code.return_value = 4021505321193921376
        mock_client.media_pk_from_url.return_value = 4021505321193921376
        return PrivateBackend(client=mock_client), mock_client

    def test_numeric_id_passes_through(self):
        backend, cl = self._backend()
        assert backend._media_pk("3141592653") == "3141592653"
        cl.media_pk_from_code.assert_not_called()

    def test_composite_id_keeps_pk(self):
        backend, _ = self._backend()
        assert backend._media_pk("3141592653_25025320") == "3141592653"

    def test_shortcode_decoded(self):
        backend, cl = self._backend()
        assert backend._media_pk("DfPQchkG1tg") == "4021505321193921376"
        cl.media_pk_from_code.assert_called_once_with("DfPQchkG1tg")

    def test_url_decoded(self):
        backend, cl = self._backend()
        url = "https://www.instagram.com/p/DfPQchkG1tg/"
        assert backend._media_pk(url) == "4021505321193921376"
        cl.media_pk_from_url.assert_called_once_with(url)

    def test_comments_list_accepts_shortcode(self):
        backend, cl = self._backend()
        cl.media_comments.return_value = []
        backend.comments_list("DfPQchkG1tg", limit=5)
        cl.media_comments.assert_called_once_with("4021505321193921376", amount=5)

    def test_like_accepts_url(self):
        backend, cl = self._backend()
        result = backend.like_post("https://www.instagram.com/reel/DfPQchkG1tg/")
        cl.media_like.assert_called_once_with("4021505321193921376")
        assert result["media_id"] == "4021505321193921376"


class TestPrivateBackendUserPostsWithheld:
    def _backend(self, media_count):
        cl = MagicMock()
        info = MagicMock(); info.pk = 42; info.media_count = media_count
        cl.user_info_by_username.return_value = info
        cl.user_medias.return_value = []
        return PrivateBackend(client=cl)

    def test_empty_feed_for_profile_with_posts_raises(self):
        import pytest
        from clinstagram.backends.private import PrivateAPIError
        with pytest.raises(PrivateAPIError, match="0 of 20 posts"):
            self._backend(20).user_posts("nasa", limit=3)

    def test_empty_feed_for_profile_without_posts_is_empty(self):
        assert self._backend(0).user_posts("newbie", limit=3) == []
