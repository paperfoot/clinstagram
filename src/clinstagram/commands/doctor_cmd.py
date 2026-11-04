"""`clinstagram doctor` — offline-first environment/session diagnostic.

Design invariant: always emit a success-shaped envelope with `data.checks` and
`data.summary`, regardless of pass/warn/fail. The envelope's `exit_code` field
signals severity so agents never lose the structured report. Exit code map:

  - 0 : all pass (optionally with warns)
  - 1 : local failures only (missing deps, unwritable config dir) → USER_ERROR
  - 2 : at least one session/auth check failed → AUTH_ERROR
  - 4 : live probe hit an upstream error (--deep only) → API_ERROR
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from typing import Any

import typer

from clinstagram.commands._dispatch import output_success, _get_secrets
from clinstagram.models import CLIResponse, ExitCode


Check = dict[str, Any]


def _check(name: str, status: str, message: str, suggestion: str | None = None) -> Check:
    c: Check = {"name": name, "status": status, "message": message}
    if suggestion:
        c["suggestion"] = suggestion
    return c


def _detect_install_mode() -> tuple[str, bool]:
    """Return (label, is_editable) using importlib.metadata + direct_url.json.

    This is the canonical way to detect editable installs — far more reliable
    than string-matching sys.executable or cwd.
    """
    try:
        from importlib.metadata import distribution
        dist = distribution("clinstagram")
    except PackageNotFoundError:
        return "unknown", False

    # PEP 660 / PEP 610: direct_url.json indicates editable or VCS installs
    try:
        direct_url = dist.read_text("direct_url.json")
        if direct_url:
            data = json.loads(direct_url)
            if data.get("dir_info", {}).get("editable"):
                return "editable", True
            if "vcs_info" in data:
                return "vcs", False
    except Exception:
        pass

    installer = ""
    try:
        txt = dist.read_text("INSTALLER") or ""
        installer = txt.strip().lower()
    except Exception:
        pass

    if installer:
        return installer, False
    return "pip", False


def _keyring_check() -> Check:
    try:
        import keyring  # type: ignore
        kr = keyring.get_keyring()
        name = type(kr).__name__
    except Exception as exc:
        return _check("keyring_backend", "fail", f"keyring module unavailable: {exc}")

    if "Plaintext" in name or "File" in name:
        return _check(
            "keyring_backend",
            "fail",
            f"{name} stores secrets in plaintext",
            "Install a real keyring backend (e.g., libsecret, KWallet) or use macOS Keychain",
        )
    if name == "Null" or "NullKeyring" in name:
        return _check(
            "keyring_backend",
            "fail",
            "No keyring backend available",
            "Install `keyring` with a platform backend (macOS Keychain, libsecret, etc.)",
        )
    return _check("keyring_backend", "pass", name)


def run_doctor(ctx: typer.Context, deep: bool = False, account: str | None = None) -> None:
    # Scope: use the local --account override, else the global --account from ctx
    target_account = account or ctx.obj.get("account", "default")
    config = ctx.obj["config"]
    config_dir_override = ctx.obj.get("config_dir")

    # Resolve effective config dir
    from clinstagram.config import get_config_dir
    config_dir = get_config_dir(config_dir_override)

    checks: list[Check] = []

    # 1. Config directory
    if config_dir.exists():
        if os.access(config_dir, os.W_OK):
            checks.append(_check("config_dir", "pass", f"{config_dir} exists and is writable"))
        else:
            checks.append(_check(
                "config_dir", "fail",
                f"{config_dir} exists but is not writable",
                f"chmod u+w {config_dir}",
            ))
    else:
        checks.append(_check(
            "config_dir", "warn",
            f"{config_dir} does not yet exist (will be created on next write)",
        ))

    # 2. Config "loaded" — load_config() runs on startup and auto-defaults, so
    # what we report here is the effective config, not file presence.
    checks.append(_check(
        "config",
        "pass",
        f"compliance_mode={config.compliance_mode.value}, default_account={config.default_account}",
    ))

    # 3. Keyring
    checks.append(_keyring_check())

    # 4. Current account's sessions (do not try to enumerate all accounts —
    # keyring does not support listing. Scoped to --account.)
    secrets = _get_secrets(ctx)
    for slot, label in (
        ("private_session", f"session.{target_account}.private"),
        ("graph_ig_token", f"session.{target_account}.graph_ig"),
        ("graph_fb_token", f"session.{target_account}.graph_fb"),
    ):
        value = secrets.get(target_account, slot)
        if not value:
            checks.append(_check(
                label, "warn",
                f"not configured for account '{target_account}'",
                {
                    "private_session": f"clinstagram auth login --username <user> --account {target_account}",
                    "graph_ig_token": f"pbpaste | clinstagram --account {target_account} auth connect-ig --token-stdin",
                    "graph_fb_token": f"pbpaste | clinstagram --account {target_account} auth connect-fb --token-stdin --ig-user-id <id>",
                }[slot],
            ))
            continue

        if slot == "private_session":
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, dict):
                    raise ValueError("not a JSON object")
                has_uuid = any(k for k in parsed if "uuid" in k.lower())
                msg = "session blob present and parseable" + ("" if has_uuid else " (no uuids — may be stale)")
                checks.append(_check(label, "pass", msg))
            except Exception as exc:
                checks.append(_check(
                    label, "fail",
                    f"session blob unparseable: {exc}",
                    f"clinstagram auth login --username <user> --account {target_account}",
                ))
        else:
            checks.append(_check(label, "pass", "token present"))

    # 5. Optional deep probes (cost quota, risk detection)
    if deep:
        # Private: use auth probe helper if we have a session
        if secrets.get(target_account, "private_session"):
            try:
                from instagrapi import Client  # type: ignore
                from clinstagram.auth.private_login import _validate_session

                cl = Client()
                cl.set_settings(json.loads(secrets.get(target_account, "private_session") or "{}"))
                ok = _validate_session(cl)
                checks.append(_check(
                    f"deep.{target_account}.private",
                    "pass" if ok else "fail",
                    "live probe: timeline_feed reachable" if ok else "live probe failed",
                    None if ok else "clinstagram auth login --username <user>",
                ))
            except Exception as exc:
                checks.append(_check(
                    f"deep.{target_account}.private", "fail",
                    f"deep probe error: {exc}",
                    "clinstagram auth login --username <user>",
                ))

        # PyPI reachability (needed by `update`)
        try:
            import httpx  # type: ignore
            r = httpx.get("https://pypi.org/pypi/clinstagram/json", timeout=5.0)
            r.raise_for_status()
            checks.append(_check("deep.pypi", "pass", f"pypi.org reachable (status={r.status_code})"))
        except Exception as exc:
            checks.append(_check("deep.pypi", "warn", f"pypi.org unreachable: {exc}"))

    # 6. Runtime + deps
    checks.append(_check("python", "pass", sys.version.split()[0]))
    for dep in ("instagrapi", "typer", "httpx", "pydantic", "rich", "keyring", "packaging"):
        try:
            v = pkg_version(dep)
            checks.append(_check(dep, "pass", v))
        except PackageNotFoundError:
            checks.append(_check(dep, "fail", "not installed", f"pip install {dep}"))

    # 7. Proxy
    if config.proxy:
        checks.append(_check("proxy", "pass", config.proxy))
    else:
        checks.append(_check("proxy", "pass", "not configured (ok)"))

    # 8. ffmpeg (optional)
    if shutil.which("ffmpeg"):
        checks.append(_check("ffmpeg", "pass", "present in PATH (required for --backend private video posting)"))
    else:
        checks.append(_check(
            "ffmpeg", "warn",
            "not found in PATH (required only for video posting on private backend)",
            "brew install ffmpeg",
        ))

    # 9. Install mode (affects whether `update` can self-upgrade)
    label, editable = _detect_install_mode()
    if editable:
        checks.append(_check(
            "install_mode", "warn",
            f"{label} install — `clinstagram update` will refuse to modify this environment",
            "git pull",
        ))
    else:
        checks.append(_check("install_mode", "pass", f"{label}"))

    # Summary
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        summary[c["status"]] = summary.get(c["status"], 0) + 1

    # Exit code:
    #   pass / warn only → 0
    #   any fail with name starting 'session.' or 'deep.*.private' → AUTH_ERROR (2)
    #   any deep.* fail with upstream → API_ERROR (4)
    #   any other local fail → USER_ERROR (1)
    exit_code = ExitCode.SUCCESS
    if summary["fail"] > 0:
        failed = [c for c in checks if c["status"] == "fail"]
        if any(c["name"].startswith("session.") or c["name"].startswith("deep.") and "private" in c["name"] for c in failed):
            exit_code = ExitCode.AUTH_ERROR
        elif any(c["name"].startswith("deep.") for c in failed):
            exit_code = ExitCode.API_ERROR
        else:
            exit_code = ExitCode.USER_ERROR

    # Always emit success-shaped envelope so agents keep the structured report.
    # Inject the severity via exit_code in the envelope; raise typer.Exit with
    # the same code after emitting.
    response = CLIResponse(exit_code=exit_code, data={"checks": checks, "summary": summary})
    if ctx.obj.get("json"):
        typer.echo(response.to_json())
    else:
        from rich.console import Console
        console = Console()
        for c in checks:
            icon = {"pass": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}[c["status"]]
            console.print(f"  {icon} {c['name']}: {c['message']}")
            if c.get("suggestion"):
                console.print(f"      [cyan]→[/cyan] {c['suggestion']}")
        console.print(
            f"\n[bold]Summary:[/bold] "
            f"[green]{summary['pass']} pass[/green], "
            f"[yellow]{summary['warn']} warn[/yellow], "
            f"[red]{summary['fail']} fail[/red]"
        )

    if exit_code != ExitCode.SUCCESS:
        raise typer.Exit(code=int(exit_code))
