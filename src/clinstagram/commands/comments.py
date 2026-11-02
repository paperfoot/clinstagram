from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import _require_growth, dispatch, make_subgroup, preferred_private_backend

comments_app = make_subgroup("Manage comments")


@comments_app.command("list")
def list_comments(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max comments to return"),
):
    """List comments on a post."""
    dispatch(ctx, Feature.COMMENTS_LIST, lambda b: b.comments_list(media_id, limit))


@comments_app.command("reply")
def reply(
    ctx: typer.Context,
    comment_id: str = typer.Argument(..., help="Comment reference from 'comments list'"),
    text: str = typer.Argument(..., help="Reply text"),
):
    """Reply to a comment. Requires --enable-growth-actions."""
    _require_growth(ctx)
    preferred_backend = preferred_private_backend(ctx, Feature.COMMENTS_REPLY) if ":" in comment_id else None
    dispatch(
        ctx,
        Feature.COMMENTS_REPLY,
        lambda b: b.comments_reply(comment_id, text),
        preferred_backend=preferred_backend,
    )


@comments_app.command("add")
def add(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL to comment on"),
    text: str = typer.Argument(..., help="Comment text"),
):
    """Comment on a post. Requires --enable-growth-actions."""
    _require_growth(ctx)
    dispatch(ctx, Feature.COMMENTS_ADD, lambda b: b.comments_add(media_id, text))



@comments_app.command("delete")
def delete(
    ctx: typer.Context,
    comment_id: str = typer.Argument(..., help="Comment reference from 'comments list'"),
):
    """Delete a comment."""
    preferred_backend = preferred_private_backend(ctx, Feature.COMMENTS_DELETE) if ":" in comment_id else None
    dispatch(
        ctx,
        Feature.COMMENTS_DELETE,
        lambda b: b.comments_delete(comment_id),
        preferred_backend=preferred_backend,
    )
