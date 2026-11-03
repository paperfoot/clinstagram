"""Shared dispatch logic — routes a Feature to a backend, calls the action, outputs the result."""
from __future__ import annotations

import contextlib
import json
import re
import sys
import random
import time
from typing import Any, Callable

import typer

def make_subgroup(help_text: str) -> typer.Typer:
    """Create a sub-Typer with proper no-args help."""
    sub = typer.Typer(help=help_text, no_args_is_help=True)
    return sub

from clinstagram.auth.keychain import BACKEND_TOKEN_MAP, SecretsStore
from clinstagram.backends.base import Backend
from clinstagram.backends.capabilities import READ_ONLY_FEATURES, Feature, can_backend_do
from clinstagram.backends.router import Router
from clinstagram.config import ComplianceMode
from clinstagram.media import cleanup_temp_files, resolve_media
from clinstagram.models import CLIError, CLIResponse, ExitCode

from clinstagram._console import console


_RATE_TYPES = {"PleaseWaitFewMinutes", "RateLimitError", "ClientThrottledError", "FeedbackRequired"}
_AUTH_TYPES = {
    "LoginRequired", "ClientLoginRequired", "BadPassword", "ReloginAttemptExceeded",
    "TwoFactorRequired",
}
# instagrapi falls back to the logged-out web API after a private-API miss; that
# fallback's 401 says nothing about our session, so a not-found anywhere in the
# chain wins over it.
_NOT_FOUND_TYPES = {"UserNotFound", "MediaNotFound", "ClientNotFoundError", "HashtagNotFound"}
_GRAPH_RATE_CODES = {4, 17, 32, 613}  # app / user / page / custom rate limits
_GRAPH_AUTH_CODES = {102, 190}  # session invalid, access token invalid or expired
_RATE_RE = re.compile(r"rate.?limit|request limit|throttl|too many requests|please wait a few minutes|\b429\b", re.I)
_CHALLENGE_RE = re.compile(r"challenge_required|checkpoint_required|\bchallenge\b", re.I)
_AUTH_RE = re.compile(
    r"login_required|not logged in|unauthori[sz]ed|\b401\b|session (?:has )?expired|"
    r"invalid (?:access )?token|oauth",
    re.I,
)


def _classify_error(exc: BaseException) -> str:
    """Map a backend failure to not_found | rate | challenge | auth | api.

    Backends wrap instagrapi errors, so walk the cause/context chain and trust
    exception types first; message patterns are a fallback for Graph/HTTP errors.
    """
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and all(cur is not c for c in chain):
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    if any(type(c).__name__ in _NOT_FOUND_TYPES for c in chain):
        return "not_found"
    seen: set[int] = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__
        if name in _RATE_TYPES:
            return "rate"
        if "Challenge" in name or "Checkpoint" in name or "Captcha" in name:
            return "challenge"
        if name in _AUTH_TYPES:
            return "auth"
        if name == "GraphAPIError":
            code = getattr(cur, "code", None)
            if code in _GRAPH_RATE_CODES or (isinstance(code, int) and 80001 <= code <= 80014):
                return "rate"
            if code in _GRAPH_AUTH_CODES or getattr(cur, "status_code", None) == 401:
                return "auth"
        cur = cur.__cause__ or cur.__context__
    text = str(exc)
    if _RATE_RE.search(text):
        return "rate"
    if _CHALLENGE_RE.search(text):
        return "challenge"
    if _AUTH_RE.search(text):
        return "auth"
    return "api"


def _get_secrets(ctx: typer.Context) -> SecretsStore:
    if "secrets" in ctx.obj:
        return ctx.obj["secrets"]
    return SecretsStore(backend="keyring")


BACKEND_NAMES = ("graph_ig", "graph_fb", "private")


