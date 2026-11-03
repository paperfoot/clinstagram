"""Manage several Instagram accounts: list, switch default, remove, per-account proxy."""
from __future__ import annotations

import re
import sys
from typing import Optional

import typer

from clinstagram.auth.keychain import BACKEND_TOKEN_MAP
from clinstagram.commands import _dispatch
from clinstagram.commands._dispatch import (
    forget_account,
    make_subgroup,
    output_error,
    output_success,
    probe_backends,
    record_account,
)
from clinstagram.models import CLIError, ExitCode

account_app = make_subgroup("Manage multiple accounts (list, switch, remove, per-account proxy)")

_PROXY_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")


def redact_proxy(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    return re.sub(r"://[^@/]+@", "://***:***@", value)


def _known_accounts(ctx: typer.Context, check: bool) -> dict[str, list[str]]:
    """name -> configured backends. Registry first; seeds unregistered default accounts."""
    config = ctx.obj["config"]
    names = list(config.accounts)
    for extra in (config.default_account, "default"):
        if extra not in names:
            names.append(extra)
    result: dict[str, list[str]] = {}
    for name in names:
        entry = config.accounts.get(name)
        if entry is not None and entry.backends and not check:
            result[name] = list(entry.backends)
            continue
        found = probe_backends(ctx, name)
        if found:
            record_account(ctx, name, backends=found)
            result[name] = found
        elif entry is not None:
            if check and entry.backends:  # registry said yes, keychain says no
                record_account(ctx, name, backends=[])
            result[name] = []
    return result


@account_app.command("list")
def list_accounts(
    ctx: typer.Context,
    check: bool = typer.Option(
        False, "--check", help="Re-read the keychain instead of trusting the account registry"
    ),
):
    """List stored accounts: name, username, backends, proxy, which is default."""
    config = ctx.obj["config"]
    rows = []
    for name, backends in _known_accounts(ctx, check).items():
        entry = config.accounts.get(name)
        rows.append({
            "name": name,
            "username": entry.username if entry else None,
            "ig_user_id": getattr(entry, "ig_user_id", None) if entry else None,
            "backends": backends,
            "uses": _uses(backends),
            "proxy": redact_proxy(entry.proxy) if entry else None,
            "is_default": name == config.default_account,
        })
    if not ctx.obj["json"]:
        if not rows:
            typer.echo("No accounts. Log in with: clinstagram --account NAME auth login -u USERNAME")
            return
        for r in rows:
            mark = "*" if r["is_default"] else " "
            user = f"@{r['username']}" if r["username"] else "-"
            proxy = f"  proxy={r['proxy']}" if r["proxy"] else ""
            typer.echo(f"{mark} {r['name']:<16} {user:<24} {','.join(r['backends']) or 'no credentials'}{proxy}")
            typer.echo(f"  {'':<16} {r['uses']}")
        return
    output_success(ctx, rows)


def _uses(backends: list[str]) -> str:
    """One line an agent can act on: which API this account goes through, and what it can't do."""
    official = any(b.startswith("graph_") for b in backends)
    private = "private" in backends
    if official and private:
        return ("official API first (posts, reels, carousels, comments, DMs); unofficial API for what it can't "
                "do: story link stickers, activity, likes, follows, editing or deleting posts, profile edits")
    if official:
        return ("official API only: posts, reels, carousels, stories without link stickers, comments, DMs. "
                "Insights, hashtags and other accounts' profiles need extra token permissions. Can't: activity, "
                "likes, follows, edit or delete posts, profile edits")
    if private:
        return "unofficial API only: everything, at higher risk of restrictions; pace writes"
    return "no credentials"


def _require_known(ctx: typer.Context, name: str) -> bool:
    config = ctx.obj["config"]
    if name in config.accounts or probe_backends(ctx, name):
        return True
    known = ", ".join(sorted(config.accounts)) or "none"
    output_error(ctx, CLIError(
        exit_code=ExitCode.USER_ERROR,
        error=f"Unknown account '{name}' (known: {known})",
        remediation=f"Log in first: clinstagram --account {name} auth login -u USERNAME",
    ))
    return False


@account_app.command("use")
def use(ctx: typer.Context, name: str = typer.Argument(..., help="Account name to make the default")):
    """Make NAME the default account for commands without --account."""
    from clinstagram.config import save_config

    if not _require_known(ctx, name):
        return
    config = ctx.obj["config"]
    config.default_account = name
    save_config(config, ctx.obj.get("config_dir"))
    if not ctx.obj["json"]:
        typer.echo(f"Default account: {name}")
        return
    output_success(ctx, {"default_account": name})


@account_app.command("remove")
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm (required with --json or without a TTY)"),
):
    """Delete an account's stored credentials and registry entry."""
    from clinstagram.config import save_config

    if not _require_known(ctx, name):
        return
    if not yes:
        if ctx.obj["json"] or not sys.stdin.isatty():
            output_error(ctx, CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Removing account '{name}' deletes its stored sessions and tokens",
                remediation=f"Re-run with --yes: clinstagram account remove {name} --yes",
            ))
            return
        typer.confirm(f"Delete stored credentials for '{name}'?", abort=True)
    secrets = _dispatch._get_secrets(ctx)
    for token_key in BACKEND_TOKEN_MAP.values():
        secrets.delete(name, token_key)
    forget_account(ctx, name)
    config = ctx.obj["config"]
    new_default = config.default_account
    if config.default_account == name:
        new_default = next(iter(config.accounts), "default")
        config.default_account = new_default
        save_config(config, ctx.obj.get("config_dir"))
    if not ctx.obj["json"]:
        typer.echo(f"Removed account {name}. Default account: {new_default}")
        return
    output_success(ctx, {"removed": name, "default_account": new_default})


@account_app.command("set-proxy")
def set_proxy(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name"),
    url: str = typer.Argument(..., help="Proxy URL (http/https/socks5://user:pass@host:port) or 'none'"),
):
    """Give an account its own proxy (one residential IP per account avoids linking them)."""
    from clinstagram.config import AccountEntry, save_config

    config = ctx.obj["config"]
    value: Optional[str] = None if url.strip().lower() in ("none", "") else url.strip()
    if value is not None:
        scheme = value.split("://", 1)[0].lower() if "://" in value else ""
        if scheme not in _PROXY_SCHEMES:
            output_error(ctx, CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Proxy must start with one of: {', '.join(s + '://' for s in _PROXY_SCHEMES)}",
            ))
            return
    entry = config.accounts.get(name) or AccountEntry()
    entry.proxy = value
    config.accounts[name] = entry
    save_config(config, ctx.obj.get("config_dir"))
    if not ctx.obj["json"]:
        typer.echo(f"Proxy for {name}: {redact_proxy(value) or 'none'}")
        return
    output_success(ctx, {"account": name, "proxy": redact_proxy(value)})
