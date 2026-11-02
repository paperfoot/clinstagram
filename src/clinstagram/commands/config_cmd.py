from __future__ import annotations

import typer

from clinstagram.commands._dispatch import make_subgroup, output_error, output_success
from clinstagram.config import ComplianceMode, WRITABLE_CONFIG_KEYS, save_config
from clinstagram.models import CLIError, ExitCode


def _redact_proxy(value: object) -> object:
    """Redact credentials from proxy URLs for display."""
    if not isinstance(value, str):
        return value
    # Redact user:pass@ in proxy URLs
    import re
    return re.sub(r"://[^@]+@", "://***:***@", value)

config_app = make_subgroup("Manage configuration")


@config_app.command("show")
def show(ctx: typer.Context):
    """Print current configuration."""
    config = ctx.obj["config"]
    data = config.to_dict()
    # Redact proxy credentials in output
    if "proxy" in data and data["proxy"]:
        data["proxy"] = _redact_proxy(data["proxy"])
    for entry in (data.get("accounts") or {}).values():
        if entry.get("proxy"):
            entry["proxy"] = _redact_proxy(entry["proxy"])
    if not ctx.obj["json"]:
        for key, val in data.items():
            typer.echo(f"  {key}: {val}")
        return
    output_success(ctx, data)


@config_app.command("mode")
def set_mode(ctx: typer.Context, mode: ComplianceMode = typer.Argument(...)):
    """Set compliance mode (official-only, hybrid-safe, private-enabled)."""
    config = ctx.obj["config"]
    config.compliance_mode = mode
    save_config(config, ctx.obj.get("config_dir"))
    if not ctx.obj["json"]:
        typer.echo(f"Compliance mode set to: {mode.value}")
        return
    output_success(ctx, {"compliance_mode": mode.value})


@config_app.command("set")
def set_value(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Config key"),
    value: str = typer.Argument(..., help="New value"),
):
    """Set a configuration value."""
    config = ctx.obj["config"]
    if key not in WRITABLE_CONFIG_KEYS:
        output_error(
            ctx,
            CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Config key '{key}' is not writable via 'config set'",
                remediation=f"Writable keys: {', '.join(sorted(WRITABLE_CONFIG_KEYS))}",
            ),
        )
        return
    try:
        setattr(config, key, value)
    except (ValueError, TypeError) as exc:
        output_error(
            ctx,
            CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Invalid value for '{key}': {exc}",
            ),
        )
        return
    save_config(config, ctx.obj.get("config_dir"))
    if not ctx.obj["json"]:
        typer.echo(f"Set {key} = {value}")
        return
    output_success(ctx, {"key": key, "value": value})