def record_account(
    ctx: typer.Context,
    account: str,
    *,
    username: str | None = None,
    add_backend: str | None = None,
    backends: list[str] | None = None,
) -> None:
    """Create/update the account's registry row in config.toml."""
    from clinstagram.config import AccountEntry, save_config

    config = ctx.obj["config"]
    entry = config.accounts.get(account) or AccountEntry()
    if username:
        entry.username = username
    if backends is not None:
        entry.backends = backends
    if add_backend and add_backend not in entry.backends:
        entry.backends = [b for b in BACKEND_NAMES if b in {*entry.backends, add_backend}]
    config.accounts[account] = entry
    save_config(config, ctx.obj.get("config_dir"))
    ctx.obj.pop("_configured", None)


def forget_account(ctx: typer.Context, account: str) -> None:
    from clinstagram.config import save_config

    config = ctx.obj["config"]
    if config.accounts.pop(account, None) is not None:
        save_config(config, ctx.obj.get("config_dir"))
    ctx.obj.pop("_configured", None)


def probe_backends(ctx: typer.Context, account: str) -> list[str]:
    """Ask the keychain which backends have credentials (slow: one read each)."""
    secrets = _get_secrets(ctx)
    return [b for b in BACKEND_NAMES if secrets.has_backend(account, b)]


def configured_backends(ctx: typer.Context) -> list[str]:
    """Backends with stored credentials for the current account.

    Read from the account registry so routing doesn't open the keychain three
    times per command; an account the registry doesn't know yet is probed once
    and recorded.
    """
    cached = ctx.obj.get("_configured")
    if cached is not None:
        return cached
    account = ctx.obj["account"]
    entry = ctx.obj["config"].accounts.get(account)
    if entry is not None and entry.backends:
        found = list(entry.backends)
    else:
        found = probe_backends(ctx, account)
        if found:
            record_account(ctx, account, backends=found)
    ctx.obj["_configured"] = found
    return found


def _get_router(ctx: typer.Context) -> Router:
    config = ctx.obj["config"]
    return Router(
        account=ctx.obj["account"],
        compliance_mode=config.compliance_mode,
        secrets=_get_secrets(ctx),
        available=configured_backends(ctx),
    )


def _get_http_client(ctx: typer.Context) -> "httpx.Client":
    """Return a shared httpx.Client for Graph API calls, creating one if needed."""
    import httpx

    if "_http_client" not in ctx.obj:
        ctx.obj["_http_client"] = httpx.Client(timeout=30.0)
    return ctx.obj["_http_client"]


def _close_http_client(ctx: typer.Context) -> None:
    """Close the shared httpx.Client if one was created."""
    client = ctx.obj.pop("_http_client", None)
    if client:
        client.close()


def _apply_jitter_delay(ctx: typer.Context, feature: Feature) -> None:
    """Apply a randomized delay before mutating actions to reduce detection risk."""
    from clinstagram.backends.capabilities import READ_ONLY_FEATURES

    if feature in READ_ONLY_FEATURES:
        return

    config = ctx.obj["config"]
    rl = config.rate_limits
    if not rl.request_jitter:
        return

    lo = rl.request_delay_min
    hi = rl.request_delay_max
    # Add ±20% variance to avoid constant patterns
    jitter = random.uniform(lo * 0.8, hi * 1.2)
    time.sleep(jitter)


def _instantiate_backend(ctx: typer.Context, backend_name: str, feature: Feature) -> Backend:
    """Create the appropriate Backend instance from stored credentials."""
    secrets = _get_secrets(ctx)
    account = ctx.obj["account"]

    if backend_name == "graph_ig":
        from clinstagram.backends.graph import GraphBackend

        token = secrets.get(account, "graph_ig_token")
        return GraphBackend(token=token, login_type="ig", client=_get_http_client(ctx))

    if backend_name == "graph_fb":
        from clinstagram.backends.graph import GraphBackend

        token = secrets.get(account, "graph_fb_token")
        entry = ctx.obj["config"].accounts.get(account)
        return GraphBackend(token=token, login_type="fb", client=_get_http_client(ctx),
                            ig_user_id=getattr(entry, "ig_user_id", None))

    if backend_name == "private":
        from instagrapi import Client

        from clinstagram.backends.private import PrivateBackend

        session_json = secrets.get(account, "private_session")
        if not session_json:
            raise RuntimeError("No private session stored. Run: clinstagram auth login")
        cl = Client()
        session_data = json.loads(session_json)
        cl.set_settings(session_data)
        proxy = ctx.obj.get("proxy")
        if proxy:
            cl.set_proxy(proxy)

        from clinstagram.backends.capabilities import READ_ONLY_FEATURES
        config = ctx.obj["config"]
        rl = config.rate_limits
        if feature in READ_ONLY_FEATURES:
            cl.delay_range = [0, rl.request_delay_min]
        else:
            cl.delay_range = [rl.request_delay_min, rl.request_delay_max]

        return PrivateBackend(client=cl)

    raise ValueError(f"Unknown backend: {backend_name}")


