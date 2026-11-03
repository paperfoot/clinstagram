from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import typer

from clinstagram.backends.capabilities import Feature
from clinstagram.commands._dispatch import dispatch, make_subgroup, output_error
from clinstagram.models import CLIError, ExitCode

analytics_app = make_subgroup("View analytics and insights")

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    media_id: str = typer.Argument(..., help="Media ID, shortcode, or post URL ('latest' on Graph)"),
):
    """Reach, views, saves, shares for one of your posts, plus watch time for reels and
    replies and navigation for stories. A post with too few viewers yet returns
    not_enough_viewers: true (exit 0). Needs a professional account."""
    dispatch(ctx, Feature.ANALYTICS_INSIGHTS, lambda b: b.analytics_insights(media_id))


def _day_option(ctx: typer.Context, day: Optional[str]) -> Optional[str]:
    """Check --day: a UTC date, YYYY-MM-DD, that has started."""
    if day is None:
        return None
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").date() if _DAY.fullmatch(day) else None
    except ValueError:
        parsed = None
    if parsed is None:
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"--day must be a date like 2026-11-03, got {day!r}",
        ))
    if parsed > datetime.now(timezone.utc).date():
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"--day {day} hasn't started yet in UTC",
            remediation="Pick today or an earlier day (UTC)",
        ))
    return parsed.isoformat()


@analytics_app.command("account")
def account(
    ctx: typer.Context,
    day: Optional[str] = typer.Option(
        None, "--day", help="UTC day, YYYY-MM-DD (default: today so far). Graph API only"
    ),
):
    """Account insights for one UTC day: reach, views, accounts engaged, interactions,
    profile views, link taps, website clicks, follows and unfollows. Needs a professional account."""
    day = _day_option(ctx, day)

    def action(b):
        if ctx.obj["_backend_name"].startswith("graph"):
            return b.analytics_account(day=day)
        if day:
            output_error(ctx, CLIError(
                exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
                error="--day needs the Graph API; the private API's account insights cover a fixed recent window",
                remediation="Drop --day, or use an account with a graph_* backend (clinstagram account list)",
            ))
        return b.analytics_account()

    dispatch(ctx, Feature.ANALYTICS_ACCOUNT, action)
