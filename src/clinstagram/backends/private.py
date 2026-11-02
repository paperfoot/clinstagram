from __future__ import annotations

import re

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


_RICH_TEXT_TOKEN = re.compile(r"\{([^|{}]+)\|[^{}]*\}")

# Ordered: first match wins. Matched against the plain notification text.
_ACTIVITY_TYPES = (
    ("follow", ("started following you", "requested to follow you")),
    ("comment_like", ("liked your comment",)),
    ("like", ("liked your", "liked a photo", "liked a video", "liked a reel")),
    ("mention", ("mentioned you",)),
    ("comment", ("commented", "replied to your comment")),
    ("tag", ("tagged you",)),
    ("suggestion", ("follow suggestions", "you might know", "suggested for you")),
)


def _activity_type(text: str) -> str:
    lowered = text.lower()
    for kind, needles in _ACTIVITY_TYPES:
        if any(n in lowered for n in needles):
            return kind
    return "other"


def _activity_to_dict(story: dict) -> dict:
    """Normalize one news-inbox story into {type, text, user, media_id, timestamp}."""
    from datetime import datetime, timezone

    args = story.get("args") or {}
    raw = args.get("rich_text") or args.get("text") or ""
    text = _RICH_TEXT_TOKEN.sub(lambda m: m.group(1), raw).strip()
    ts = args.get("timestamp")
    timestamp = None
    if isinstance(ts, (int, float)):
        timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    medias = args.get("media") or []
    media_id = None
    if medias and isinstance(medias[0], dict) and medias[0].get("id"):
        media_id = str(medias[0]["id"]).split("_", 1)[0]
    user = None
    if args.get("profile_id") or args.get("profile_name"):
        user = {
            "id": str(args["profile_id"]) if args.get("profile_id") else None,
            "username": args.get("profile_name"),
            "profile_picture_url": args.get("profile_image"),
        }
    return {
        "type": _activity_type(text),
        "text": text,
        "user": user,
        "media_id": media_id,
        "comment_id": str(args["comment_id"]) if args.get("comment_id") else None,
        "timestamp": timestamp,
        "story_type": story.get("story_type"),
    }


def _account_to_dict(acc: Any, info: Any = None) -> dict:
    """Own-profile view. Deliberately omits email, phone number and birthday."""
    return {
        "id": str(acc.pk),
        "username": acc.username,
        "full_name": acc.full_name or None,
        "biography": acc.biography or None,
        "website": acc.external_url or None,
        "profile_picture_url": str(acc.profile_pic_url) if acc.profile_pic_url else None,
        "is_private": acc.is_private,
        "is_verified": acc.is_verified,
        "is_business": acc.is_business,
        "followers_count": getattr(info, "follower_count", None),
        "following_count": getattr(info, "following_count", None),
        "media_count": getattr(info, "media_count", None),
        "category": getattr(info, "category_name", None) or getattr(info, "category", None) or None,
    }


