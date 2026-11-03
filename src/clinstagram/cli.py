from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from clinstagram import __version__
from clinstagram.config import BackendType, load_config

app = typer.Typer(
    name="clinstagram",
    help="Hybrid Instagram CLI for OpenClaw — Graph API + Private API",
    epilog="Global options (--json, --proxy, --account) must appear before the subcommand. Example: clinstagram --json auth status",
    no_args_is_help=True,
)


def _version_callback(value: bool):
    if value:
        typer.echo(f"clinstagram {__version__}")
        raise typer.Exit()


def _resolve_config_dir() -> Optional[Path]:
    env = os.environ.get("CLINSTAGRAM_CONFIG_DIR")
    return Path(env) if env else None


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (place before subcommand)"),
    account: Optional[str] = typer.Option(
        None, "--account", envvar="CLINSTAGRAM_ACCOUNT",
        help="Account name (default: config default_account; env CLINSTAGRAM_ACCOUNT)",
    ),
    backend: BackendType = typer.Option(BackendType.AUTO, "--backend", help="Force backend"),
    proxy: Optional[str] = typer.Option(None, "--proxy", help="Proxy URL for private API"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
    enable_growth: bool = typer.Option(
        False, "--enable-growth-actions", help="Unlock follow/unfollow, like/unlike, and commenting"
    ),
    full: bool = typer.Option(
        False, "--full", help="Include CDN URLs and null fields (default output is compact)"
    ),
    fields: Optional[str] = typer.Option(
        None, "--fields", help="Comma-separated fields to return per item, dotted paths ok (e.g. id,code,user.username)"
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True
    ),
):
    config_dir = _resolve_config_dir()
    if not json_output and not sys.stdout.isatty():
        json_output = True
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["backend"] = backend
    config = load_config(config_dir)
    account = account or config.default_account
    ctx.obj["account"] = account
    entry = config.accounts.get(account)
    # --proxy beats the account's own proxy, which beats the global one.
    ctx.obj["proxy"] = proxy or (entry.proxy if entry else None) or config.proxy
    ctx.obj["dry_run"] = dry_run
    ctx.obj["full"] = full
    ctx.obj["fields"] = fields
    ctx.obj["enable_growth"] = enable_growth
    ctx.obj["config_dir"] = config_dir
    ctx.obj["config"] = config
    # Use memory-backed secrets only in explicit test mode
    if os.environ.get("CLINSTAGRAM_TEST_MODE") == "1":
        from clinstagram.auth.keychain import SecretsStore

        ctx.obj["secrets"] = SecretsStore(backend="memory")


