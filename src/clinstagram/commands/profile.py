from __future__ import annotations

from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, stage

profile_app = make_subgroup("View and edit your own profile")


@profile_app.command("show")
def show(ctx: typer.Context):
    """Show your profile: bio, website, picture, counts, account type."""
    dispatch(ctx, Feature.PROFILE_SHOW, lambda b: b.profile_show())


@profile_app.command("edit")
def edit(
    ctx: typer.Context,
    bio: Optional[str] = typer.Option(None, "--bio", help="New biography (pass \"\" to clear)"),
    name: Optional[str] = typer.Option(None, "--name", help="New display name"),
    website: Optional[str] = typer.Option(None, "--website", help="New website / link in bio"),
):
    """Edit bio, display name and/or website. Only the fields you pass change."""
    dispatch(ctx, Feature.PROFILE_EDIT, lambda b: b.profile_edit(bio=bio, name=name, website=website))


@profile_app.command("picture")
def picture(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Image path or URL (square JPG works best)"),
):
    """Change your profile picture."""
    dispatch(
        ctx,
        Feature.PROFILE_PICTURE,
        lambda b: b.profile_picture(stage(path, ctx.obj["_backend_name"])),
    )
