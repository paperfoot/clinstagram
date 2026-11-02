from __future__ import annotations

from pathlib import Path
from typing import Any

from clinstagram.backends.base import Backend


class PrivateAPIError(Exception):
    """Raised when an instagrapi operation fails."""

    pass


def _user_to_dict(user: Any) -> dict:
    """Convert an instagrapi User model to a plain dict."""
    return {
        "id": str(user.pk),
        "username": user.username,
        "full_name": getattr(user, "full_name", "") or None,
        "profile_picture_url": str(user.profile_pic_url) if getattr(user, "profile_pic_url", None) else None,
        "is_private": getattr(user, "is_private", None),
        "is_verified": getattr(user, "is_verified", None),
        "biography": getattr(user, "biography", None),
        "followers_count": getattr(user, "follower_count", None),
        "following_count": getattr(user, "following_count", None),
        "media_count": getattr(user, "media_count", None),
    }


def _location_to_dict(loc: Any) -> dict | None:
    """Convert an instagrapi Location model to a plain dict."""
    if loc is None:
        return None
    return {
        "name": getattr(loc, "name", None),
        "address": getattr(loc, "address", None),
        "city": getattr(loc, "city", None),
        "lat": getattr(loc, "lat", None),
        "lng": getattr(loc, "lng", None),
    }


def _resource_to_dict(res: Any) -> dict:
    """Convert an instagrapi Resource (carousel child) to a plain dict."""
    video_url = getattr(res, "video_url", None)
    thumbnail_url = getattr(res, "thumbnail_url", None)
    return {
        "id": str(res.pk),
        "media_type": res.media_type,
        "thumbnail_url": str(thumbnail_url) if thumbnail_url else None,
        "video_url": str(video_url) if video_url else None,
    }


def _media_to_dict(media: Any) -> dict:
    """Convert an instagrapi Media model to a plain dict."""
    thumbnail_url = getattr(media, "thumbnail_url", None)
    video_url = getattr(media, "video_url", None)
    permalink = getattr(media, "link", None)

    # Carousel children
    resources = getattr(media, "resources", None) or []

    # Tagged users — extract just usernames
    usertags_raw = getattr(media, "usertags", None) or []
    usertags = [
        getattr(ut.user, "username", None)
        for ut in usertags_raw
        if getattr(ut, "user", None) and getattr(ut.user, "username", None)
    ]

    return {
        "id": str(media.pk),
        "code": media.code,
        "media_type": media.media_type,
        "product_type": getattr(media, "product_type", None),
        "caption": media.caption_text if hasattr(media, "caption_text") else "",
        "accessibility_caption": getattr(media, "accessibility_caption", None),
        "title": getattr(media, "title", None) or None,
        "timestamp": str(media.taken_at) if media.taken_at else None,
        "like_count": getattr(media, "like_count", 0),
        "comment_count": getattr(media, "comment_count", 0),
        "play_count": getattr(media, "play_count", None),
        "video_url": str(video_url) if video_url else None,
        "thumbnail_url": str(thumbnail_url) if thumbnail_url else None,
        "video_duration": getattr(media, "video_duration", None),
        "permalink": str(permalink) if permalink else None,
        "location": _location_to_dict(getattr(media, "location", None)),
        "usertags": usertags if usertags else None,
        "resources": [_resource_to_dict(r) for r in resources] if resources else None,
    }


def _comment_to_dict(comment: Any, media_id: str = "") -> dict:
    """Convert an instagrapi Comment model to a plain dict."""
    comment_id = str(comment.pk)
    comment_ref = f"{media_id}:{comment_id}" if media_id else comment_id
    return {
        "id": comment_ref,
        "comment_id": comment_id,
        "comment_ref": comment_ref,
        "text": comment.text,
        "user": _user_to_dict(comment.user) if comment.user else None,
        "timestamp": str(comment.created_at_utc) if comment.created_at_utc else None,
    }


def _story_to_dict(story: Any) -> dict:
    """Convert an instagrapi Story model to a plain dict."""
    return {
        "id": str(story.pk),
        "media_type": story.media_type,
        "video_url": str(story.video_url) if story.video_url else None,
        "thumbnail_url": str(story.thumbnail_url) if story.thumbnail_url else None,
        "timestamp": str(story.taken_at) if story.taken_at else None,
    }


