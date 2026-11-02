from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, strip_at

user_app = make_subgroup("User lookup and search")


@user_app.command("info")
def info(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to look up (with or without @)"),
):
    """Get detailed info for a user."""
    dispatch(ctx, Feature.USER_INFO, lambda b: b.user_info(strip_at(username)))


@user_app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(20, "--limit", help="Maximum number of users to return"),
):
    """Search for users by name or username."""
    dispatch(ctx, Feature.USER_SEARCH, lambda b: b.user_search(query)[:limit])


@user_app.command("posts")
def posts(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username to look up (with or without @)"),
    limit: int = typer.Option(20, "--limit", help="Number of posts to list"),
):
    """List a user's recent media."""
    dispatch(ctx, Feature.USER_POSTS, lambda b: b.user_posts(strip_at(username), limit=limit))


@user_app.command("tagged")
def tagged(
    ctx: typer.Context,
    username: str = typer.Argument(..., help="Username (with or without @)"),
    limit: int = typer.Option(20, "--limit", help="Number of posts to list"),
):
    """List posts other accounts tagged this user in."""
    dispatch(ctx, Feature.USER_TAGGED, lambda b: b.user_tagged(strip_at(username), limit=limit))
