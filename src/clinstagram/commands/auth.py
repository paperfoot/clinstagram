from __future__ import annotations

import contextlib
import json
import re
import shlex
import sys
from typing import TYPE_CHECKING, Any, Optional

import typer

from clinstagram.auth.keychain import SecretsStore
from clinstagram.backends.capabilities import CAPABILITY_MATRIX
from clinstagram.commands._dispatch import (
    _classify_error,
    configured_backends,
    forget_account,
    make_subgroup,
    output_error,
    output_success,
    record_account,
)
from clinstagram.models import CLIError, ExitCode

from clinstagram._console import console

if TYPE_CHECKING:
    import httpx

    from clinstagram.backends.graph import GraphAPIError

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
    configured = configured_backends(ctx)
    for name in ["graph_ig", "graph_fb", "private"]:
        backends[name] = {"configured": name in configured}

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


def _graph_client() -> httpx.Client:
    """HTTP client for live Graph checks (tests swap in an httpx.MockTransport)."""
    import httpx

    return httpx.Client(timeout=15)


# Meta's answers when an account's authorization of the app is broken. Seen on
# 3 Nov 2026 after Instagram's Boost flow linked @agingresearch.news to Facebook:
# the profile still read, while the media list failed and media_count read 0.
_BROKEN_APP_AUTH = re.compile(
    r"API access blocked|Application does not have permission|Cannot call API for app", re.I,
)
_NEW_TOKEN_STEPS = (
    "Meta app dashboard → Instagram API → API setup with Unified login → "
    "2. Generate access tokens → Generate token on the account's row"
)


def _redact(text: str, token: str) -> str:
    """Meta and httpx errors shouldn't carry the token; make sure none ever does.

    Real tokens run to hundreds of characters; replacing a one-letter "token"
    would only garble the message.
    """
    return text.replace(token, "<token>") if len(token) >= 8 else text


def _reconnect_hint(backend_name: str, account: str, ig_user_id: Optional[str] = None) -> str:
    """The command that stores a token for this account without putting it on the command line."""
    suffix = backend_name.split("_", 1)[1]
    cmd = f"pbpaste | clinstagram --account {shlex.quote(account)} auth connect-{suffix} --token-stdin"
    if suffix == "fb":
        cmd += f" --ig-user-id {ig_user_id or '<id>'}"
    return cmd


def _verdict(exc: GraphAPIError, ig_unknown: bool) -> str:
    """What a Graph refusal means for a token: broken | no_ig | transient | token | refused."""
    if _BROKEN_APP_AUTH.search(str(exc)):
        return "broken"
    if ig_unknown and (exc.code == 100 or exc.error_type == "NoInstagramAccount"):
        return "no_ig"
    kind = _classify_error(exc)
    if kind == "rate" or exc.status_code >= 500 or exc.code in (1, 2):
        return "transient"  # says nothing about the token
    if kind == "auth":
        return "token"
    return "refused"


def _remediation(verdict: str, backend_name: str, account: str, ig_user_id: Optional[str]) -> str:
    if verdict == "no_ig":
        return ("This token isn't linked to an Instagram account. Store it with the ID of the "
                f"Instagram account it acts on: {_reconnect_hint(backend_name, account)}")
    reason = {
        "broken": ("This account's authorization of the Meta app is broken (seen after Instagram's "
                   "Boost flow linked the account to Facebook); a new token fixes it."),
        "token": "Meta rejected the token as invalid or expired.",
    }.get(verdict, "Meta refused the check: the token may belong to another account or lack a permission.")
    return (f"{reason} Make a new token ({_NEW_TOKEN_STEPS}), then store it: "
            f"{_reconnect_hint(backend_name, account, ig_user_id)}")


