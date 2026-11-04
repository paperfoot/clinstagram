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

    # The private API can't answer for a given day, so a Graph refusal of --day is the answer.
    dispatch(ctx, Feature.ANALYTICS_ACCOUNT, action, private_fallback=day is None)


SUMMARY_MAX_LIMIT = 50  # one page of posts, and a bounded number of insights calls


@analytics_app.command("summary")
def summary(
    ctx: typer.Context,
    limit: int = typer.Option(12, "--limit", "-n", help=f"Latest posts to include (1-{SUMMARY_MAX_LIMIT})"),
):
    """Dashboard in one command: profile counts, today's account insights (UTC, so far),
    the latest N posts (type, time, first caption line, permalink, insights) and current
    stories (insights, or not_enough_viewers). Graph API only. Costs about N + S + 4
    Graph calls, S being the current stories; one more for a Facebook-login token with
    no stored IG user id, plus a retry per media type when Meta rejects a metric."""
    if not 1 <= limit <= SUMMARY_MAX_LIMIT:
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"--limit must be between 1 and {SUMMARY_MAX_LIMIT}, got {limit}",
        ))

    def action(b):
        if not ctx.obj["_backend_name"].startswith("graph"):
            acct = ctx.obj.get("account") or "default"
            output_error(ctx, CLIError(
                exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
                error="analytics summary runs on the Graph API (graph_ig or graph_fb), not the private API",
                remediation=(f"Drop --backend private, or connect Graph: clinstagram --account {acct} "
                             "auth connect-ig --token <token>. For one post: clinstagram analytics insights <ref>"),
            ))
        return b.analytics_summary(limit)

    dispatch(ctx, Feature.ANALYTICS_SUMMARY, action, render=_print_summary, private_fallback=False)


# ── Readable summary (no --json) ────────────────────────────────────

_POST_COLUMNS = (("reach", "Reach"), ("views", "Views"), ("likes", "Likes"),
                 ("comments", "Comments"), ("saved", "Saves"), ("shares", "Shares"))
_STORY_COLUMNS = (("reach", "Reach"), ("views", "Views"), ("replies", "Replies"),
                  ("shares", "Shares"), ("navigation", "Taps"), ("follows", "Follows"))
_TYPE_LABELS = {"CAROUSEL_ALBUM": "CAROUSEL", "REELS": "REEL"}


def _n(value) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


def _when(timestamp: str | None) -> str:
    # Graph timestamps look like 2026-11-03T09:15:00+0000 (UTC)
    return f"{timestamp[:10]} {timestamp[11:16]}" if timestamp and len(timestamp) >= 16 else "?"


def _media_rows(title: str, items: list[dict], columns: tuple) -> list[str]:
    head = f"  {'When (UTC)':<16}  {'Type':<8}" + "".join(f"{label:>9}" for _, label in columns)
    lines = [title, head]
    indent = " " * 2
    for item in items:
        kind = item.get("product_type") if item.get("product_type") in ("REELS", "STORY") else item.get("media_type")
        insights = item.get("insights") or {}
        cells = "".join(f"{_n(insights.get(key)):>9}" for key, _ in columns)
        lines.append(f"{indent}{_when(item.get('timestamp')):<16}  {_TYPE_LABELS.get(kind, kind or '?'):<8}{cells}")
        notes = [item[k] for k in ("caption", "permalink") if item.get(k)]
        if item.get("not_enough_viewers"):
            notes.append("no insights yet: not enough viewers")
        if item.get("insights_error"):
            notes.append(f"insights unavailable: {item['insights_error']}")
        lines += [f"{indent}  {note}" for note in notes]
    return lines


def summary_lines(data: dict) -> list[str]:
    """The summary as text lines for a terminal."""
    profile = data.get("profile") or {}
    errors = data.get("errors") or {}
    lines = [
        f"@{profile.get('username') or '?'}  followers {_n(profile.get('followers_count'))}"
        f"  following {_n(profile.get('following_count'))}  posts {_n(profile.get('media_count'))}"
    ]
    today = data.get("today")
    if today:
        stats = "  ".join(f"{name.replace('_', ' ')} {_n(value)}"
                          for name, value in (today.get("insights") or {}).items())
        lines.append(f"Today ({today.get('day')} UTC, so far): {stats or 'no data yet'}")
    elif errors.get("today"):
        lines.append(f"Today: unavailable ({errors['today']})")
    media = data.get("media") or []
    lines += [""] + (_media_rows(f"Latest {len(media)} posts", media, _POST_COLUMNS) if media else ["No posts yet"])
    stories = data.get("stories") or []
    lines.append("")
    if stories:
        lines += _media_rows(f"Stories up now ({len(stories)})", stories, _STORY_COLUMNS)
    elif errors.get("stories"):
        lines.append(f"Stories: unavailable ({errors['stories']})")
    else:
        lines.append("Stories up now: none")
    dropped = data.get("dropped_metrics") or {}
    if dropped:
        lines += ["", "Meta didn't offer: " + "; ".join(
            f"{_TYPE_LABELS.get(group, group).lower()} {', '.join(names)}" for group, names in dropped.items())]
    return lines


def _print_summary(data: dict) -> None:
    for line in summary_lines(data):
        typer.echo(line)