def _thread_to_dict(thread: Any) -> dict:
    """Convert an instagrapi DirectThread model to a plain dict."""
    last_item = getattr(thread, "last_permanent_item", None)
    last_message = None
    last_message_at = str(thread.last_activity_at) if thread.last_activity_at else None
    if last_item is not None:
        last_message = getattr(last_item, "text", None) or getattr(last_item, "message", None)
        timestamp = getattr(last_item, "timestamp", None)
        if timestamp:
            last_message_at = str(timestamp)
    return {
        "thread_id": str(thread.id),
        "thread_title": thread.thread_title,
        "participants": [_user_to_dict(u) for u in (thread.users or [])],
        "last_message": last_message,
        "last_message_at": last_message_at,
        "unread": bool(getattr(thread, "unread_count", 0)),
    }


def _message_to_dict(msg: Any) -> dict:
    """Convert an instagrapi DirectMessage model to a plain dict."""
    return {
        "message_id": str(msg.id),
        "text": msg.text or "",
        "sender_id": str(msg.user_id) if msg.user_id else None,
        "timestamp": str(msg.timestamp) if msg.timestamp else None,
        "item_type": getattr(msg, "item_type", None),
    }


def _wrap_error(fn_name: str, exc: Exception) -> PrivateAPIError:
    """Wrap an instagrapi exception with context."""
    return PrivateAPIError(f"{fn_name} failed: {exc}")


