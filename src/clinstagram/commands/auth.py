from __future__ import annotations

import contextlib
import json
import sys
from typing import Optional

import typer

from clinstagram.auth.keychain import SecretsStore
from clinstagram.backends.capabilities import CAPABILITY_MATRIX
from clinstagram.commands._dispatch import make_subgroup, output_error, output_success
from clinstagram.models import CLIError, ExitCode

from clinstagram._console import console
auth_app = make_subgroup("Manage authentication (Graph & Private)")


def _get_secrets(ctx: typer.Context) -> SecretsStore:
    if "secrets" in ctx.obj:
        return ctx.obj["secrets"]
    return SecretsStore(backend="keyring")


@auth_app.command("status")
def status(ctx: typer.Context):
    """Show authentication status for the current account."""
    account = ctx.obj["account"]
    config = ctx.obj["config"]
    secrets = _get_secrets(ctx)

    backends = {}
    for name in ["graph_ig", "graph_fb", "private"]:
        backends[name] = {"configured": secrets.has_backend(account, name)}

    result = {
        "account": account,
        "compliance_mode": config.compliance_mode.value,
        "backends": backends,
    }

    if not ctx.obj["json"]:
        from rich.table import Table

        table = Table(title=f"Auth Status: {account}")
        table.add_column("Backend", style="cyan")
        table.add_column("Status", style="green")
        for name, info in backends.items():
            table.add_row(name, "Configured" if info["configured"] else "Not configured")
        console.print(table)
        console.print(f"Compliance mode: [bold]{config.compliance_mode.value}[/bold]")
        return
    output_success(ctx, result)


def _store_graph_token(
    ctx: typer.Context,
    backend_name: str,
    token: Optional[str],
    label: str,
) -> None:
    account = ctx.obj["account"]
    secrets = _get_secrets(ctx)
    token_key = f"{backend_name}_token"
    effective_token = token
    if not effective_token:
        if ctx.obj["json"]:
            output_error(
                ctx,
                CLIError(
                    exit_code=ExitCode.USER_ERROR,
                    error=f"{label} access token is required",
                    remediation=f"Run: clinstagram auth connect-{backend_name.split('_', 1)[1]} --token <token>",
                ),
            )
        effective_token = typer.prompt(f"{label} access token", hide_input=True)

    secrets.set(account, token_key, effective_token)

    if not ctx.obj["json"]:
        console.print(f"[green]Stored[/green] {label} token for [bold]{account}[/bold]")
        return
    output_success(
        ctx,
        {
            "account": account,
            "connected": True,
            "import_method": "manual_token",
        },
        backend_used=backend_name,
    )


@auth_app.command("connect-ig")
def connect_ig(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(None, "--token", help="Instagram Login access token to store"),
):
    """Store an Instagram Login access token for the Graph API."""
    _store_graph_token(ctx, "graph_ig", token, "Instagram Login")


@auth_app.command("connect-fb")
def connect_fb(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(None, "--token", help="Facebook Login access token to store"),
):
    """Store a Facebook Login access token for the Graph API."""
    _store_graph_token(ctx, "graph_fb", token, "Facebook Login")


