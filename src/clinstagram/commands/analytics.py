from __future__ import annotations

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup

analytics_app = make_subgroup("View analytics and insights")


@analytics_app.command("profile")
def profile(ctx: typer.Context):
    """Show profile analytics (followers, posts, engagement)."""
    dispatch(ctx, Feature.ANALYTICS_PROFILE, lambda b: b.analytics_profile())


@analytics_app.command("post")
def post(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, post URL, or 'latest'"),
):
    """Show analytics for a specific post."""
    dispatch(ctx, Feature.ANALYTICS_POST, lambda b: b.analytics_post(media_id))


@analytics_app.command("hashtag")
def hashtag(
    ctx: typer.Context,
    tag: str = typer.Argument(..., help="Hashtag to analyze (without #)"),
):
    """Show hashtag analytics."""
    dispatch(ctx, Feature.ANALYTICS_HASHTAG, lambda b: b.analytics_hashtag(tag))


@analytics_app.command("insights")
def insights(
    ctx: typer.Context,
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL (Graph: numeric ID)"),
):
    """Reach, views, saves, shares for one of your posts. Needs a professional account."""
    dispatch(ctx, Feature.ANALYTICS_INSIGHTS, lambda b: b.analytics_insights(media_id))


@analytics_app.command("account")
def account(ctx: typer.Context):
    """Account-level insights (reach, engagement). Needs a professional account."""
    dispatch(ctx, Feature.ANALYTICS_ACCOUNT, lambda b: b.analytics_account())