@app.command("agent-info")
@app.command("info", hidden=True)
def agent_info():
    """Output machine-readable manifest of CLI capabilities."""
    from clinstagram.models import ExitCode

    # Static manifest parts
    manifest: dict[str, Any] = {
        "name": "clinstagram",
        "version": __version__,
        "description": "Hybrid Instagram CLI for agents (Graph API + instagrapi)",
        "homepage": "https://github.com/paperfoot/clinstagram",
        "commands": {},
        "global_flags": {
            "--json": {"description": "Output as JSON (place before subcommand)", "type": "boolean"},
            "--account": {
                "description": "Account to act as (default: config default_account; env CLINSTAGRAM_ACCOUNT). See `account list`.",
                "type": "string",
            },
            "--backend": {
                "description": "Force backend",
                "type": "string",
                "values": ["auto", "graph_ig", "graph_fb", "private"],
                "default": "auto",
            },
            "--proxy": {"description": "Proxy URL for private API", "type": "string"},
            "--dry-run": {"description": "Show what would happen", "type": "boolean"},
            "--full": {
                "description": "Include CDN URL fields (profile_picture_url, thumbnail_url, video_url, ...) and null values. Default output omits both; a missing key means null.",
                "type": "boolean",
            },
            "--fields": {
                "description": "Comma-separated fields to keep per item, dotted paths allowed (e.g. id,code,caption,user.username). Exact projection at full fidelity; unknown fields exit 1 listing the available ones.",
                "type": "string",
            },
            "--enable-growth-actions": {
                "description": "Unlock follow/unfollow, like/unlike, and commenting",
                "type": "boolean",
            },
        },
        "exit_codes": {
            str(code.value): description
            for code, description in zip(
                ExitCode,
                [
                    "SUCCESS",
                    "USER_ERROR \u2014 invalid arguments or user error",
                    "AUTH_ERROR \u2014 authentication failed or session expired",
                    "RATE_LIMITED \u2014 wait and retry",
                    "API_ERROR \u2014 upstream API failed (transient)",
                    "CHALLENGE_REQUIRED \u2014 2FA or checkpoint needed",
                    "POLICY_BLOCKED \u2014 action blocked by compliance mode / growth gate",
                    "CAPABILITY_UNAVAILABLE \u2014 selected backend cannot do this",
                ],
            )
        },

        "envelope": {
            "success": {
                "shape": "{ exit_code: int, data: any, backend_used: string | null }",
                "notes": "`backend_used` is always present (may be null for commands that don't touch a backend).",
            },
            "error": {
                "shape": "{ exit_code: int, error: string, remediation?: string, retry_after?: int, challenge_type?: string, challenge_url?: string }",
                "notes": "Unset optional fields are omitted (not emitted as null). `retry_after` present on RATE_LIMITED. `challenge_*` present on CHALLENGE_REQUIRED.",
            },
            "status_field": None,
            "version_field": None,
            "standard": "non-ACF",
        },
        "compliance_modes": {
            "official-only": "Only graph_ig / graph_fb. Private backend blocked.",
            "hybrid-safe": "Graph API preferred; private only for read-only features. No growth actions.",
            "private-enabled": "Private backend allowed for all features including growth actions.",
        },
        "config": {
            "path": "~/.clinstagram/config.toml",
            "env_prefix": "CLINSTAGRAM_",
            "env_vars": {
                "CLINSTAGRAM_CONFIG_DIR": "Override config dir (default ~/.clinstagram)",
                "CLINSTAGRAM_TEST_MODE": "Set to `1` to use an in-memory secrets store (for tests).",
            },
            "account_dir": "~/.clinstagram/accounts/",
        },
        "auto_json_when_piped": True,
        "notes_for_agents": {
            "accounts_first": "Run `clinstagram --json account list` before acting: each row gives the account name to pass as `--account`, its @username, its backends and a `uses` line saying which API it goes through and what it can't do. Never guess an account name.",
            "official_vs_private": "Accounts with a graph_* backend publish, comment and DM through Meta's official API (safe). The private API (instagrapi) is the only way to do story link stickers, activity, likes, follows, post edits and profile edits, and it can get an account restricted: pace it, never burst identity edits, stop after any 429 or feedback_required.",
            "graph_media_refs": "On graph backends, media refs accept a Graph media ID, 'latest', or a shortcode/URL of the account's own recent posts. Graph media IDs differ from private-API media pks.",
            "exit_7_means_stop": "Exit 7 (CAPABILITY_UNAVAILABLE) is never transient: the account lacks a backend or the token lacks a Meta permission. Read `remediation`; don't retry.",
            "json_flag_position": "`--json` must come BEFORE the subcommand (e.g. `clinstagram --json user posts @someuser`). Not strictly required if stdout is piped; clinstagram auto-enables JSON when not a TTY.",
            "growth_gate": "Follow/unfollow/like/comment require --enable-growth-actions and compliance_mode != hybrid-safe.",
            "backend_auto_select": "Omit --backend to let the router pick. Use --backend private only when needed.",
            "discover_posts": "Use `clinstagram --json user posts @username --limit N` to list recent media with captions, video_url, product_type.",
            "download_media": "Use `clinstagram media download <id|shortcode|url>` to save a post/reel/carousel locally.",
            "doctor": "`clinstagram --json doctor` reports structured health checks; `--deep` runs live probes (costs quota).",
            "update": "`clinstagram --json update` is check-only by default. To self-upgrade: `clinstagram update --apply --yes` (requires plain pip install).",
        },
    }

    # Derive backend capability lists from the single source of truth so the
    # manifest never drifts from the router's decisions.
    from clinstagram.backends.capabilities import CAPABILITY_MATRIX

    manifest["backends"] = {
        "graph_ig": {
            "description": "Meta Graph API via Instagram login. Official, most compliance-safe. Business/Creator only.",
            "capabilities": sorted(f.value for f in CAPABILITY_MATRIX["graph_ig"]),
        },
        "graph_fb": {
            "description": "Meta Graph API via Facebook login. Official, supports DMs and stories.",
            "capabilities": sorted(f.value for f in CAPABILITY_MATRIX["graph_fb"]),
        },
        "private": {
            "description": "instagrapi (reverse-engineered). Full feature set. Detection risk — use behind a residential proxy if possible.",
            "capabilities": sorted(f.value for f in CAPABILITY_MATRIX["private"]),
        },
    }

    # Introspect commands — walk the Typer app and extract click-style params.
    # Duck-typed on purpose: Typer >= 0.25 vendors click as typer._click and no
    # longer installs `click`, so isinstance checks against click classes break.

    def _is_choice(t: Any) -> bool:
        return isinstance(getattr(t, "choices", None), (list, tuple))

    def _param_kind(param: Any) -> str:
        return "positional" if getattr(param, "param_type_name", "") == "argument" else "option"

    def _param_type_name(param: Any) -> str:
        t = param.type
        if _is_choice(t):
            return "choice"
        name = getattr(t, "name", None) or type(t).__name__.lower()
        return {"int": "integer", "string": "string", "bool": "boolean", "float": "number"}.get(name, name)

    def _param_to_dict(param: Any) -> dict:
        d: dict[str, Any] = {
            "name": (param.opts[0] if param.opts else param.name),
            "kind": _param_kind(param),
            "type": _param_type_name(param),
            "required": bool(param.required),
        }
        if _is_choice(param.type):
            d["values"] = list(param.type.choices)
        default = param.default
        if default is not None and default is not False:
            try:
                json.dumps(default)
                d["default"] = default
            except TypeError:
                pass
        help_text = getattr(param, "help", None)
        if help_text:
            d["description"] = help_text
        return d

    # Two-pass walk: collect every (name, cmd_info) pair bucketed by callback
    # identity, then pick the first visible (non-hidden) entry as the primary
    # and demote the rest to aliases. This avoids the ordering bug where a
    # hidden alias registered first steals the primary slot.
    from dataclasses import dataclass

    @dataclass
    class _CmdRef:
        name: str
        cmd_info: Any
        hidden: bool

    buckets: dict[int, list[_CmdRef]] = {}

    def _collect(typer_app: typer.Typer, prefix: str = "") -> None:
        for cmd_info in typer_app.registered_commands:
            name = f"{prefix} {cmd_info.name or cmd_info.callback.__name__}".strip()
            buckets.setdefault(id(cmd_info.callback), []).append(
                _CmdRef(name=name, cmd_info=cmd_info, hidden=getattr(cmd_info, "hidden", False))
            )
        for group in typer_app.registered_groups:
            _collect(group.typer_instance, prefix=f"{prefix} {group.name}".strip())

    _collect(app)

    aliases: dict[str, list[str]] = {}

    for _, refs in buckets.items():
        visible = [r for r in refs if not r.hidden]
        primary = visible[0] if visible else refs[0]
        for other in refs:
            if other is primary:
                continue
            aliases.setdefault(primary.name, []).append(other.name)

        cmd_info = primary.cmd_info
        try:
            click_cmd = typer.main.get_command_from_info(
                cmd_info,
                pretty_exceptions_short=True,
                rich_markup_mode=None,
            )
        except TypeError:
            click_cmd = typer.main.get_command_from_info(cmd_info)

        args: list[dict] = []
        options: list[dict] = []
        for param in click_cmd.params:
            pd = _param_to_dict(param)
            (args if pd["kind"] == "positional" else options).append(pd)

        manifest["commands"][primary.name] = {
            "description": (cmd_info.help or click_cmd.help or "").strip(),
            "args": args,
            "options": options,
        }
    # Attach discovered aliases to each primary command entry
    for primary, alts in aliases.items():
        entry = manifest["commands"].get(primary)
        if entry is not None:
            entry["aliases"] = alts
    typer.echo(json.dumps(manifest, indent=2))