class PrivateBackend(Backend):
    """Instagram Private API backend via instagrapi."""

    def __init__(self, client: Any):
        """
        Parameters
        ----------
        client : instagrapi.Client
            An already-authenticated instagrapi Client instance.
        """
        self._cl = client
        # In-memory cache: username → (user_pk_str, user_object)
        # Eliminates redundant user_info_by_username API calls within a session.
        self._user_cache: dict[str, tuple[str, Any]] = {}

    @property
    def name(self) -> str:
        return "private"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _user_id_from_username(self, username: str) -> str:
        cached = self._user_cache.get(username)
        if cached:
            return cached[0]
        try:
            info = self._cl.user_info_by_username(username)
            pk = str(info.pk)
            self._user_cache[username] = (pk, info)
            return pk
        except Exception as exc:
            raise _wrap_error("user_id_from_username", exc)

    def _media_pk(self, media_ref: str) -> str:
        """Resolve a media id, "pk_userid" id, shortcode, or instagram.com URL to a pk.

        Shortcode and URL decoding is local (no network call).
        """
        ref = media_ref.strip()
        if ref.startswith(("http://", "https://")):
            return str(self._cl.media_pk_from_url(ref))
        head = ref.split("_", 1)[0]
        if head.isdigit():
            return head
        return str(self._cl.media_pk_from_code(ref))

    def _cached_user_info(self, username: str) -> Any:
        """Get user info object, using cache to avoid redundant API calls."""
        cached = self._user_cache.get(username)
        if cached:
            return cached[1]
        try:
            info = self._cl.user_info_by_username(username)
            self._user_cache[username] = (str(info.pk), info)
            return info
        except Exception as exc:
            raise _wrap_error("user_info", exc)

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    def post_photo(
        self, media: str, caption: str = "", location: str = "", tags: list[str] | None = None
    ) -> dict:
        try:
            kwargs: dict[str, Any] = {"path": Path(media), "caption": caption}
            result = self._cl.photo_upload(**kwargs)
            return _media_to_dict(result)
        except Exception as exc:
            raise _wrap_error("post_photo", exc)

    def post_video(
        self, media: str, caption: str = "", thumbnail: str = "", location: str = ""
    ) -> dict:
        try:
            kwargs: dict[str, Any] = {"path": Path(media), "caption": caption}
            if thumbnail:
                kwargs["thumbnail"] = Path(thumbnail)
            result = self._cl.video_upload(**kwargs)
            return _media_to_dict(result)
        except Exception as exc:
            raise _wrap_error("post_video", exc)

    def post_reel(
        self, media: str, caption: str = "", thumbnail: str = "", audio: str = ""
    ) -> dict:
        try:
            kwargs: dict[str, Any] = {"path": Path(media), "caption": caption}
            if thumbnail:
                kwargs["thumbnail"] = Path(thumbnail)
            result = self._cl.clip_upload(**kwargs)
            return _media_to_dict(result)
        except Exception as exc:
            raise _wrap_error("post_reel", exc)

    def post_carousel(self, media_list: list[str], caption: str = "") -> dict:
        try:
            paths = [Path(m) for m in media_list]
            result = self._cl.album_upload(paths, caption=caption)
            return _media_to_dict(result)
        except Exception as exc:
            raise _wrap_error("post_carousel", exc)

    # ------------------------------------------------------------------
    # DMs
    # ------------------------------------------------------------------

    def dm_inbox(self, limit: int = 20, unread_only: bool = False) -> list[dict]:
        try:
            threads = self._cl.direct_threads(amount=limit)
            results = [_thread_to_dict(t) for t in threads]
            if unread_only:
                results = [t for t in results if t["unread"]]
            return results
        except Exception as exc:
            raise _wrap_error("dm_inbox", exc)

    def dm_thread(self, thread_id: str, limit: int = 20) -> list[dict]:
        try:
            messages = self._cl.direct_messages(thread_id, amount=limit)
            return [_message_to_dict(m) for m in messages]
        except Exception as exc:
            raise _wrap_error("dm_thread", exc)

    def dm_send(self, user: str, message: str) -> dict:
        try:
            kwargs: dict[str, Any] = {}
            if user.isdigit():
                kwargs["thread_ids"] = [user]
            else:
                kwargs["user_ids"] = [int(self._user_id_from_username(user))]
            result = self._cl.direct_send(message, **kwargs)
            return {"message_id": str(result.id) if result else None, "status": "sent"}
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("dm_send", exc)

    def dm_send_media(self, user: str, media: str) -> dict:
        try:
            kwargs: dict[str, Any] = {}
            if user.isdigit():
                kwargs["thread_ids"] = [user]
            else:
                kwargs["user_ids"] = [int(self._user_id_from_username(user))]
            path = Path(media)
            suffix = path.suffix.lower()
            if suffix in (".mp4", ".mov", ".avi"):
                result = self._cl.direct_send_video(path, **kwargs)
            else:
                result = self._cl.direct_send_photo(path, **kwargs)
            return {"message_id": str(result.id) if result else None, "status": "sent"}
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("dm_send_media", exc)

    # ------------------------------------------------------------------
    # Stories
    # ------------------------------------------------------------------

    def story_list(self, user: str = "") -> list[dict]:
        try:
            if user:
                user_id = int(self._user_id_from_username(user))
                stories = self._cl.user_stories(user_id)
            else:
                user_id = self._cl.user_id
                stories = self._cl.user_stories(user_id)
            return [_story_to_dict(s) for s in stories]
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("story_list", exc)

    def story_post_photo(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict:
        try:
            kwargs: dict[str, Any] = {"path": Path(media)}
            if mentions:
                from instagrapi.types import StoryMention

                mention_objects = []
                for m in mentions:
                    user_info = self._cached_user_info(m)
                    mention_objects.append(
                        StoryMention(user=user_info, x=0.5, y=0.5, width=0.3, height=0.05)
                    )
                kwargs["mentions"] = mention_objects
            if link:
                from instagrapi.types import StoryLink

                kwargs["links"] = [StoryLink(webUri=link)]
            result = self._cl.photo_upload_to_story(**kwargs)
            return _story_to_dict(result)
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("story_post_photo", exc)

    def story_post_video(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict:
        try:
            kwargs: dict[str, Any] = {"path": Path(media)}
            if mentions:
                from instagrapi.types import StoryMention

                mention_objects = []
                for m in mentions:
                    user_info = self._cached_user_info(m)
                    mention_objects.append(
                        StoryMention(user=user_info, x=0.5, y=0.5, width=0.3, height=0.05)
                    )
                kwargs["mentions"] = mention_objects
            if link:
                from instagrapi.types import StoryLink

                kwargs["links"] = [StoryLink(webUri=link)]
            result = self._cl.video_upload_to_story(**kwargs)
            return _story_to_dict(result)
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("story_post_video", exc)

    def story_viewers(self, story_id: str) -> list[dict]:
        try:
            viewers = self._cl.story_viewers(story_id)
            return [_user_to_dict(v) for v in viewers]
        except Exception as exc:
            raise _wrap_error("story_viewers", exc)

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def comments_list(self, media_id: str, limit: int = 50) -> list[dict]:
        try:
            media_id = self._media_pk(media_id)
            comments = self._cl.media_comments(media_id, amount=limit)
            return [_comment_to_dict(c, media_id) for c in comments]
        except Exception as exc:
            raise _wrap_error("comments_list", exc)

    def comments_reply(self, comment_id: str, text: str) -> dict:
        try:
            # Composite ID format: media_id:comment_id (from comments_list)
            parts = comment_id.split(":", 1)
            if len(parts) == 2:
                media_id, cid = parts
                result = self._cl.media_comment(media_id, text, replied_to_comment_id=int(cid))
            else:
                raise ValueError(
                    "Private comment replies require the comment_ref returned by 'comments list' "
                    "(format: media_id:comment_id)."
                )
            return _comment_to_dict(result, media_id)
        except Exception as exc:
            raise _wrap_error("comments_reply", exc)

    def comments_delete(self, comment_id: str) -> dict:
        try:
            parts = comment_id.split(":", 1)
            if len(parts) == 2:
                media_id, cid = parts
                self._cl.comment_bulk_delete(media_id, [int(cid)])
            else:
                raise ValueError(
                    "Private comment deletes require the comment_ref returned by 'comments list' "
                    "(format: media_id:comment_id)."
                )
            return {"comment_ref": comment_id, "status": "deleted"}
        except Exception as exc:
            raise _wrap_error("comments_delete", exc)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def analytics_profile(self) -> dict:
        try:
            account = self._cl.account_info()
            # Account object lacks follower/media counts — fetch via user_info
            info = self._cl.user_info(int(account.pk))
            return _user_to_dict(info)
        except Exception as exc:
            raise _wrap_error("analytics_profile", exc)

    def analytics_post(self, media_id: str) -> dict:
        try:
            if media_id == "latest":
                medias = self._cl.user_medias(self._cl.user_id, amount=1)
                if not medias:
                    raise ValueError("No posts found for the current account")
                media_id = str(medias[0].pk)
            info = self._cl.media_info(self._media_pk(media_id))
            return _media_to_dict(info)
        except Exception as exc:
            raise _wrap_error("analytics_post", exc)

    def analytics_hashtag(self, tag: str) -> dict:
        try:
            info = self._cl.hashtag_info(tag)
            return {
                "name": info.name,
                "media_count": info.media_count,
            }
        except Exception as exc:
            raise _wrap_error("analytics_hashtag", exc)

    # ------------------------------------------------------------------
    # Followers
    # ------------------------------------------------------------------

    def followers_list(self, limit: int = 100) -> list[dict]:
        try:
            user_id = self._cl.user_id
            followers = self._cl.user_followers(user_id, amount=limit)
            return [_user_to_dict(u) for u in followers.values()]
        except Exception as exc:
            raise _wrap_error("followers_list", exc)

    def followers_following(self, limit: int = 100) -> list[dict]:
        try:
            user_id = self._cl.user_id
            following = self._cl.user_following(user_id, amount=limit)
            return [_user_to_dict(u) for u in following.values()]
        except Exception as exc:
            raise _wrap_error("followers_following", exc)

    def follow(self, user: str) -> dict:
        try:
            user_id = int(self._user_id_from_username(user))
            result = self._cl.user_follow(user_id)
            return {"username": user, "followed": result}
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("follow", exc)

    def unfollow(self, user: str) -> dict:
        try:
            user_id = int(self._user_id_from_username(user))
            result = self._cl.user_unfollow(user_id)
            return {"username": user, "unfollowed": result}
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("unfollow", exc)

    # ------------------------------------------------------------------
    # User
    # ------------------------------------------------------------------

    def user_info(self, username: str) -> dict:
        try:
            info = self._cl.user_info_by_username(username)
            return _user_to_dict(info)
        except Exception as exc:
            raise _wrap_error("user_info", exc)

    def user_search(self, query: str) -> list[dict]:
        try:
            users = self._cl.search_users(query)
            return [_user_to_dict(u) for u in users]
        except Exception as exc:
            raise _wrap_error("user_search", exc)

    def user_posts(self, username: str, limit: int = 20) -> list[dict]:
        try:
            user_id = self._user_id_from_username(username)
            medias = self._cl.user_medias(int(user_id), amount=limit)
        except Exception as exc:
            raise _wrap_error("user_posts", exc)
        if not medias and limit > 0:
            # Instagram sometimes answers "ok" with an empty profile feed for a
            # profile that has posts — it withholds grids from sessions it
            # suspects of automation. An empty list would read as "no posts".
            info = self._user_cache.get(username, (None, None))[1]
            expected = getattr(info, "media_count", 0) or 0
            if expected > 0:
                raise PrivateAPIError(
                    f"user_posts failed: Instagram returned 0 of {expected} posts for "
                    f"@{username}. It is withholding profile feeds from this session "
                    "(common for new or automation-flagged accounts). Use the Graph "
                    "backend (--backend graph_ig) or log in with an established account."
                )
        return [_media_to_dict(m) for m in medias]

    # ------------------------------------------------------------------
    # Engagement
    # ------------------------------------------------------------------

    def like_post(self, media_id: str) -> dict:
        try:
            media_id = self._media_pk(media_id)
            self._cl.media_like(media_id)
            return {"media_id": media_id, "status": "liked"}
        except Exception as exc:
            raise _wrap_error("like_post", exc)

    def unlike_post(self, media_id: str) -> dict:
        try:
            media_id = self._media_pk(media_id)
            self._cl.media_unlike(media_id)
            return {"media_id": media_id, "status": "unliked"}
        except Exception as exc:
            raise _wrap_error("unlike_post", exc)

    def comments_add(self, media_id: str, text: str) -> dict:
        try:
            media_id = self._media_pk(media_id)
            result = self._cl.media_comment(media_id, text)
            return _comment_to_dict(result, media_id)
        except Exception as exc:
            raise _wrap_error("comments_add", exc)

    # ------------------------------------------------------------------
    # Hashtag browsing
    # ------------------------------------------------------------------

    def hashtag_top(self, tag: str, limit: int = 20) -> list[dict]:
        try:
            medias = self._cl.hashtag_medias_top(tag, amount=limit)
            return [_media_to_dict(m) for m in medias]
        except Exception as exc:
            raise _wrap_error("hashtag_top", exc)

    def hashtag_recent(self, tag: str, limit: int = 20) -> list[dict]:
        try:
            medias = self._cl.hashtag_medias_recent(tag, amount=limit)
            return [_media_to_dict(m) for m in medias]
        except Exception as exc:
            raise _wrap_error("hashtag_recent", exc)

    # ------------------------------------------------------------------
    # Media download
    # ------------------------------------------------------------------

    def media_download(self, media_ref: str, output_dir: str = "") -> dict:
        """Download media by id/shortcode/URL. Returns paths and metadata."""
        from pathlib import Path

        try:
            media_pk = int(self._media_pk(media_ref))

            folder = Path(output_dir) if output_dir else Path.cwd()
            folder.mkdir(parents=True, exist_ok=True)

            info = self._cl.media_info(media_pk)
            media_type = info.media_type  # 1=photo, 2=video, 8=album

            files: list[str] = []
            if media_type == 1:
                path = self._cl.photo_download(media_pk, folder=folder)
                files.append(str(path))
            elif media_type == 2:
                path = self._cl.video_download(media_pk, folder=folder)
                files.append(str(path))
            elif media_type == 8:
                paths = self._cl.album_download(media_pk, folder=folder)
                files.extend(str(p) for p in paths)
            else:
                raise ValueError(f"Unsupported media_type: {media_type}")

            return {
                "media_id": str(media_pk),
                "code": info.code,
                "media_type": media_type,
                "product_type": getattr(info, "product_type", None),
                "files": files,
                "output_dir": str(folder.resolve()),
            }
        except Exception as exc:
            raise _wrap_error("media_download", exc)