@auth_app.command("login")
def login(
    ctx: typer.Context,
    username: str = typer.Option(..., "--username", "-u", prompt=True, help="Instagram username, email, or phone number"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Instagram password (prompted if needed)"),
    totp_seed: str = typer.Option("", "--totp-seed", help="TOTP seed for 2FA (base32)"),
    proxy: str = typer.Option("", "--proxy", help="Proxy URL (recommended for private API)"),
    locale: str = typer.Option("", "--locale", help="Locale (e.g. en_GB, pt_BR). Auto-detected if omitted"),
    timezone: str = typer.Option("", "--timezone", help="Timezone offset in seconds (e.g. 0). Auto-detected if omitted"),
    delay_min: int = typer.Option(1, "--delay-min", help="Min delay between actions (seconds)"),
    delay_max: int = typer.Option(3, "--delay-max", help="Max delay between actions (seconds)"),
):
    """Login via Private API (instagrapi). Accepts username, email, or phone."""
    from clinstagram.auth.private_login import LoginConfig, login_private

    account = ctx.obj["account"]
    secrets = _get_secrets(ctx)

    # Check for existing session
    existing_session = secrets.get(account, "private_session") or ""
    
    # Prompt for password if not provided AND no session exists
    effective_password = password
    if not effective_password and not existing_session:
        effective_password = typer.prompt("Instagram password", hide_input=True)

    # Warn about missing proxy
    effective_proxy = proxy or ctx.obj.get("proxy", "")
    if not effective_proxy and not ctx.obj["json"]:
        console.print("[yellow]Warning:[/yellow] No proxy set. Instagram may flag your IP.")
        console.print("  Use --proxy or set proxy in config.toml for safety.")

    config = LoginConfig(
        username=username,
        password=effective_password or "",
        totp_seed=totp_seed,
        proxy=effective_proxy,
        locale=locale,
        timezone=timezone,
        delay_range=[delay_min, delay_max],
    )

    with contextlib.redirect_stdout(sys.stderr):  # keep instagrapi prints out of JSON stdout
        result = login_private(config, existing_session=existing_session)

    # If session expired and no password was provided, prompt and retry
    if not result.success and "no password provided" in (result.error or "").lower() and not effective_password:
        if ctx.obj["json"]:
            # In JSON mode, can't prompt interactively — just report the error
            pass
        else:
            console.print("[yellow]Session expired.[/yellow] Password required to re-authenticate.")
            effective_password = typer.prompt("Instagram password", hide_input=True)
            config = LoginConfig(
                username=username,
                password=effective_password,
                totp_seed=totp_seed,
                proxy=effective_proxy,
                locale=locale,
                timezone=timezone,
                delay_range=[delay_min, delay_max],
            )
            with contextlib.redirect_stdout(sys.stderr):  # keep instagrapi prints out of JSON stdout
                result = login_private(config, existing_session=existing_session)

    if result.success:
        # Store session in keychain
        secrets.set(account, "private_session", result.session_json)

        if not ctx.obj["json"]:
            label = "Re-authenticated" if result.relogin else "Logged in"
            console.print(f"[green]{label}[/green] as [bold]{result.username}[/bold] (private API)")
            return
        output_success(
            ctx,
            {
                "username": result.username,
                "relogin": result.relogin,
            },
            backend_used="private",
        )
    else:
        if not ctx.obj["json"]:
            console.print(f"[red]Login failed:[/red] {result.error}")
            if result.remediation:
                console.print(f"[yellow]Fix:[/yellow] {result.remediation}")
            if result.challenge_required:
                console.print("[yellow]Tip:[/yellow] Run login again — Instagram will send a verification code.")
            raise typer.Exit(
                code=ExitCode.CHALLENGE_REQUIRED if result.challenge_required else ExitCode.AUTH_ERROR
            )
        output_error(
            ctx,
            CLIError(
                exit_code=ExitCode.CHALLENGE_REQUIRED if result.challenge_required else ExitCode.AUTH_ERROR,
                error=result.error,
                remediation=result.remediation or None,
                challenge_type="instagram" if result.challenge_required else None,
            ),
        )


def _probe_backend(ctx: typer.Context, backend_name: str) -> dict:
    account = ctx.obj["account"]
    secrets = _get_secrets(ctx)
    configured = secrets.has_backend(account, backend_name)
    result = {
        "configured": configured,
        "reachable": False,
        "features": sorted(f.value for f in CAPABILITY_MATRIX.get(backend_name, set())),
    }
    if not configured:
        return result

    try:
        if backend_name.startswith("graph_"):
            import httpx

            from clinstagram.backends.graph import GraphBackend

            login_type = backend_name.split("_", 1)[1]
            token = secrets.get(account, f"{backend_name}_token")
            client = httpx.Client(timeout=10.0)
            try:
                backend = GraphBackend(token=token or "", login_type=login_type, client=client)
                result["account_id"] = backend._me_id()
            finally:
                client.close()
        else:
            from instagrapi import Client

            from clinstagram.auth.private_login import _validate_session

            session_json = secrets.get(account, "private_session")
            if not session_json:
                return result
            cl = Client()
            cl.set_settings(json.loads(session_json))
            proxy = ctx.obj.get("proxy")
            if proxy:
                cl.set_proxy(proxy)
            result["reachable"] = _validate_session(cl)
            if result["reachable"]:
                info = cl.account_info()
                result["username"] = getattr(info, "username", None)
            return result
        result["reachable"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


@auth_app.command("probe")
def probe(ctx: typer.Context):
    """Validate configured backends with a lightweight live check."""
    account = ctx.obj["account"]
    result = {"account": account, "backends": {}}
    for name in ["graph_ig", "graph_fb", "private"]:
        result["backends"][name] = _probe_backend(ctx, name)
    if not ctx.obj["json"]:
        for name, info in result["backends"].items():
            if not info["configured"]:
                s = "Not configured"
            elif info["reachable"]:
                s = "Reachable"
            else:
                s = "Configured but failing"
            typer.echo(f"  {name}: {s}")
        return
    output_success(ctx, result)


@auth_app.command("logout")
def logout(
    ctx: typer.Context,
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear stored sessions for the current account."""
    if not confirm:
        typer.confirm("Clear all stored sessions?", abort=True)
    account = ctx.obj["account"]
    secrets = _get_secrets(ctx)
    for name in ["graph_ig_token", "graph_fb_token", "private_session"]:
        secrets.delete(account, name)
    if not ctx.obj["json"]:
        typer.echo(f"Cleared sessions for account: {account}")
        return
    output_success(ctx, {"account": account, "cleared": True})