def _output_response(ctx: typer.Context, response: CLIResponse) -> None:
    if ctx.obj["json"]:
        typer.echo(response.to_json())
    else:
        if isinstance(response.data, list):
            for item in response.data:
                if isinstance(item, dict):
                    for k, v in item.items():
                        typer.echo(f"  {k}: {v}")
                    typer.echo()
                else:
                    typer.echo(f"  {item}")
        elif isinstance(response.data, dict):
            for k, v in response.data.items():
                typer.echo(f"  {k}: {v}")
        else:
            typer.echo(str(response.data))


def _output_error(ctx: typer.Context, error: CLIError) -> None:
    if ctx.obj["json"]:
        typer.echo(error.to_json(), err=True)
    else:
        console.print(f"[red]Error:[/red] {error.error}")
        if error.remediation:
            console.print(f"[yellow]Fix:[/yellow] {error.remediation}")
    raise typer.Exit(code=error.exit_code)


# Fields that are long signed CDN URLs (~600–1,200 chars each) and rarely what an
# agent needs; dropped unless --full. Any string pointing at Instagram's CDN goes too.
HEAVY_FIELDS = frozenset({
    "profile_picture_url", "profile_pic_url", "profile_pic_url_hd", "hd_profile_pic_url_info",
    "thumbnail_url", "video_url", "image_url", "media_url", "display_url",
    "image_versions", "image_versions2", "video_versions",
})
_CDN_URL = re.compile(r"^https?://[^/]*(?:fbcdn\.net|cdninstagram\.com)/", re.I)


def compact_data(obj: Any) -> Any:
    """Token-lean view: drop CDN URL fields and null values, recursively."""
    if isinstance(obj, dict):
        return {
            k: compact_data(v)
            for k, v in obj.items()
            if v is not None
            and k not in HEAVY_FIELDS
            and not (isinstance(v, str) and _CDN_URL.match(v))
        }
    if isinstance(obj, list):
        return [compact_data(v) for v in obj]
    return obj


def parse_fields(spec: str | None) -> list[tuple[str, ...]]:
    if not spec:
        return []
    return [tuple(p for p in part.strip().split(".") if p) for part in spec.split(",") if part.strip()]


def project_data(obj: Any, paths: list[tuple[str, ...]]) -> Any:
    """Keep only the requested (dotted) fields of each item; lists map over items."""
    if isinstance(obj, list):
        return [project_data(v, paths) for v in obj]
    if not isinstance(obj, dict):
        return obj
    heads: dict[str, list[tuple[str, ...]]] = {}
    for p in paths:
        heads.setdefault(p[0], []).append(p[1:])
    out: dict[str, Any] = {}
    for key, rests in heads.items():
        if key not in obj:
            continue
        out[key] = obj[key] if any(not r for r in rests) else project_data(obj[key], rests)
    return out


def _top_level_keys(data: Any) -> set[str]:
    items = data if isinstance(data, list) else [data]
    keys: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            keys.update(item)
    return keys


def shape_output(ctx: typer.Context, data: Any) -> Any:
    """Apply --fields (exact projection, full fidelity) or the compact default."""
    obj = ctx.obj or {}
    paths = parse_fields(obj.get("fields"))
    if paths:
        available = _top_level_keys(data)
        unknown = sorted({p[0] for p in paths} - available)
        if unknown and available:
            output_error(ctx, CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Unknown field(s) for --fields: {', '.join(unknown)}",
                remediation=f"Available: {', '.join(sorted(available))}",
            ))
        return project_data(data, paths)
    if obj.get("full"):
        return data
    return compact_data(data)