@app.command("doctor")
def doctor(
    ctx: typer.Context,
    deep: bool = typer.Option(
        False,
        "--deep",
        help=(
            "Run live probes (hits Instagram and pypi.org). Can cost rate-limit "
            "quota and, for the private backend, may look like a login check to IG. "
            "Off by default."
        ),
    ),
):
    """Diagnose environment and session health. Use the global `--account` to scope."""
    from clinstagram.commands.doctor_cmd import run_doctor

    run_doctor(ctx, deep=deep, account=None)


@app.command("update")
def update(
    ctx: typer.Context,
    check: bool = typer.Option(
        False,
        "--check",
        help="Check-only (redundant — this is the default). Kept for clarity.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Attempt to self-upgrade via pip. Only works for plain pip installs.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the TTY confirmation when used with --apply. Required in --json mode.",
    ),
    pre: bool = typer.Option(
        False,
        "--pre",
        help="Consider pre-release versions on PyPI (normally filtered out).",
    ),
):
    """Check for clinstagram updates. Default is check-only; use --apply --yes to upgrade."""
    from clinstagram.commands.update_cmd import run_update

    run_update(ctx, check=check, apply=apply, yes=yes, pre=pre)


@app.command("activity")
def activity(
    ctx: typer.Context,
    limit: int = typer.Option(50, "--limit", "-n", help="Max notifications to return"),
):
    """Your notifications: new followers, likes, comments, mentions, tags."""
    from clinstagram.backends.capabilities import Feature
    from clinstagram.commands._dispatch import dispatch

    dispatch(ctx, Feature.ACTIVITY, lambda b: b.activity(limit))


