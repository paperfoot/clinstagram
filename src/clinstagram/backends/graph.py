from __future__ import annotations

import json as _json
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from clinstagram.backends.base import Backend
from clinstagram.media import is_url

# Instagram usernames: 1-30 chars, alphanumeric + dots + underscores
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{1,30}$")


class GraphAPIError(Exception):
    """Raised when the Instagram/Facebook Graph API returns an error.

    `needs_new_token` marks a refusal only a new token fixes; the CLI, which knows the
    account, then says how to store one for it.
    """

    def __init__(self, status_code: int, error_type: str, message: str, code: int | None = None,
                 subcode: int | None = None, *, needs_new_token: bool = False):
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.subcode = subcode
        self.needs_new_token = needs_new_token
        super().__init__(message)


def _extract_error(response: httpx.Response) -> GraphAPIError:
    """Parse a Graph API error response into a structured exception."""
    try:
        body = response.json()
        err = body.get("error", {})
        message = err.get("message", response.text)
        # Meta often puts the useful part in error_user_msg ("Invalid parameter" alone
        # doesn't say the post predates the switch to a business account).
        detail = err.get("error_user_msg") or err.get("error_user_title")
        if detail and detail not in message:
            message = f"{message}: {detail}"
        return GraphAPIError(
            status_code=response.status_code,
            error_type=err.get("type", "Unknown"),
            message=message,
            code=err.get("code"),
            subcode=err.get("error_subcode"),
        )
    except Exception:
        return GraphAPIError(
            status_code=response.status_code,
            error_type="Unknown",
            message=response.text,
        )


def _check(response: httpx.Response) -> dict | list:
    """Raise on non-2xx, otherwise return parsed JSON."""
    if response.status_code >= 400:
        raise _extract_error(response)
    return response.json()


# Meta answers code 10 both for "the token lacks this permission" and for this benign
# case: the post hasn't reached enough viewers to have insights yet.
_NOT_ENOUGH_VIEWERS = re.compile(r"not enough viewers", re.I)
# "(#100) metric[3] must be one of the following values: reach, views, ..." lists what is
# allowed, so the rejected metrics are the requested ones it leaves out.
_ALLOWED_METRICS = re.compile(r"\bmetric(?:\[(\d+)\])?\s+must be one of the following values:(.*)", re.I | re.S)
# Rate limits and dead tokens: every later call of a multi-call command fails the same way.
_FATAL_CODES = {4, 17, 32, 613, 102, 190}


def _not_enough_viewers(exc: GraphAPIError) -> bool:
    return bool(_NOT_ENOUGH_VIEWERS.search(str(exc)))


def _rejected_metrics(exc: GraphAPIError, asked: list[str]) -> list[str]:
    """The requested metrics a code-100 error rejects; empty when it names none of them."""
    if exc.code != 100:
        return []
    text = str(exc)
    listed = _ALLOWED_METRICS.search(text)
    if listed:
        allowed = set(re.findall(r"\w+", listed.group(2).lower()))
        rejected = [m for m in asked if m not in allowed]
        index = listed.group(1)
        if not rejected and index is not None and int(index) < len(asked):
            rejected = [asked[int(index)]]
        return rejected
    return [m for m in asked if re.search(rf"(?<!\w){re.escape(m)}(?!\w)", text)]


def _is_fatal(exc: GraphAPIError) -> bool:
    code = exc.code
    return (exc.status_code == 401 or code in _FATAL_CODES
            or (isinstance(code, int) and 80001 <= code <= 80014))


_PERMISSION_CODES = {3, 10} | set(range(200, 300))  # the app or token lacks the permission


def _cut_off(exc: GraphAPIError, what: str) -> GraphAPIError:
    """A refusal of the account's own profile or posts, which every token may read: on
    3 Nov 2026 that was a token Meta had cut off, and a new token fixed it."""
    return GraphAPIError(
        exc.status_code, exc.error_type,
        f"{what} ({exc}). A token that worked before and now does this was cut off on Meta's "
        "side, as seen after Boost and Facebook-linking changes",
        code=exc.code, subcode=exc.subcode, needs_new_token=True,
    )