def _verify_graph_token(ctx: typer.Context, backend_name: str, token: str, account: str) -> dict[str, Any]:
    """Check a Graph token live, on the account the commands will act on. Never raises.

    `verified` is True when the token reads its identity, the Instagram account and
    that account's media list; False when Meta refuses (with a `remediation`); None
    when Meta couldn't be asked (network, rate limit, server error), with a `note`.
    """
    from clinstagram.backends.graph import GraphAPIError, GraphBackend

    login_type = backend_name.split("_", 1)[1]
    entry = ctx.obj["config"].accounts.get(account)
    # Resolve the account the way _dispatch._instantiate_backend does.
    ig_id = (getattr(entry, "ig_user_id", None) or None) if login_type == "fb" else None
    ig_unknown = login_type == "fb" and not ig_id
    probe_cmd = f"clinstagram --account {shlex.quote(account)} auth probe"
    out: dict[str, Any] = {"verified": None}
    step = "identity"
    try:
        with _graph_client() as client:
            gb = GraphBackend(token=token, login_type=login_type, client=client, ig_user_id=ig_id)
            me = gb._get("me", {"fields": "id,name"}) or {}
            me_id = str(me["id"]) if me.get("id") is not None else None
            out["identity"] = {"id": me_id, "name": me.get("name")}
            step = "account"
            if not ig_id:
                # Instagram Login: /me is the Instagram account itself.
                ig_id = me_id if login_type == "ig" and me_id else str(gb._me_id())
            out["ig_user_id"] = ig_id
            step = "profile"
            profile = gb._get(ig_id, {"fields": "username,media_count"}) or {}
            out["username"] = profile.get("username")
            out["media_count"] = profile.get("media_count")
            # The profile can read fine while Meta refuses the media list, so only
            # this read tells a working token from a broken one.
            step = "media"
            gb._get(f"{ig_id}/media", {"fields": "id", "limit": "1"})
            out["media_access"] = True
            out["verified"] = True
    except GraphAPIError as exc:
        # Past /me, a missing-object error on an unknown account means there's no linked one.
        verdict = _verdict(exc, ig_unknown and step != "identity")
        out["verify_error"] = _redact(str(exc), token)
        if verdict == "transient":
            out["note"] = ("Meta couldn't answer the check just now (rate limit or server error). "
                           f"The token is stored; check it later: {probe_cmd}")
        else:
            out["verified"] = False
            if step == "media":
                out["media_access"] = False
            out["remediation"] = _remediation(verdict, backend_name, account, ig_id)
    except Exception as exc:  # network failure, timeout, an answer that isn't JSON
        out["verify_error"] = _redact(f"{type(exc).__name__}: {exc}", token)
        out["note"] = ("Couldn't check the token with Meta (network error or unexpected answer). "
                       f"The token is stored; check it later: {probe_cmd}")
    return out


def _print_check(check: dict[str, Any], username: Optional[str]) -> None:
    from rich.markup import escape

    if check["verified"]:
        who = f"@{username}" if username else check.get("ig_user_id")
        posts = check.get("media_count")
        console.print(f"[green]Verified:[/green] reads {escape(str(who))} and its media"
                      + (f" ({posts} posts)" if posts is not None else ""))
    elif check["verified"] is False:
        console.print(f"[yellow]Warning:[/yellow] Meta refused the token: {escape(check['verify_error'])}")
        console.print(f"[yellow]Fix:[/yellow] {escape(check['remediation'])}")
    else:
        console.print(f"[yellow]Not verified:[/yellow] {escape(check['note'])}")