def _group_loaders() -> dict:
    """name -> import path of each command group (imported only when registered)."""
    return {
        "auth": ("clinstagram.commands.auth", "auth_app"),
        "account": ("clinstagram.commands.account_cmd", "account_app"),
        "config": ("clinstagram.commands.config_cmd", "config_app"),
        "profile": ("clinstagram.commands.profile", "profile_app"),
        "post": ("clinstagram.commands.post", "post_app"),
        "dm": ("clinstagram.commands.dm", "dm_app"),
        "story": ("clinstagram.commands.story", "story_app"),
        "comments": ("clinstagram.commands.comments", "comments_app"),
        "analytics": ("clinstagram.commands.analytics", "analytics_app"),
        "followers": ("clinstagram.commands.followers", "followers_app"),
        "user": ("clinstagram.commands.user", "user_app"),
        "like": ("clinstagram.commands.like", "like_app"),
        "hashtag": ("clinstagram.commands.hashtag", "hashtag_app"),
        "media": ("clinstagram.commands.media", "media_app"),
        "skill": ("clinstagram.commands.skill_install", "skill_app"),
    }


def _register_groups() -> None:
    """Register command groups. The console entry point (clinstagram.entry) may
    narrow this to the one group an invocation targets; importing this module
    directly (tests, `python -m`) registers everything."""
    import importlib

    import clinstagram

    only = getattr(clinstagram, "_only_group", None)
    for name, (module, attr) in _group_loaders().items():
        if only is not None and name != only:
            continue
        app.add_typer(getattr(importlib.import_module(module), attr), name=name)


_register_groups()


if __name__ == "__main__":
    app()
