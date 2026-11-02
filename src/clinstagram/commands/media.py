from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup

media_app = make_subgroup("Media download and inspection")


@media_app.command("download")
def download(
    ctx: typer.Context,
    media_ref: str = typer.Argument(
        ...,
        help="Media ID, shortcode, or full instagram.com/p/XXX/ URL",
    ),
    output: str = typer.Option(
        "",
        "--output",
        "-o",
        help="Output directory (default: current working directory)",
    ),
):
    """Download a post, reel, or carousel to local disk.

    Returns JSON with the list of files downloaded. For carousels, all
    children are downloaded. For videos/reels, the fresh video URL is
    resolved automatically so expired CDN links are not an issue.
    """
    dispatch(
        ctx,
        Feature.MEDIA_DOWNLOAD,
        lambda b: b.media_download(media_ref, output_dir=output),
    )


@media_app.command("likers")
def likers(
    ctx: typer.Context,
    media_ref: str = typer.Argument(..., help="Media ID, shortcode, or post URL"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max users to return"),
):
    """List accounts that liked a post."""
    dispatch(ctx, Feature.MEDIA_LIKERS, lambda b: b.media_likers(media_ref, limit))
