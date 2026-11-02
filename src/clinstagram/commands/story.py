from __future__ import annotations

from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, preferred_private_backend, stage, strip_at
from clinstagram.media import is_url

story_app = make_subgroup("Manage stories")


def _prefer_private_for_story(ctx: typer.Context, feature: Feature, path: str, link: str) -> str | None:
    if link or not is_url(path):
        return preferred_private_backend(ctx, feature)
    return None


@story_app.command("list")
def list_stories(
    ctx: typer.Context,
    user: str = typer.Argument("", help="Username (omit for own stories)"),
):
    """List stories for a user (or yourself)."""
    clean_user = strip_at(user) if user else ""
    feature = Feature.STORY_VIEW_OTHERS if clean_user else Feature.STORY_LIST
    dispatch(ctx, feature, lambda b: b.story_list(clean_user))


@story_app.command("post-photo")
def post_photo(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Photo path or URL"),
    mention: Optional[list[str]] = typer.Option(None, "--mention", "-m", help="Mention users"),
    link: str = typer.Option("", "--link", help="Swipe-up link URL"),
):
    """Post a photo story."""
    mentions = [strip_at(m) for m in mention] if mention else None
    dispatch(
        ctx,
        Feature.STORY_POST,
        lambda b: b.story_post_photo(stage(path, ctx.obj["_backend_name"]), mentions, link),
        preferred_backend=_prefer_private_for_story(ctx, Feature.STORY_POST, path, link),
    )


@story_app.command("post-video")
def post_video(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Video path or URL"),
    mention: Optional[list[str]] = typer.Option(None, "--mention", "-m", help="Mention users"),
    link: str = typer.Option("", "--link", help="Swipe-up link URL"),
):
    """Post a video story."""
    mentions = [strip_at(m) for m in mention] if mention else None
    dispatch(
        ctx,
        Feature.STORY_POST,
        lambda b: b.story_post_video(stage(path, ctx.obj["_backend_name"]), mentions, link),
        preferred_backend=_prefer_private_for_story(ctx, Feature.STORY_POST, path, link),
    )


@story_app.command("viewers")
def viewers(
    ctx: typer.Context,
    story_id: str = typer.Argument(..., help="Story ID"),
):
    """List viewers of a story."""
    dispatch(ctx, Feature.STORY_VIEWERS, lambda b: b.story_viewers(story_id))


@story_app.command("delete")
def delete(
    ctx: typer.Context,
    story_id: str = typer.Argument(..., help="Story ID (from story list)"),
):
    """Delete one of your stories."""
    dispatch(ctx, Feature.STORY_DELETE, lambda b: b.story_delete(story_id))
