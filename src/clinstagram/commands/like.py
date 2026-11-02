from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import _require_growth, dispatch, make_subgroup

like_app = make_subgroup("Like and unlike posts")


@like_app.command("post")
def like_post(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL to like"),
):
    """Like a post. Requires --enable-growth-actions."""
    _require_growth(ctx)
    dispatch(ctx, Feature.LIKE_POST, lambda b: b.like_post(media_id))


@like_app.command("undo")
def unlike_post(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL to unlike"),
):
    """Unlike a post. Requires --enable-growth-actions."""
    _require_growth(ctx)
    dispatch(ctx, Feature.UNLIKE_POST, lambda b: b.unlike_post(media_id))

