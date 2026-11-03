"""`clinstagram safety`: read-only view of the private-API write-safety ledger."""
from __future__ import annotations

from typing import Optional

import typer

from clinstagram.commands._dispatch import make_subgroup, output_success

safety_app = make_subgroup(
    "Write safety for private-API writes: caps, current usage and any cooldown "
    "after an Instagram block (override: CLINSTAGRAM_IGNORE_COOLDOWN=1)"
)


@safety_app.command("status")
def status(
    ctx: typer.Context,
    account: Optional[str] = typer.Option(
        None, "--account", help="Account to show (default: the global --account / default_account)"
    ),
):
    """Show caps, usage in each rolling window and any active cooldown. Read-only."""
    from clinstagram.safety import SafetyGuard

    name = account or ctx.obj["account"]
    guard = SafetyGuard(name, ctx.obj["config"].rate_limits, ctx.obj.get("config_dir"))
    data = guard.status()
    if ctx.obj["json"]:
        output_success(ctx, data)
        return
    cd = data["cooldown"]
    typer.echo(f"Account: {data['account']}  (ledger: {data['ledger']})")
    if cd:
        typer.echo(
            f"COOLDOWN ACTIVE: private writes paused until {cd['until']} ({cd['remaining']} left)"
            f" after {cd['kind']} on {cd['source'] or 'a private call'}."
        )
        if cd.get("detail"):
            typer.echo(f"  detail: {cd['detail']}")
    else:
        typer.echo("Cooldown: none")
    if data["ignore_cooldown_override"]:
        typer.echo("WARNING: CLINSTAGRAM_IGNORE_COOLDOWN=1 is set; cooldowns are being ignored.")
    typer.echo("Caps (private-API writes; Graph API is exempt):")
    for row in data["caps"]:
        limit = "no cap" if row["limit"] is None else f"{row['used']}/{row['limit']}"
        extra = f"  next slot {row['next_slot_at']}" if row.get("next_slot_at") else ""
        typer.echo(f"  {row['action']:<13} {limit:>8} per {row['window']:<4} [{row['config_key']}]{extra}")
    rn = data["username_change"]
    if rn["min_days_between"]:
        typer.echo(
            f"Username: one change per {rn['min_days_between']} days; last "
            f"{rn['last_change'] or 'never'}; next allowed {rn['next_allowed']}"
        )
