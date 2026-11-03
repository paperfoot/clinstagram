from __future__ import annotations

from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import (
    dispatch,
    make_subgroup,
    output_error,
    preferred_private_backend,
    resolve_backend_name,
    stage,
)
from clinstagram.media import is_url
from clinstagram.models import CLIError, ExitCode

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


def _reel_cover_error(thumbnail: str, cover: str, backend_name: str | None) -> CLIError | None:
    """Cover options the chosen backend can't honour, refused before anything is uploaded."""
    if cover and thumbnail:
        return CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="--cover and --thumbnail both set the reel's cover; pass only one",
            remediation="--cover takes an image; --thumbnail takes a frame offset in ms (Graph) "
                        "or an image (private API)",
        )
    if not (backend_name or "").startswith("graph"):
        return None
    if cover and not is_url(cover):
        return CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"Graph needs a public image URL for --cover, not a local file: {cover}",
            remediation="Upload the image and pass its https:// URL, or use --backend private",
        )
    if thumbnail and not thumbnail.isdigit():
        return CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"Graph takes --thumbnail as a frame offset in ms, not an image: {thumbnail}",
            remediation="For a cover image use --cover <public image URL>, or use --backend private",
        )
    return None


@post_app.command("reel")
def reel(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Video path or URL"),
    caption: str = typer.Option("", "--caption", "-c", help="Post caption"),
    thumbnail: str = typer.Option(
        "", "--thumbnail",
        help="Graph: frame offset in ms to use as the cover. Private API: cover image path or URL",
    ),
    cover: str = typer.Option(
        "", "--cover",
        help="Cover image. Graph needs a public image URL (cover_url); the private API also "
             "takes a local file. Not with --thumbnail",
    ),
    audio: str = typer.Option("", "--audio", help="Audio name"),
    feed: bool = typer.Option(
        True, "--feed/--no-feed",
        help="Graph: also show the reel on the profile grid and in followers' feeds "
             "(share_to_feed), not only in the Reels tab. Ignored by the private API",
    ),
):
    """Post a reel."""
    if cover and thumbnail:
        output_error(ctx, _reel_cover_error(thumbnail, cover, None))
    preferred_backend = _prefer_private_for_media(ctx, Feature.POST_REEL, path)
    if (thumbnail and not thumbnail.isdigit()) or (cover and not is_url(cover)):
        # A cover image file (or an image as --thumbnail) only works on the private API.
        preferred_backend = preferred_private_backend(ctx, Feature.POST_REEL) or preferred_backend
        chosen = resolve_backend_name(ctx, Feature.POST_REEL, preferred_backend=preferred_backend)
        problem = _reel_cover_error(thumbnail, cover, chosen)
        if problem:
            output_error(ctx, problem)

    def action(b):
        name = ctx.obj["_backend_name"]
        video = stage(path, name)
        if name.startswith("graph"):
            return b.post_reel(video, caption, thumbnail, audio, cover=cover, share_to_feed=feed)
        # The private API takes the cover as its thumbnail image and has no feed switch.
        return b.post_reel(video, caption, _resolve_thumbnail(ctx, thumbnail or cover), audio)

    dispatch(ctx, Feature.POST_REEL, action, preferred_backend=preferred_backend)


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