def _read_token_stdin(ctx: typer.Context, hint: str) -> str:
    """The token piped on stdin, stripped of surrounding whitespace."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        # A terminal would wait for Ctrl-D and show the token as it's typed.
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="--token-stdin reads the token from a pipe, and nothing is piped in",
            remediation=f"Pipe it in: {hint}",
        ))
    token = stream.read().strip()
    if not token:
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="No token on stdin",
            remediation=f"Copy the token, then run: {hint}",
        ))
    if any(ch.isspace() for ch in token):
        # Never a token (e.g. "Bearer EAA..." or a multi-line clipboard); storing it
        # would replace a working token with a broken one.
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="What's on stdin isn't a single token: it has spaces or line breaks inside",
            remediation=f"Copy only the token, then run: {hint}",
        ))
    return token


def _resolve_token(
    ctx: typer.Context, token: Optional[str], token_stdin: bool, label: str, hint: str,
) -> str:
    """The token from --token, --token-stdin or a hidden prompt; exits 1 if there is none."""
    if token_stdin and token is not None:
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error="Pass the token with --token or --token-stdin, not both",
            remediation=f"Use stdin, which keeps the token out of ps and shell history: {hint}",
        ))
    if token_stdin:
        return _read_token_stdin(ctx, hint)
    if token:
        return token
    if ctx.obj["json"]:
        output_error(ctx, CLIError(
            exit_code=ExitCode.USER_ERROR,
            error=f"{label} access token is required",
            remediation=f"Pipe it in: {hint}",
        ))
    return typer.prompt(f"{label} access token", hide_input=True)


def _store_graph_token(ctx: typer.Context, backend_name: str, token: str, label: str) -> None:
    account = ctx.obj["account"]
    secrets = _get_secrets(ctx)
    secrets.set(account, f"{backend_name}_token", token)
    # Stored before the check: a failed or impossible check is reported, and the
    # token is kept either way (exit 0).
    check = _verify_graph_token(ctx, backend_name, token, account)
    username = check.pop("username", None)
    record_account(ctx, account, username=username, add_backend=backend_name)

    if not ctx.obj["json"]:
        console.print(f"[green]Stored[/green] {label} token for [bold]{account}[/bold]")
        _print_check(check, username)
        return
    output_success(
        ctx,
        {
            "account": account,
            "username": username,
            "connected": True,
            "import_method": "manual_token",
            **check,
        },
        backend_used=backend_name,
    )


_TOKEN_STDIN_HELP = (
    "Read the token from stdin, which keeps it out of ps and shell history, e.g. "
    "pbpaste | clinstagram auth {cmd} --token-stdin"
)


@auth_app.command("connect-ig")
def connect_ig(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(
        None, "--token",
        help="Instagram Login access token to store (visible in ps and shell history; prefer --token-stdin)",
    ),
    token_stdin: bool = typer.Option(False, "--token-stdin", help=_TOKEN_STDIN_HELP.format(cmd="connect-ig")),
):
    """Store an Instagram Login access token for the Graph API."""
    hint = _reconnect_hint("graph_ig", ctx.obj["account"])
    value = _resolve_token(ctx, token, token_stdin, "Instagram Login", hint)
    _store_graph_token(ctx, "graph_ig", value, "Instagram Login")


@auth_app.command("connect-fb")
def connect_fb(
    ctx: typer.Context,
    token: Optional[str] = typer.Option(
        None, "--token",
        help="Facebook Login access token to store (visible in ps and shell history; prefer --token-stdin)",
    ),
    token_stdin: bool = typer.Option(
        False, "--token-stdin", help=_TOKEN_STDIN_HELP.format(cmd="connect-fb") + " --ig-user-id <id>",
    ),
    ig_user_id: Optional[str] = typer.Option(
        None, "--ig-user-id",
        help="Instagram user ID the token acts on. Needed for system-user tokens from Meta's unified login, whose /me has no Instagram account",
    ),
):
    """Store a Facebook Login access token for the Graph API."""
    config = ctx.obj["config"]
    account = ctx.obj["account"]
    known_ig = (ig_user_id or "").strip() or getattr(config.accounts.get(account), "ig_user_id", None)
    # Get the token before saving anything, so a refused call leaves --ig-user-id unapplied.
    value = _resolve_token(ctx, token, token_stdin, "Facebook Login", _reconnect_hint("graph_fb", account, known_ig))
    if ig_user_id:
        from clinstagram.config import AccountEntry, save_config
        entry = config.accounts.get(account) or AccountEntry()
        entry.ig_user_id = ig_user_id.strip()
        config.accounts[account] = entry
        save_config(config, ctx.obj.get("config_dir"))
    _store_graph_token(ctx, "graph_fb", value, "Facebook Login")


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
        record_account(ctx, account, username=result.username, add_backend="private")

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
    forget_account(ctx, account)
    if not ctx.obj["json"]:
        typer.echo(f"Cleared sessions for account: {account}")
        return
    output_success(ctx, {"account": account, "cleared": True})