def _first_line(text: str | None, width: int = 80) -> str | None:
    """A caption's first non-empty line, cut to `width` characters."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= width else line[: width - 1].rstrip() + "…"
    return None


class GraphBackend(Backend):
    """Instagram Graph API / Facebook Graph API backend."""

    BASE_URLS = {
        "ig": "https://graph.instagram.com/v21.0",
        "fb": "https://graph.facebook.com/v21.0",
    }

    def __init__(self, token: str, login_type: str, client: httpx.Client, ig_user_id: str | None = None):
        if login_type not in self.BASE_URLS:
            raise ValueError(f"login_type must be 'ig' or 'fb', got '{login_type}'")
        self._token = token
        self._login_type = login_type
        self._client = client
        self._base = self.BASE_URLS[login_type]
        self._me_id_cache: str | None = ig_user_id or None
        # media id -> (media_type, media_product_type), learned from listings
        self._media_types_cache: dict[str, tuple[str | None, str | None]] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"graph_{self._login_type}"

    def _url(self, path: str) -> str:
        return f"{self._base}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if extra:
            p.update(extra)
        return p

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return _check(self._client.get(self._url(path), params=self._params(params), headers=self._headers()))

    def _post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return _check(self._client.post(self._url(path), data=self._params(data), headers=self._headers()))

    def _delete(self, path: str) -> Any:
        return _check(self._client.delete(self._url(path), params=self._params(), headers=self._headers()))

    @staticmethod
    def _validate_username(username: str) -> str:
        """Validate an Instagram username before interpolating into Graph queries."""
        if not _USERNAME_RE.match(username):
            raise ValueError(f"Invalid Instagram username: {username!r}")
        return username

    def _normalize_user(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(data["id"]) if data.get("id") is not None else None,
            "username": data.get("username"),
            "full_name": data.get("full_name") or data.get("name"),
            "profile_picture_url": data.get("profile_picture_url") or data.get("profile_pic_url"),
            "is_private": data.get("is_private"),
            "is_verified": data.get("is_verified"),
            "biography": data.get("biography"),
            "followers_count": data.get("followers_count"),
            "following_count": data.get("following_count", data.get("follows_count")),
            "media_count": data.get("media_count"),
        }

    def _normalize_media(self, data: dict[str, Any]) -> dict[str, Any]:
        media_type = data.get("media_type")
        media_url = data.get("media_url")

        # Graph API returns a single media_url; infer video vs thumbnail
        video_url = media_url if media_type == "VIDEO" else None
        thumbnail_url = media_url if media_type != "VIDEO" else data.get("thumbnail_url")

        # Carousel children (if requested with children{} expansion)
        children_raw = data.get("children", {}).get("data", [])
        resources = None
        if children_raw:
            resources = [
                {
                    "id": str(child["id"]),
                    "media_type": child.get("media_type"),
                    "thumbnail_url": child.get("media_url") if child.get("media_type") != "VIDEO" else None,
                    "video_url": child.get("media_url") if child.get("media_type") == "VIDEO" else None,
                }
                for child in children_raw
            ]

        return {
            "id": str(data["id"]),
            "code": data.get("shortcode") or data.get("code"),
            "media_type": media_type,
            "product_type": data.get("media_product_type"),
            "caption": data.get("caption", ""),
            "accessibility_caption": None,
            "title": None,
            "timestamp": data.get("timestamp"),
            "like_count": data.get("like_count", 0),
            "comment_count": data.get("comment_count", data.get("comments_count", 0)),
            "play_count": None,
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
            "video_duration": None,
            "permalink": data.get("permalink"),
            "location": None,
            "usertags": None,
            "resources": resources,
        }

    def _normalize_comment(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(data["id"]),
            "comment_id": str(data["id"]),
            "comment_ref": str(data["id"]),
            "text": data.get("text", ""),
            "user": self._normalize_user(data["from"]) if isinstance(data.get("from"), dict) else None,
            "timestamp": data.get("timestamp"),
        }

    def _normalize_thread(self, data: dict[str, Any]) -> dict[str, Any]:
        participants = [
            self._normalize_user(participant)
            for participant in data.get("participants", {}).get("data", [])
        ]
        messages = data.get("messages", {}).get("data", [])
        last_message = messages[0] if messages else {}
        thread_title = ", ".join(
            participant["username"] or participant["full_name"] or participant["id"] or ""
            for participant in participants
            if participant["username"] or participant["full_name"] or participant["id"]
        )
        return {
            "thread_id": str(data["id"]),
            "thread_title": thread_title or None,
            "participants": participants,
            "last_message": last_message.get("message"),
            "last_message_at": last_message.get("created_time") or data.get("updated_time"),
            "unread": bool(data["unread_count"]) if data.get("unread_count") is not None else None,
        }

    def _normalize_message(self, data: dict[str, Any]) -> dict[str, Any]:
        sender = data.get("from") or {}
        return {
            "message_id": str(data["id"]),
            "text": data.get("message", ""),
            "sender_id": str(sender["id"]) if sender.get("id") is not None else None,
            "sender_username": sender.get("username"),
            "timestamp": data.get("created_time"),
            "item_type": "text",
        }

    def _require_fb(self, feature: str) -> None:
        if self._login_type != "fb":
            raise NotImplementedError(
                f"{feature} requires Facebook login (login_type='fb'). "
                "Re-authenticate with a Facebook-linked Instagram account."
            )

    def _me_id(self) -> str:
        """The Instagram user ID that the IG endpoints act on.

        Instagram Login tokens: /me is the IG user. Facebook Login tokens: /me is a
        person, Page or system user, so look for the linked instagram_business_account.
        A system-user token (Meta's "unified login") has no /me id at all; it needs the
        IG user ID stored with `auth connect-fb --ig-user-id`.
        """
        if self._me_id_cache is None:
            if self._login_type == "ig":
                self._me_id_cache = self._get("me", {"fields": "id"})["id"]
                return self._me_id_cache
            me = self._get("me", {"fields": "id,instagram_business_account"}) or {}
            iba = (me.get("instagram_business_account") or {}).get("id")
            if not iba and me.get("id"):
                # A Page or user token with no linked account in /me: keep the old
                # behaviour and act on /me itself.
                iba = me["id"]
            if not iba:
                raise GraphAPIError(
                    400, "NoInstagramAccount",
                    "This Facebook token isn't linked to an Instagram account. "
                    "Run: clinstagram auth connect-fb --token <token> --ig-user-id <id>",
                )
            self._me_id_cache = iba
        return self._me_id_cache

    _SHORTCODE_RE = re.compile(r"(?:instagram\.com/(?:p|reel|reels|tv)/)?([A-Za-z0-9_-]{5,})/?(?:\?.*)?$")

    def _media_ref(self, ref: str) -> str:
        """Graph media ID for 'latest', a numeric Graph ID, a shortcode or a post URL.

        Graph IDs differ from the private API's media pk, so shortcodes and URLs are
        matched against this account's own recent media (Graph can't look up others').
        """
        ref = (ref or "").strip()
        me = self._me_id()
        if ref == "latest":
            items = self._get(f"{me}/media", {"fields": "id,media_type,media_product_type",
                                              "limit": "1"}).get("data", [])
            if not items:
                raise ValueError("No posts found for the current account")
            self._remember_types(items[:1])
            return str(items[0]["id"])
        if ref.isdigit():
            return ref
        m = self._SHORTCODE_RE.search(ref)
        code = m.group(1) if m else ref
        after = None
        for _ in range(4):  # up to ~200 recent posts
            params = {"fields": "id,shortcode,permalink,media_type,media_product_type", "limit": "50"}
            if after:
                params["after"] = after
            page = self._get(f"{me}/media", params) or {}
            for item in page.get("data", []):
                if item.get("shortcode") == code or f"/{code}/" in (item.get("permalink") or ""):
                    self._remember_types([item])
                    return str(item["id"])
            after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (page.get("paging") or {}).get("next"):
                break
        raise ValueError(
            f"Post '{ref}' isn't among this account's recent posts. The Graph API only "
            "resolves shortcodes and URLs of your own media; for anyone else's use --backend private."
        )

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    # Meta processes uploaded media asynchronously. Publishing a container that is
    # still IN_PROGRESS fails (error 9007 for video), so wait for FINISHED first.
    CONTAINER_POLL_SECONDS = 3.0
    CONTAINER_TIMEOUT_SECONDS = 300.0

    def _wait_container(self, container_id: str) -> None:
        sleep = getattr(self, "_sleep", time.sleep)
        waited = 0.0
        while True:
            status = self._get(container_id, {"fields": "status_code,status"})
            code = (status or {}).get("status_code", "FINISHED")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                detail = (status or {}).get("status") or code
                raise GraphAPIError(400, "ContainerError", f"Media container {container_id} failed: {detail}")
            if waited >= self.CONTAINER_TIMEOUT_SECONDS:
                raise GraphAPIError(
                    408, "ContainerTimeout",
                    f"Media container {container_id} still {code} after {int(waited)} s",
                )
            sleep(self.CONTAINER_POLL_SECONDS)
            waited += self.CONTAINER_POLL_SECONDS

    def _publish(self, me: str, container_id: str) -> dict:
        """Publish a finished container; return the new media's id, permalink and shortcode."""
        self._wait_container(container_id)
        media_id = self._post(f"{me}/media_publish", {"creation_id": container_id})["id"]
        return {"id": media_id, "status": "published", **self._published_links(media_id)}

    def _published_links(self, media_id: str) -> dict:
        # The post is already live. A failed lookup must not fail the command: an error
        # here would also let dispatch retry on the private API and post a second time.
        try:
            info = self._get(str(media_id), {"fields": "permalink,shortcode"}) or {}
        except Exception as exc:
            return {"permalink_error": str(exc)}
        links = {}
        if info.get("permalink"):
            links["permalink"] = info["permalink"]
        if info.get("shortcode"):
            links["code"] = info["shortcode"]
        return links

    def post_photo(
        self, media: str, caption: str = "", location: str = "", tags: list[str] | None = None
    ) -> dict:
        me = self._me_id()
        payload: dict[str, Any] = {"image_url": media}
        if caption:
            payload["caption"] = caption
        if location:
            payload["location_id"] = location
        if tags:
            payload["user_tags"] = _json.dumps(
                [{"username": tag, "x": 0.5, "y": 0.5} for tag in tags]
            )

        # Step 1: create media container
        container = self._post(f"{me}/media", payload)
        container_id = container["id"]

        # Step 2: publish
        return self._publish(me, container_id)

    def post_video(
        self, media: str, caption: str = "", thumbnail: str = "", location: str = ""
    ) -> dict:
        me = self._me_id()
        payload: dict[str, Any] = {"video_url": media, "media_type": "VIDEO"}
        if caption:
            payload["caption"] = caption
        if thumbnail:
            if not thumbnail.isdigit():
                raise ValueError(
                    "Graph API expects --thumbnail to be a millisecond thumb offset. "
                    "Use a numeric value or route through --backend private."
                )
            payload["thumb_offset"] = thumbnail
        if location:
            payload["location_id"] = location

        container = self._post(f"{me}/media", payload)
        container_id = container["id"]
        return self._publish(me, container_id)

    def post_reel(
        self, media: str, caption: str = "", thumbnail: str = "", audio: str = "",
        cover: str = "", share_to_feed: bool = True,
    ) -> dict:
        """Publish a reel. `cover` is a public image URL (Graph's cover_url); `thumbnail`
        is a frame offset in ms (thumb_offset). Meta would silently ignore the offset
        when both are sent, so both together is refused."""
        if cover and thumbnail:
            raise ValueError(
                "Pass either a cover image (--cover) or a frame offset (--thumbnail), not both."
            )
        if cover and not is_url(cover):
            raise ValueError(
                "Graph needs a public image URL for --cover. Upload the image first, "
                "or use --backend private for a local file."
            )
        me = self._me_id()
        # share_to_feed: the reel shows on the profile grid and in followers' feeds, not only
        # in the Reels tab. Said explicitly rather than left to Meta's default.
        payload: dict[str, Any] = {
            "video_url": media,
            "media_type": "REELS",
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if caption:
            payload["caption"] = caption
        if cover:
            payload["cover_url"] = cover
        if thumbnail:
            if not thumbnail.isdigit():
                raise ValueError(
                    "Graph API expects --thumbnail to be a millisecond thumb offset. "
                    "For a cover image use --cover <public image URL>, or route through --backend private."
                )
            payload["thumb_offset"] = thumbnail
        if audio:
            payload["audio_name"] = audio

        container = self._post(f"{me}/media", payload)
        container_id = container["id"]
        return self._publish(me, container_id)

    def post_carousel(self, media_list: list[str], caption: str = "") -> dict:
        me = self._me_id()
        # Create a container for each item
        children_ids = []
        for url in media_list:
            is_video = any(url.lower().endswith(ext) for ext in (".mp4", ".mov", ".avi"))
            payload: dict[str, Any] = {
                "is_carousel_item": "true",
                "video_url" if is_video else "image_url": url,
            }
            if is_video:
                payload["media_type"] = "VIDEO"
            child = self._post(f"{me}/media", payload)
            if is_video:
                self._wait_container(child["id"])
            children_ids.append(child["id"])

        # Create the carousel container
        carousel_payload: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
        }
        if caption:
            carousel_payload["caption"] = caption
        container = self._post(f"{me}/media", carousel_payload)
        return self._publish(me, container["id"])

    # ------------------------------------------------------------------
    # DMs (Facebook login only)
    # ------------------------------------------------------------------

    def dm_inbox(self, limit: int = 20, unread_only: bool = False) -> list[dict]:
        self._require_fb("DM inbox")
        me = self._me_id()
        params: dict[str, Any] = {
            "fields": (
                "id,updated_time,unread_count,"
                "participants{id,username,name},"
                "messages.limit(1){id,message,from{id,username,name},created_time}"
            ),
            "limit": str(limit),
        }
        data = self._get(f"{me}/conversations", params)
        threads = data.get("data", [])
        if unread_only:
            threads = [t for t in threads if t.get("unread_count", 0) > 0]
        return [self._normalize_thread(thread) for thread in threads]

    def dm_thread(self, thread_id: str, limit: int = 20) -> list[dict]:
        self._require_fb("DM thread")
        params = {
            "fields": "id,message,from{id,username,name},created_time",
            "limit": str(limit),
        }
        data = self._get(f"{thread_id}/messages", params)
        return [self._normalize_message(message) for message in data.get("data", [])]

    def dm_send(self, user: str, message: str) -> dict:
        self._require_fb("DM send")
        me = self._me_id()
        payload = {
            "recipient": _json.dumps({"id": user}),
            "message": _json.dumps({"text": message}),
        }
        result = self._post(f"{me}/messages", payload)
        return {"message_id": result.get("message_id", result.get("id")), "status": "sent"}

    def dm_send_media(self, user: str, media: str) -> dict:
        self._require_fb("DM send media")
        me = self._me_id()
        media_type = "video" if media.lower().split("?", 1)[0].endswith((".mp4", ".mov", ".avi")) else "image"
        payload = {
            "recipient": _json.dumps({"id": user}),
            "message": _json.dumps({"attachment": {"type": media_type, "payload": {"url": media}}}),
        }
        result = self._post(f"{me}/messages", payload)
        return {"message_id": result.get("message_id", result.get("id")), "status": "sent"}

    # ------------------------------------------------------------------
    # Stories
    # ------------------------------------------------------------------

    def story_list(self, user: str = "") -> list[dict]:
        target = user or self._me_id()
        params = {"fields": "id,media_type,media_url,timestamp"}
        data = self._get(f"{target}/stories", params)
        return data.get("data", [])

    def story_post_photo(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict:
        self._require_fb("Story photo post")
        if link:
            raise ValueError(
                "Graph story publishing does not support link stickers through this CLI. "
                "Use --backend private for story links."
            )
        me = self._me_id()
        payload: dict[str, Any] = {"image_url": media, "media_type": "STORIES"}
        if mentions:
            payload["user_tags"] = _json.dumps(
                [{"username": mention, "x": 0.5, "y": 0.5} for mention in mentions]
            )

        container = self._post(f"{me}/media", payload)
        container_id = container["id"]
        return self._publish(me, container_id)

    def story_post_video(
        self, media: str, mentions: list[str] | None = None, link: str = ""
    ) -> dict:
        self._require_fb("Story video post")
        if link:
            raise ValueError(
                "Graph story publishing does not support link stickers through this CLI. "
                "Use --backend private for story links."
            )
        me = self._me_id()
        payload: dict[str, Any] = {"video_url": media, "media_type": "STORIES"}
        if mentions:
            payload["user_tags"] = _json.dumps(
                [{"username": mention, "x": 0.5, "y": 0.5} for mention in mentions]
            )

        container = self._post(f"{me}/media", payload)
        container_id = container["id"]
        return self._publish(me, container_id)

    def story_viewers(self, story_id: str) -> list[dict]:
        params = {"fields": "id,username"}
        data = self._get(f"{story_id}/insights", params)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def comments_list(self, media_id: str, limit: int = 50) -> list[dict]:
        media_id = self._media_ref(media_id)
        params = {
            "fields": "id,text,from,timestamp",
            "limit": str(limit),
        }
        data = self._get(f"{media_id}/comments", params)
        return [self._normalize_comment(comment) for comment in data.get("data", [])]

    def comments_reply(self, comment_id: str, text: str) -> dict:
        result = self._post(f"{comment_id}/replies", {"message": text})
        return {"comment_ref": str(result["id"]), "status": "replied"}

    def comments_delete(self, comment_id: str) -> dict:
        self._delete(comment_id)
        return {"comment_ref": comment_id, "status": "deleted"}

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def analytics_profile(self) -> dict:
        me = self._me_id()
        params = {
            "fields": "id,followers_count,follows_count,media_count,name,username,biography,profile_picture_url",
        }
        return self._normalize_user(self._get(me, params))

    def analytics_post(self, media_id: str) -> dict:
        media_id = self._media_ref(media_id)
        params = {
            "fields": "id,caption,like_count,comments_count,timestamp,media_type,media_product_type,media_url,thumbnail_url,permalink,shortcode,children{id,media_type,media_url}",
        }
        return self._normalize_media(self._get(media_id, params))

    def analytics_hashtag(self, tag: str) -> dict:
        me = self._me_id()
        # ig_hashtag_search requires user_id on the search request itself
        search = self._get("ig_hashtag_search", {"q": tag, "user_id": me})
        results = search.get("data", [])
        if not results:
            return {"tag": tag, "error": "Hashtag not found"}
        hashtag_id = results[0]["id"]
        params = {"fields": "id,name,media_count", "user_id": me}
        return self._get(hashtag_id, params)

    # Media metrics that fit each kind of media (Meta's 2025 set: views replaced
    # impressions and plays). One metric a media type lacks fails the whole call.
    _MEDIA_METRICS: dict[str, tuple[str, ...]] = {
        "FEED": ("reach", "views", "likes", "comments", "saved", "shares", "total_interactions",
                 "profile_visits", "follows"),
        "REELS": ("reach", "views", "likes", "comments", "saved", "shares", "total_interactions",
                  "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"),
        "STORY": ("reach", "views", "replies", "shares", "total_interactions", "navigation",
                  "follows", "profile_visits"),
    }
    _ACCOUNT_METRICS: tuple[str, ...] = (
        "reach", "views", "accounts_engaged", "total_interactions", "profile_views",
        "profile_links_taps", "website_clicks", "follows_and_unfollows",
    )
    INSIGHT_RETRIES = 3  # times a call is retried without the metrics Meta rejected

    def _remember_types(self, items: list[dict]) -> None:
        for item in items:
            if item.get("id") is not None and item.get("media_type"):
                self._media_types_cache[str(item["id"])] = (item["media_type"], item.get("media_product_type"))

    def _media_types(self, media_id: str) -> tuple[str | None, str | None]:
        """(media_type, media_product_type) of one of this account's posts."""
        if media_id not in self._media_types_cache:
            try:
                info = self._get(media_id, {"fields": "media_type,media_product_type"}) or {}
            except GraphAPIError as exc:
                if _is_fatal(exc):
                    raise
                # Type unknown: ask for the feed set and let rejected metrics drop out.
                return None, None
            self._media_types_cache[media_id] = (info.get("media_type"), info.get("media_product_type"))
        return self._media_types_cache[media_id]

    @staticmethod
    def _metric_kind(media_type: str | None, product_type: str | None) -> str:
        """FEED, REELS or STORY: the metric set that fits this media."""
        if product_type in ("REELS", "STORY"):
            return product_type
        if product_type is None and media_type == "VIDEO":
            return "REELS"  # new videos are all reels; older feed videos say FEED
        return "FEED"

    def _insights(self, path: str, metrics: tuple[str, ...], params: dict[str, Any] | None = None,
                  skip: set[str] | None = None) -> dict:
        """GET an insights edge for `metrics`.

        Meta fails the whole call over a single metric it doesn't offer (code 100 naming
        it), so rejected metrics are dropped and the call retried, at most INSIGHT_RETRIES
        times. `skip` holds metrics already known to fail and learns the new ones. Returns
        `insights`, plus `dropped_metrics` and `not_enough_viewers` when they apply.
        """
        skip = set() if skip is None else skip
        asked = [m for m in metrics if m not in skip]
        data: Any = None
        flags: dict[str, Any] = {}
        for attempt in range(self.INSIGHT_RETRIES + 1):
            if not asked:
                break
            try:
                data = self._get(path, {**(params or {}), "metric": ",".join(asked)})
                break
            except GraphAPIError as exc:
                if _not_enough_viewers(exc):
                    flags["not_enough_viewers"] = True
                    break
                rejected = _rejected_metrics(exc, asked)
                if not rejected or attempt == self.INSIGHT_RETRIES:
                    raise
                skip.update(rejected)
                asked = [m for m in asked if m not in skip]
        out: dict[str, Any] = {"insights": self._flatten_insights(data)}
        dropped = [m for m in metrics if m in skip]
        if dropped:
            out["dropped_metrics"] = dropped
        out.update(flags)
        return out

    def profile_show(self) -> dict:
        me = self._me_id()
        data = self._get(me, {
            "fields": "id,username,name,biography,website,profile_picture_url,"
                      "followers_count,follows_count,media_count",
        })
        out = self._normalize_user(data)
        out["website"] = data.get("website")
        out["is_business"] = True  # Graph API access implies a professional account
        return out

    def analytics_insights(self, media_ref: str) -> dict:
        """Insights for one of this account's posts, with the metrics its type supports.

        A post too new or too small for insights gives not_enough_viewers instead of an
        error (Meta reports it as code 10, the same code as a missing permission).
        """
        media_id = self._media_ref(media_ref)
        media_type, product_type = self._media_types(media_id)
        out: dict[str, Any] = {"media_id": media_id}
        if media_type:
            out["media_type"] = media_type
        if product_type:
            out["product_type"] = product_type
        kind = self._metric_kind(media_type, product_type)
        out.update(self._insights(f"{media_id}/insights", self._MEDIA_METRICS[kind]))
        return out

    def analytics_account(self, day: str | None = None) -> dict:
        """Account totals for one UTC day, YYYY-MM-DD (default: today so far)."""
        me = self._me_id()
        day, since, until = self._utc_day(day)
        out: dict[str, Any] = {"period": "day", "day": day}
        out.update(self._insights(f"{me}/insights", self._ACCOUNT_METRICS, {
            "period": "day",
            "metric_type": "total_value",
            "since": str(since),
            "until": str(until),
        }))
        return out

    def _utc_day(self, day: str | None) -> tuple[str, int, int]:
        """(YYYY-MM-DD, since, until) bounding a UTC day."""
        now = getattr(self, "_now", time.time)()
        if day:
            start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            start = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since = int(start.timestamp())
        if since > now:
            raise ValueError(f"{start:%Y-%m-%d} (UTC) hasn't started yet")
        # Meta can't report on time that hasn't happened: today's range ends now.
        until = max(since + 1, min(since + 86400, int(now)))
        return f"{start:%Y-%m-%d}", since, until

    _SUMMARY_MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink"

    def analytics_summary(self, limit: int = 12) -> dict:
        """Profile counts, today's account insights, the latest `limit` posts and the
        current stories, each with the insights its type has.

        About limit + stories + 4 Graph calls: profile, today, the post list, the story
        list, then one insights call per post and per story. Add one for a Facebook-login
        token with no stored IG user id, and a retry per media type for a metric Meta
        rejects (learned once, then left out). Dead tokens and rate limits stop the
        summary, and so does a refused profile or post list (raised with needs_new_token);
        any other failure is reported where it happened.
        """
        try:
            out: dict[str, Any] = {"profile": self._summary_profile()}
        except GraphAPIError as exc:
            if exc.code in _PERMISSION_CODES:
                # The first break on 3 Nov 2026: "(#200) API access blocked" on every call.
                raise _cut_off(exc, "Meta refuses this account's own profile") from exc
            raise
        me = self._me_id()
        errors: dict[str, str] = {}
        try:
            out["today"] = self.analytics_account()
        except GraphAPIError as exc:
            if _is_fatal(exc):
                raise
            errors["today"] = str(exc)
        try:
            posts = self._get(f"{me}/media", {"fields": self._SUMMARY_MEDIA_FIELDS,
                                              "limit": str(limit)}).get("data", [])
        except GraphAPIError as exc:
            if exc.code in _PERMISSION_CODES:
                # The second break on 3 Nov 2026: profile fine, posts refused with (#10).
                raise _cut_off(exc, "Meta reads this account's profile but refuses its posts") from exc
            raise
        skip: dict[str, set[str]] = {}  # media group -> metrics Meta rejected for it this run
        out["media"] = [
            self._summary_entry(p, self._metric_kind(p.get("media_type"), p.get("media_product_type")), skip)
            for p in posts[:limit]
        ]
        try:
            stories = self._get(f"{me}/stories", {
                "fields": "id,media_type,media_product_type,timestamp,permalink",
            }).get("data", [])
        except GraphAPIError as exc:
            if _is_fatal(exc):
                raise
            errors["stories"] = str(exc)
            stories = []
        out["stories"] = [self._summary_entry(s, "STORY", skip) for s in stories]
        dropped = {
            group: [m for m in self._MEDIA_METRICS[self._group_kind(group)] if m in bad]
            for group, bad in skip.items() if bad
        }
        if dropped:
            out["dropped_metrics"] = dropped
        if errors:
            out["errors"] = errors
        return out

    def _summary_profile(self) -> dict:
        fields = "id,username,followers_count,follows_count,media_count"
        if self._me_id_cache is None and self._login_type == "ig":
            # With Instagram login /me is the IG user, so one call also finds its id.
            data = self._get("me", {"fields": fields}) or {}
            self._me_id_cache = data["id"]
        else:
            data = self._get(self._me_id(), {"fields": fields}) or {}
        return {
            "username": data.get("username"),
            "followers_count": data.get("followers_count"),
            "following_count": data.get("follows_count"),
            "media_count": data.get("media_count"),
        }

    @staticmethod
    def _media_group(kind: str, media_type: str | None) -> str:
        """Media Meta treats alike for metrics: REELS, STORY, or each feed media type
        (a metric a carousel lacks may still work on an image)."""
        return (media_type or kind) if kind == "FEED" else kind

    @staticmethod
    def _group_kind(group: str) -> str:
        return group if group in ("REELS", "STORY") else "FEED"

    def _summary_entry(self, item: dict, kind: str, skip: dict[str, set[str]]) -> dict:
        entry: dict[str, Any] = {
            "id": str(item["id"]),
            "media_type": item.get("media_type"),
            "product_type": item.get("media_product_type"),
            "timestamp": item.get("timestamp"),
            "caption": _first_line(item.get("caption")),
            "permalink": item.get("permalink"),
        }
        group = self._media_group(kind, entry["media_type"])
        try:
            found = self._insights(f"{entry['id']}/insights", self._MEDIA_METRICS[kind],
                                   skip=skip.setdefault(group, set()))
        except GraphAPIError as exc:
            if _is_fatal(exc):
                raise
            # e.g. a post from before the switch to a business account: note it, carry on.
            entry["insights_error"] = str(exc)
            return entry
        found.pop("dropped_metrics", None)  # reported once per media group, at the top
        entry.update(found)
        return entry

    @staticmethod
    def _flatten_insights(data: Any) -> dict:
        out: dict[str, Any] = {}
        for item in (data or {}).get("data", []):
            name = item.get("name")
            if "total_value" in item:
                out[name] = (item.get("total_value") or {}).get("value")
            else:
                values = item.get("values") or []
                out[name] = values[-1].get("value") if values else None
        return out

    # ------------------------------------------------------------------
    # Followers
    # ------------------------------------------------------------------

    def followers_list(self, limit: int = 100, user: str = "") -> list[dict]:
        # Graph API does not expose a full followers list.
        # Only business discovery can show follower counts.
        raise NotImplementedError(
            "The Graph API does not provide a full followers list. "
            "Use --backend private for this feature."
        )

    def followers_following(self, limit: int = 100, user: str = "") -> list[dict]:
        raise NotImplementedError(
            "The Graph API does not provide a following list. "
            "Use --backend private for this feature."
        )

    def follow(self, user: str) -> dict:
        raise NotImplementedError(
            "The Graph API does not support follow actions. "
            "Use --backend private for this feature."
        )

    def unfollow(self, user: str) -> dict:
        raise NotImplementedError(
            "The Graph API does not support unfollow actions. "
            "Use --backend private for this feature."
        )

    # ------------------------------------------------------------------
    # User
    # ------------------------------------------------------------------

    def user_info(self, username: str) -> dict:
        self._validate_username(username)
        me = self._me_id()
        params = {
            "fields": f"business_discovery.fields(id,username,name,biography,followers_count,follows_count,media_count,profile_picture_url).username({username})",
        }
        data = self._get(me, params)
        business = data.get("business_discovery", {})
        return self._normalize_user(business) if business else {}

    def user_search(self, query: str) -> list[dict]:
        # Graph API has no direct user search endpoint.
        # Use business_discovery as a single-user lookup fallback.
        if any(ch.isspace() for ch in query.strip()):
            return []
        try:
            info = self.user_info(query)
            return [info] if info else []
        except GraphAPIError:
            return []

    def user_posts(self, username: str, limit: int = 20) -> list[dict]:
        self._validate_username(username)
        me = self._me_id()
        params = {
            "fields": f"business_discovery.fields(media.limit({limit}){{id,caption,media_type,media_product_type,media_url,thumbnail_url,timestamp,like_count,comments_count,permalink,shortcode,children{{id,media_type,media_url}}}}).username({username})",
        }
        data = self._get(me, params)
        business = data.get("business_discovery", {})
        media = business.get("media", {})
        return [self._normalize_media(item) for item in media.get("data", [])]

    # ------------------------------------------------------------------
    # Engagement
    # ------------------------------------------------------------------

    def like_post(self, media_id: str) -> dict:
        raise NotImplementedError(
            "The Graph API does not support liking posts. "
            "Use --backend private for this feature."
        )

    def unlike_post(self, media_id: str) -> dict:
        raise NotImplementedError(
            "The Graph API does not support unliking posts. "
            "Use --backend private for this feature."
        )

    def comments_add(self, media_id: str, text: str) -> dict:
        media_id = self._media_ref(media_id)
        result = self._post(f"{media_id}/comments", {"message": text})
        return {"id": result["id"], "status": "commented"}

    # ------------------------------------------------------------------
    # Hashtag browsing
    # ------------------------------------------------------------------

    def _hashtag_id(self, tag: str) -> str | None:
        """Look up a hashtag ID (requires user_id per Meta docs)."""
        me = self._me_id()
        search = self._get("ig_hashtag_search", {"q": tag, "user_id": me})
        results = search.get("data", [])
        return results[0]["id"] if results else None

    def hashtag_top(self, tag: str, limit: int = 20) -> list[dict]:
        hashtag_id = self._hashtag_id(tag)
        if not hashtag_id:
            return []
        me = self._me_id()
        params = {
            "fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,timestamp,like_count,comments_count,permalink,shortcode,children{id,media_type,media_url}",
            "user_id": me,
            "limit": str(limit),
        }
        data = self._get(f"{hashtag_id}/top_media", params)
        return [self._normalize_media(item) for item in data.get("data", [])]

    def hashtag_recent(self, tag: str, limit: int = 20) -> list[dict]:
        hashtag_id = self._hashtag_id(tag)
        if not hashtag_id:
            return []
        me = self._me_id()
        params = {
            "fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,timestamp,like_count,comments_count,permalink,shortcode,children{id,media_type,media_url}",
            "user_id": me,
            "limit": str(limit),
        }
        data = self._get(f"{hashtag_id}/recent_media", params)
        return [self._normalize_media(item) for item in data.get("data", [])]

    def media_download(self, media_ref: str, output_dir: str = "") -> dict:
        raise NotImplementedError(
            "The Graph API cannot download media (CDN URLs require an authenticated "
            "session). Use --backend private for this feature."
        )