def output_success(ctx: typer.Context, data: Any, backend_used: str | None = None) -> None:
    """Emit a standard success envelope."""
    _output_response(ctx, CLIResponse(data=shape_output(ctx, data), backend_used=backend_used))


def output_error(ctx: typer.Context, error: CLIError) -> None:
    """Emit a standard error envelope and exit."""
    _output_error(ctx, error)


def strip_at(username: str) -> str:
    """Remove leading @ from usernames."""
    return username.lstrip("@")


def _require_growth(ctx: typer.Context) -> None:
    """Abort if --enable-growth-actions was not passed."""
    if not ctx.obj.get("enable_growth"):
        err = CLIError(
            exit_code=ExitCode.POLICY_BLOCKED,
            error="Growth actions are disabled by default",
            remediation="Add --enable-growth-actions flag",
        )
        if ctx.obj.get("json"):
            typer.echo(err.to_json(), err=True)
        else:
            typer.echo("Error: Growth actions are disabled by default.")
            typer.echo("Add --enable-growth-actions to enable follow/unfollow and automated engagement.")
        raise typer.Exit(code=ExitCode.POLICY_BLOCKED)


def stage(source: str, backend_name: str) -> str:
    """Resolve a media source (path or URL) for the given backend."""
    needs_url = backend_name.startswith("graph")
    return resolve_media(source, needs_url=needs_url)


def _backend_remediation(backend_name: str) -> str:
    if backend_name == "private":
        return "Run: clinstagram auth login"
    suffix = backend_name.split("_", 1)[1]
    return f"Run: clinstagram auth connect-{suffix}"


def _backend_is_configured(ctx: typer.Context, backend_name: str) -> bool:
    return backend_name in configured_backends(ctx)


def can_use_backend(ctx: typer.Context, feature: Feature, backend_name: str) -> bool:
    """Return True if a backend is configured, supports the feature, and passes policy."""
    router = _get_router(ctx)
    return (
        _backend_is_configured(ctx, backend_name)
        and can_backend_do(backend_name, feature)
        and router._is_allowed_by_policy(backend_name, feature)
    )


def preferred_private_backend(ctx: typer.Context, feature: Feature) -> str | None:
    """Return 'private' when it is a usable preference for the feature."""
    if can_use_backend(ctx, feature, "private"):
        return "private"
    return None


def _forced_backend_error(ctx: typer.Context, feature: Feature, backend_name: str) -> CLIError:
    config = ctx.obj["config"]
    router = _get_router(ctx)
    if not _backend_is_configured(ctx, backend_name):
        return CLIError(
            exit_code=ExitCode.AUTH_ERROR,
            error=f"Backend '{backend_name}' is not configured",
            remediation=_backend_remediation(backend_name),
        )
    if not can_backend_do(backend_name, feature):
        return CLIError(
            exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
            error=f"Backend '{backend_name}' does not support '{feature.value}'",
            remediation="Retry without --backend or choose a backend that supports this feature",
        )
    if not router._is_allowed_by_policy(backend_name, feature):
        return CLIError(
            exit_code=ExitCode.POLICY_BLOCKED,
            error=(
                f"Feature '{feature.value}' is blocked by compliance mode "
                f"'{config.compliance_mode.value}' on backend '{backend_name}'"
            ),
            remediation="Remove --backend or change compliance mode with: clinstagram config mode private-enabled",
        )
    return CLIError(
        exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
        error=f"No backend available for '{feature.value}'",
        remediation="Run: clinstagram auth status  — then connect a backend",
    )


def resolve_backend_name(
    ctx: typer.Context,
    feature: Feature,
    preferred_backend: str | None = None,
) -> str | None:
    """Resolve the backend for a feature, enforcing forced-backend policy checks."""
    forced = ctx.obj.get("backend")
    if forced and forced.value != "auto":
        backend_name = forced.value
        if can_use_backend(ctx, feature, backend_name):
            return backend_name
        output_error(ctx, _forced_backend_error(ctx, feature, backend_name))

    if preferred_backend and can_use_backend(ctx, feature, preferred_backend):
        return preferred_backend

    router = _get_router(ctx)
    return router.route(feature)


