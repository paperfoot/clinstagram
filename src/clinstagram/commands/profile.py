from __future__ import annotations

from enum import Enum
from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, output_error, stage
from clinstagram.models import CLIError, ExitCode

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
    path: Optional[str] = typer.Argument(None, help="Image path or URL (square JPG works best)"),
    remove: bool = typer.Option(False, "--remove", help="Remove the current profile picture"),
):
    """Change (or with --remove, delete) your profile picture."""
    if remove == bool(path):
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="Pass either an image PATH|URL or --remove (not both, not neither).",
        ))
        return
    if remove:
        dispatch(ctx, Feature.PROFILE_PICTURE, lambda b: b.profile_picture_remove())
        return
    dispatch(
        ctx,
        Feature.PROFILE_PICTURE,
        lambda b: b.profile_picture(stage(path, ctx.obj["_backend_name"])),
    )


class AccountKind(str, Enum):
    creator = "creator"
    business = "business"


@profile_app.command("convert")
def convert(
    ctx: typer.Context,
    to: AccountKind = typer.Option(..., "--to", help="Professional account type"),
    category_id: Optional[str] = typer.Option(
        None, "--category-id", help="Instagram category id (default: Instagram's generic category)"
    ),
):
    """Switch to a professional (creator/business) account — unlocks insights."""
    dispatch(ctx, Feature.PROFILE_CONVERT, lambda b: b.profile_convert(to.value, category_id))
