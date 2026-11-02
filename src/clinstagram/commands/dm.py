from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, preferred_private_backend, stage, strip_at
from clinstagram.media import is_url

dm_app = make_subgroup("Manage direct messages")


def _is_numeric_identifier(value: str) -> bool:
    return value.isdigit()


def _find_thread_id(threads: list[dict], username: str) -> str | None:
    target = username.lower()
    for thread in threads:
        participants = thread.get("participants") or thread.get("users") or []
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            candidate = participant.get("username")
            if candidate and candidate.lower() == target:
                return str(thread["thread_id"])
        title = (thread.get("thread_title") or thread.get("username") or "").lower()
        if title == target or target in title.split(", "):
            return str(thread["thread_id"])
    return None


@dm_app.command("inbox")
def inbox(
    ctx: typer.Context,
    unread: bool = typer.Option(False, "--unread", help="Show only unread threads"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max threads to return"),
):
    """List DM inbox threads."""
    dispatch(ctx, Feature.DM_INBOX, lambda b: b.dm_inbox(limit, unread))


@dm_app.command("thread")
def thread(
    ctx: typer.Context,
    thread_id: str = typer.Argument(..., help="Thread ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max messages to return"),
):
    """View messages in a DM thread."""
    target = strip_at(thread_id)
    preferred_backend = None
    if not _is_numeric_identifier(target):
        preferred_backend = preferred_private_backend(ctx, Feature.DM_THREAD)

    def _thread(backend):
        resolved = target
        if not _is_numeric_identifier(target):
            threads = backend.dm_inbox(100, False)
            resolved = _find_thread_id(threads, target) or ""
            if not resolved:
                raise ValueError(
                    f"No thread found for '{thread_id}'. Run 'clinstagram dm inbox' and use the returned thread_id."
                )
        return backend.dm_thread(resolved, limit)

    dispatch(ctx, Feature.DM_THREAD, _thread, preferred_backend=preferred_backend)


@dm_app.command("send")
def send(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to message (with or without @)"),
    message: str = typer.Argument(..., help="Message text"),
):
    """Send a text DM."""
    target = strip_at(user)
    feature = Feature.DM_REPLY if _is_numeric_identifier(target) else Feature.DM_COLD_SEND
    dispatch(ctx, feature, lambda b: b.dm_send(target, message))


@dm_app.command("send-media")
def send_media(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to message (with or without @)"),
    media: str = typer.Argument(..., help="Media path or URL"),
):
    """Send a media DM (photo/video)."""
    target = strip_at(user)
    preferred_backend = None
    if not _is_numeric_identifier(target) or not is_url(media):
        preferred_backend = preferred_private_backend(ctx, Feature.DM_SEND_MEDIA)
    dispatch(
        ctx,
        Feature.DM_SEND_MEDIA,
        lambda b: b.dm_send_media(target, stage(media, ctx.obj["_backend_name"])),
        preferred_backend=preferred_backend,
    )


@dm_app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
):
    """Search DM threads by keyword."""
    def _search(b):
        threads = b.dm_inbox(100, False)
        q = query.lower()
        return [t for t in threads if q in str(t).lower()]
    dispatch(ctx, Feature.DM_SEARCH, _search)


@dm_app.command("pending")
def pending(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Max requests to return"),
):
    """List message requests (DMs from people you don't follow)."""
    dispatch(ctx, Feature.DM_PENDING, lambda b: b.dm_pending(limit))


@dm_app.command("approve")
def approve(
    ctx: typer.Context,
    thread_id: str = typer.Argument(..., help="Thread ID from 'dm pending'"),
):
    """Accept a message request so it moves to the main inbox."""
    dispatch(ctx, Feature.DM_APPROVE, lambda b: b.dm_approve(thread_id))


@dm_app.command("seen")
def seen(
    ctx: typer.Context,
    thread_id: str = typer.Argument(..., help="Thread ID"),
):
    """Mark a DM thread as read."""
    dispatch(ctx, Feature.DM_SEEN, lambda b: b.dm_seen(thread_id))
