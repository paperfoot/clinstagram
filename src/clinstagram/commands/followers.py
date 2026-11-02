from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import _require_growth, dispatch, make_subgroup, strip_at
from clinstagram.models import CLIError, ExitCode

followers_app = make_subgroup("Manage followers")


@followers_app.command("list")
def list_followers(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", "-n", help="Max followers to return"),
    user: str = typer.Option("", "--user", "-u", help="Another account (default: you)"),
):
    """List followers of your account, or of --user."""
    target = strip_at(user)
    dispatch(ctx, Feature.FOLLOWERS_LIST, lambda b: b.followers_list(limit, user=target))


@followers_app.command("following")
def following(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", "-n", help="Max accounts to return"),
    user: str = typer.Option("", "--user", "-u", help="Another account (default: you)"),
):
    """List accounts you (or --user) follow."""
    target = strip_at(user)
    dispatch(ctx, Feature.FOLLOWERS_FOLLOWING, lambda b: b.followers_following(limit, user=target))


@followers_app.command("follow")
def follow(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to follow (with or without @)"),
):
    """Follow a user. Requires --enable-growth-actions."""
    _require_growth(ctx)
    dispatch(ctx, Feature.FOLLOW, lambda b: b.follow(strip_at(user)))


@followers_app.command("unfollow")
def unfollow(
    ctx: typer.Context,
    user: str = typer.Argument(..., help="Username to unfollow (with or without @)"),
):
    """Unfollow a user. Requires --enable-growth-actions."""
    _require_growth(ctx)
    dispatch(ctx, Feature.UNFOLLOW, lambda b: b.unfollow(strip_at(user)))