def _business_required(fn_name: str, exc: Exception) -> PrivateAPIError:
    msg = str(exc)
    if "business" in msg.lower() or "insights" in msg.lower():
        return PrivateAPIError(
            f"{fn_name} failed: insights exist only for your own posts on a professional "
            "(Business or Creator) account. Switch in the Instagram app: Settings > "
            f"Account type and tools > Switch to professional account. ({msg})"
        )
    return _wrap_error(fn_name, exc)


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

    def followers_list(self, limit: int = 100, user: str = "") -> list[dict]:
        try:
            user_id = int(self._user_id_from_username(user)) if user else self._cl.user_id
            followers = self._cl.user_followers(user_id, amount=limit)
            return [_user_to_dict(u) for u in list(followers.values())[:limit]]
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("followers_list", exc)

    def followers_following(self, limit: int = 100, user: str = "") -> list[dict]:
        try:
            user_id = int(self._user_id_from_username(user)) if user else self._cl.user_id
            following = self._cl.user_following(user_id, amount=limit)
            return [_user_to_dict(u) for u in list(following.values())[:limit]]
        except PrivateAPIError:
            raise
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


    # ------------------------------------------------------------------
    # Profile (own account)
    # ------------------------------------------------------------------

    def _split_comment_ref(self, comment_ref: str, action: str) -> tuple[str, int]:
        parts = comment_ref.split(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            raise ValueError(
                f"{action} needs the comment_ref returned by 'comments list' "
                "(format: media_id:comment_id)."
            )
        return self._media_pk(parts[0]), int(parts[1])

    def profile_show(self) -> dict:
        try:
            acc = self._cl.account_info()
            info = self._cl.user_info(int(acc.pk))
            return _account_to_dict(acc, info)
        except Exception as exc:
            raise _wrap_error("profile_show", exc)

    def profile_edit(self, bio: str | None = None, name: str | None = None,
                     website: str | None = None) -> dict:
        fields: dict[str, str] = {}
        if bio is not None:
            fields["biography"] = bio
        if name is not None:
            fields["full_name"] = name
        if website is not None:
            fields["external_url"] = website
        if not fields:
            raise ValueError("Nothing to change: pass --bio, --name and/or --website.")
        try:
            acc = self._cl.account_edit(**fields)
            if bio == "":
                # account_edit only re-sets the biography when it is non-empty
                self._cl.account_set_biography("")
            return {"updated": sorted(fields), "profile": _account_to_dict(acc)}
        except Exception as exc:
            raise _wrap_error("profile_edit", exc)

    def profile_picture(self, media: str) -> dict:
        try:
            user = self._cl.account_change_picture(Path(media))
            pic = getattr(user, "profile_pic_url", None)
            return {"status": "updated", "profile_picture_url": str(pic) if pic else None}
        except Exception as exc:
            raise _wrap_error("profile_picture", exc)

    # ------------------------------------------------------------------
    # Post management
    # ------------------------------------------------------------------

    def post_delete(self, media_ref: str) -> dict:
        try:
            pk = self._media_pk(media_ref)
            ok = self._cl.media_delete(self._cl.media_id(pk))
            if not ok:
                raise RuntimeError("Instagram did not confirm the deletion")
            return {"media_id": pk, "status": "deleted"}
        except Exception as exc:
            raise _wrap_error("post_delete", exc)

    def post_edit(self, media_ref: str, caption: str) -> dict:
        try:
            pk = self._media_pk(media_ref)
            self._cl.media_edit(self._cl.media_id(pk), caption)
            return {"media_id": pk, "caption": caption, "status": "edited"}
        except Exception as exc:
            raise _wrap_error("post_edit", exc)

    # ------------------------------------------------------------------
    # DM management
    # ------------------------------------------------------------------

    def dm_pending(self, limit: int = 20) -> list[dict]:
        try:
            threads = self._cl.direct_pending_inbox(amount=limit)
            return [_thread_to_dict(t) for t in threads[:limit]]
        except Exception as exc:
            raise _wrap_error("dm_pending", exc)

    def dm_approve(self, thread_id: str) -> dict:
        try:
            ok = self._cl.direct_pending_approve(int(thread_id))
            return {"thread_id": thread_id, "status": "approved" if ok else "failed"}
        except Exception as exc:
            raise _wrap_error("dm_approve", exc)

    def dm_seen(self, thread_id: str) -> dict:
        try:
            ok = self._cl.direct_send_seen(int(thread_id))
            return {"thread_id": thread_id, "status": "seen" if ok else "failed"}
        except Exception as exc:
            raise _wrap_error("dm_seen", exc)

    # ------------------------------------------------------------------
    # Comment moderation
    # ------------------------------------------------------------------

    def comments_like(self, comment_ref: str) -> dict:
        try:
            _, cid = self._split_comment_ref(comment_ref, "comments like")
            self._cl.comment_like(cid)
            return {"comment_ref": comment_ref, "status": "liked"}
        except Exception as exc:
            raise _wrap_error("comments_like", exc)

    def comments_unlike(self, comment_ref: str) -> dict:
        try:
            _, cid = self._split_comment_ref(comment_ref, "comments unlike")
            self._cl.comment_unlike(cid)
            return {"comment_ref": comment_ref, "status": "unliked"}
        except Exception as exc:
            raise _wrap_error("comments_unlike", exc)

    def comments_pin(self, comment_ref: str) -> dict:
        try:
            media_pk, cid = self._split_comment_ref(comment_ref, "comments pin")
            self._cl.comment_pin(media_pk, cid)
            return {"comment_ref": comment_ref, "status": "pinned"}
        except Exception as exc:
            raise _wrap_error("comments_pin", exc)

    def comments_unpin(self, comment_ref: str) -> dict:
        try:
            media_pk, cid = self._split_comment_ref(comment_ref, "comments unpin")
            self._cl.comment_unpin(media_pk, cid)
            return {"comment_ref": comment_ref, "status": "unpinned"}
        except Exception as exc:
            raise _wrap_error("comments_unpin", exc)

    # ------------------------------------------------------------------
    # Notifications and insights
    # ------------------------------------------------------------------

    def activity(self, limit: int = 50) -> list[dict]:
        try:
            inbox = self._cl.news_inbox_v1(mark_as_seen=False)
        except Exception as exc:
            raise _wrap_error("activity", exc)
        stories = (inbox.get("new_stories") or []) + (inbox.get("old_stories") or [])
        return [_activity_to_dict(st) for st in stories[:limit]]

    def analytics_insights(self, media_ref: str) -> dict:
        try:
            pk = self._media_pk(media_ref)
            data = self._cl.insights_media(int(pk))
        except Exception as exc:
            raise _business_required("analytics_insights", exc)
        return {"media_id": pk, "insights": data}

    def analytics_account(self) -> dict:
        try:
            data = self._cl.insights_account()
        except Exception as exc:
            raise _business_required("analytics_account", exc)
        account_type = ((data or {}).get("status") or {}).get("account_type")
        if account_type == "PERSONAL_ACCOUNT":
            # Instagram answers with an all-zero skeleton for personal accounts
            raise _business_required(
                "analytics_account", RuntimeError("Account is not business account")
            )
        return data

    # ------------------------------------------------------------------
    # Prospecting
    # ------------------------------------------------------------------

    def media_likers(self, media_ref: str, limit: int = 100) -> list[dict]:
        try:
            users = self._cl.media_likers(self._media_pk(media_ref))
            return [_user_to_dict(u) for u in users[:limit]]
        except Exception as exc:
            raise _wrap_error("media_likers", exc)

    def user_tagged(self, username: str, limit: int = 20) -> list[dict]:
        try:
            user_id = self._user_id_from_username(username)
            medias = self._cl.usertag_medias(int(user_id), amount=limit)
            return [_media_to_dict(m) for m in medias[:limit]]
        except PrivateAPIError:
            raise
        except Exception as exc:
            raise _wrap_error("user_tagged", exc)
