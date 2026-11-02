from __future__ import annotations

from abc import ABC, abstractmethod


class Backend(ABC):
    """Abstract backend interface for Instagram API operations."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def post_photo(
        self, media: str, caption: str = "", location: str = "", tags: list[str] | None = None
    ) -> dict: ...

    @abstractmethod
    def post_video(
        self, media: str, caption: str = "", thumbnail: str = "", location: str = ""
    ) -> dict: ...

    @abstractmethod
    def post_reel(
        self, media: str, caption: str = "", thumbnail: str = "", audio: str = ""
    ) -> dict: ...

    @abstractmethod
    def post_carousel(
        self, media_list: list[str], caption: str = ""
    ) -> dict: ...

    @abstractmethod
    def dm_inbox(self, limit: int = 20, unread_only: bool = False) -> list[dict]: ...

    @abstractmethod
    def dm_thread(self, thread_id: str, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def dm_send(self, user: str, message: str) -> dict: ...

    @abstractmethod
    def dm_send_media(self, user: str, media: str) -> dict: ...

    @abstractmethod
    def story_list(self, user: str = "") -> list[dict]: ...

    @abstractmethod
    def story_post_photo(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict: ...

    @abstractmethod
    def story_post_video(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict: ...

    @abstractmethod
    def story_viewers(self, story_id: str) -> list[dict]: ...

    @abstractmethod
    def comments_list(self, media_id: str, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    def comments_reply(self, comment_id: str, text: str) -> dict: ...

    @abstractmethod
    def comments_delete(self, comment_id: str) -> dict: ...

    @abstractmethod
    def analytics_profile(self) -> dict: ...

    @abstractmethod
    def analytics_post(self, media_id: str) -> dict: ...

    @abstractmethod
    def analytics_hashtag(self, tag: str) -> dict: ...

    @abstractmethod
    def followers_list(self, limit: int = 100, user: str = "") -> list[dict]: ...

    @abstractmethod
    def followers_following(self, limit: int = 100, user: str = "") -> list[dict]: ...

    @abstractmethod
    def follow(self, user: str) -> dict: ...

    @abstractmethod
    def unfollow(self, user: str) -> dict: ...

    @abstractmethod
    def user_info(self, username: str) -> dict: ...

    @abstractmethod
    def user_search(self, query: str) -> list[dict]: ...

    @abstractmethod
    def user_posts(self, username: str, limit: int = 20) -> list[dict]: ...

    # Engagement
    @abstractmethod
    def like_post(self, media_id: str) -> dict: ...

    @abstractmethod
    def unlike_post(self, media_id: str) -> dict: ...

    @abstractmethod
    def comments_add(self, media_id: str, text: str) -> dict: ...

    # Hashtag browsing
    @abstractmethod
    def hashtag_top(self, tag: str, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def hashtag_recent(self, tag: str, limit: int = 20) -> list[dict]: ...

    # Media download
    @abstractmethod
    def media_download(self, media_ref: str, output_dir: str = "") -> dict: ...

    # ------------------------------------------------------------------
    # Channel operations. Not every backend supports these, so they default
    # to NotImplementedError (dispatch maps it to CAPABILITY_UNAVAILABLE)
    # instead of forcing stub overrides.
    # ------------------------------------------------------------------

    def _unsupported(self, what: str):
        raise NotImplementedError(
            f"{what} is not supported by the {self.name} backend. Use --backend private."
        )

    def profile_show(self) -> dict:
        self._unsupported("profile show")

    def profile_edit(self, bio: str | None = None, name: str | None = None,
                     website: str | None = None) -> dict:
        self._unsupported("profile edit")

    def profile_picture(self, media: str) -> dict:
        self._unsupported("profile picture")

    def profile_picture_remove(self) -> dict:
        self._unsupported("profile picture --remove")

    def profile_convert(self, to: str, category_id: str | None = None) -> dict:
        self._unsupported("profile convert")

    def story_delete(self, story_id: str) -> dict:
        self._unsupported("story delete")

    def post_delete(self, media_ref: str) -> dict:
        self._unsupported("post delete")

    def post_edit(self, media_ref: str, caption: str) -> dict:
        self._unsupported("post edit")

    def dm_pending(self, limit: int = 20) -> list[dict]:
        self._unsupported("dm pending")

    def dm_approve(self, thread_id: str) -> dict:
        self._unsupported("dm approve")

    def dm_seen(self, thread_id: str) -> dict:
        self._unsupported("dm seen")

    def comments_like(self, comment_ref: str) -> dict:
        self._unsupported("comments like")

    def comments_unlike(self, comment_ref: str) -> dict:
        self._unsupported("comments unlike")

    def comments_pin(self, comment_ref: str) -> dict:
        self._unsupported("comments pin")

    def comments_unpin(self, comment_ref: str) -> dict:
        self._unsupported("comments unpin")

    def activity(self, limit: int = 50) -> list[dict]:
        self._unsupported("activity")

    def analytics_insights(self, media_ref: str) -> dict:
        self._unsupported("analytics insights")

    def analytics_account(self) -> dict:
        self._unsupported("analytics account")

    def media_likers(self, media_ref: str, limit: int = 100) -> list[dict]:
        self._unsupported("media likers")

    def user_tagged(self, username: str, limit: int = 20) -> list[dict]:
        self._unsupported("user tagged")
