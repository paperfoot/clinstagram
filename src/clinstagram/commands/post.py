from __future__ import annotations

from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, preferred_private_backend, stage
from clinstagram.media import is_url

post_app = make_subgroup("Post photos, videos, reels, carousels")


def _prefer_private_for_media(ctx: typer.Context, feature: Feature, *sources: str) -> str | None:
    if any(source and not is_url(source) for source in sources):
        return preferred_private_backend(ctx, feature)
    return None


def _resolve_thumbnail(ctx: typer.Context, thumbnail: str) -> str:
    if not thumbnail:
        return ""
    if ctx.obj["_backend_name"].startswith("graph"):
        return thumbnail
    return stage(thumbnail, ctx.obj["_backend_name"])


@post_app.command("photo")
def photo(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Image path or URL"),
    caption: str = typer.Option("", "--caption", "-c", help="Post caption"),
    location: str = typer.Option("", "--location", "-l", help="Location ID"),
    tags: Optional[list[str]] = typer.Option(None, "--tags", "-t", help="User tags"),
):
    """Post a photo."""
    dispatch(
        ctx,
        Feature.POST_PHOTO,
        lambda b: b.post_photo(stage(path, ctx.obj["_backend_name"]), caption, location, tags),
        preferred_backend=_prefer_private_for_media(ctx, Feature.POST_PHOTO, path),
    )


@post_app.command("video")
def video(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Video path or URL"),
    caption: str = typer.Option("", "--caption", "-c", help="Post caption"),
    thumbnail: str = typer.Option("", "--thumbnail", help="Thumbnail path or URL"),
    location: str = typer.Option("", "--location", "-l", help="Location ID"),
):
    """Post a video."""
    preferred_backend = _prefer_private_for_media(ctx, Feature.POST_VIDEO, path)
    if thumbnail and not thumbnail.isdigit():
        preferred_backend = preferred_private_backend(ctx, Feature.POST_VIDEO) or preferred_backend
    dispatch(
        ctx,
        Feature.POST_VIDEO,
        lambda b: b.post_video(
            stage(path, ctx.obj["_backend_name"]),
            caption,
            _resolve_thumbnail(ctx, thumbnail),
            location,
        ),
        preferred_backend=preferred_backend,
    )


@post_app.command("reel")
def reel(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Video path or URL"),
    caption: str = typer.Option("", "--caption", "-c", help="Post caption"),
    thumbnail: str = typer.Option("", "--thumbnail", help="Thumbnail path or URL"),
    audio: str = typer.Option("", "--audio", help="Audio name"),
):
    """Post a reel."""
    preferred_backend = _prefer_private_for_media(ctx, Feature.POST_REEL, path)
    if thumbnail and not thumbnail.isdigit():
        preferred_backend = preferred_private_backend(ctx, Feature.POST_REEL) or preferred_backend
    dispatch(
        ctx,
        Feature.POST_REEL,
        lambda b: b.post_reel(
            stage(path, ctx.obj["_backend_name"]),
            caption,
            _resolve_thumbnail(ctx, thumbnail),
            audio,
        ),
        preferred_backend=preferred_backend,
    )


@post_app.command("carousel")
def carousel(
    ctx: typer.Context,
    paths: list[str] = typer.Argument(..., help="Image/video paths or URLs"),
    caption: str = typer.Option("", "--caption", "-c", help="Post caption"),
):
    """Post a carousel (multiple images/videos)."""
    dispatch(
        ctx,
        Feature.POST_CAROUSEL,
        lambda b: b.post_carousel([stage(p, ctx.obj["_backend_name"]) for p in paths], caption),
        preferred_backend=_prefer_private_for_media(ctx, Feature.POST_CAROUSEL, *paths),
    )


@post_app.command("delete")
def delete(
    ctx: typer.Context,
    media_ref: str = typer.Argument(..., help="Media ID, shortcode, or post URL"),
):
    """Delete one of your posts or reels. Cannot be undone."""
    dispatch(ctx, Feature.POST_DELETE, lambda b: b.post_delete(media_ref))


@post_app.command("edit")
def edit(
    ctx: typer.Context,
    media_ref: str = typer.Argument(..., help="Media ID, shortcode, or post URL"),
    caption: str = typer.Option(..., "--caption", "-c", help="New caption (replaces the old one)"),
):
    """Replace the caption of one of your posts."""
    dispatch(ctx, Feature.POST_EDIT, lambda b: b.post_edit(media_ref, caption))