def dispatch(
    ctx: typer.Context,
    feature: Feature,
    action: Callable[[Backend], Any],
    preferred_backend: str | None = None,
) -> None:
    """Route feature → backend → call action → output result."""
    backend_name = resolve_backend_name(ctx, feature, preferred_backend=preferred_backend)

    if backend_name is None:
        # Policy block (a configured backend could do it) vs. nothing configured
        config = ctx.obj["config"]
        mode = config.compliance_mode
        blocked = [
            b for b in ("graph_ig", "graph_fb", "private")
            if _backend_is_configured(ctx, b) and can_backend_do(b, feature)
        ]
        if blocked:
            # hybrid-safe already unlocks private reads; writes need private-enabled
            relaxes_enough = mode == ComplianceMode.OFFICIAL_ONLY and feature in READ_ONLY_FEATURES
            next_mode = "hybrid-safe" if relaxes_enough else "private-enabled"
            output_error(ctx, CLIError(
                exit_code=ExitCode.POLICY_BLOCKED,
                error=(
                    f"Feature '{feature.value}' is blocked by compliance mode '{mode.value}' "
                    f"(configured backend that supports it: {', '.join(blocked)})"
                ),
                remediation=f"Run: clinstagram config mode {next_mode}",
            ))
        else:
            output_error(ctx, CLIError(
                exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
                error=f"No configured backend supports '{feature.value}'",
                remediation="Run: clinstagram auth status  — then connect a backend",
            ))
        return

    if ctx.obj.get("dry_run"):
        output_success(
            ctx,
            {
                "dry_run": True,
                "feature": feature.value,
                "backend": backend_name,
            },
            backend_used=backend_name,
        )
        return

    try:
        backend = _instantiate_backend(ctx, backend_name, feature)
    except Exception as exc:
        output_error(ctx, CLIError(
            exit_code=ExitCode.AUTH_ERROR,
            error=f"Failed to initialize {backend_name}: {exc}",
            remediation=_backend_remediation(backend_name),
        ))
        return

    try:
        # Apply jitter delay for write operations to reduce detection risk
        _apply_jitter_delay(ctx, feature)
        # Store backend_name in context for stage() calls in action lambdas
        ctx.obj["_backend_name"] = backend_name
        # instagrapi print()s progress ("Analyzing CLIP file ...") to stdout,
        # which would corrupt the JSON envelope agents parse. Send it to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            result = action(backend)
        output_success(ctx, result, backend_used=backend_name)
    except typer.Exit:
        raise  # an envelope was already written (e.g. --fields validation)
    except NotImplementedError as exc:
        output_error(ctx, CLIError(
            exit_code=ExitCode.CAPABILITY_UNAVAILABLE,
            error=str(exc),
            remediation="Try a different backend: --backend private",
        ))
    except Exception as exc:
        error_str = str(exc)
        kind = _classify_error(exc)
        if kind == "not_found":
            output_error(ctx, CLIError(
                exit_code=ExitCode.USER_ERROR,
                error=f"Not found on Instagram: {error_str}",
                remediation="Check the username, media ID, shortcode or URL",
            ))
        elif kind == "rate":
            output_error(ctx, CLIError(
                exit_code=ExitCode.RATE_LIMITED,
                error=error_str,
                retry_after=60,
            ))
        elif kind == "challenge":
            output_error(ctx, CLIError(
                exit_code=ExitCode.CHALLENGE_REQUIRED,
                error=error_str,
                challenge_type="unknown",
            ))
        elif kind == "auth":
            output_error(ctx, CLIError(
                exit_code=ExitCode.AUTH_ERROR,
                error=error_str,
                remediation="Run: clinstagram auth login",
            ))
        else:
            output_error(ctx, CLIError(
                exit_code=ExitCode.API_ERROR,
                error=error_str,
            ))
    finally:
        cleanup_temp_files()
        _close_http_client(ctx)
