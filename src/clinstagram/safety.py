"""Write-safety guard for private-API (instagrapi) writes.

A per-account ledger under ``<config dir>/accounts/<account>/safety.json``,
shared across processes through an exclusive file lock, records every
private-API write with a timestamp. Before a write runs, the guard checks:

1. an active cooldown, set after Instagram pushed back (429, feedback_required,
   challenge, "action blocked"/spam). While it lasts, every private write for
   the account is refused; reads stay allowed.
2. the rolling-window caps in ``[rate_limits]`` of config.toml.

Graph API calls never pass through here: Meta enforces its own quotas.

Owner escape hatch: ``CLINSTAGRAM_IGNORE_COOLDOWN=1`` skips the cooldown check
(not the caps; tune those in config.toml, -1 disables one). It prints a warning
on every write it lets through, because pushing past a block is exactly how the
2 Nov 2026 link restriction happened.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

IGNORE_COOLDOWN_ENV = "CLINSTAGRAM_IGNORE_COOLDOWN"

# Exception type names that mean "refused by local write-safety policy" (exit 6).
# Inside the CLI the guard writes the POLICY_BLOCKED envelope itself and exits,
# so _dispatch needs no change; this list is for any classifier that wants to
# recognise the exception when it escapes (library use).
POLICY_EXCEPTION_TYPES = frozenset({"SafetyBlocked"})

HOUR = 3600
DAY = 24 * HOUR

# Cooldowns after Instagram pushes back.
COOLDOWN_SECONDS = {
    "feedback": DAY,
    "challenge": DAY,
    "spam": DAY,
    "rate": HOUR,
}

# action -> (RateLimits field, window in seconds). Actions not listed are
# recorded but uncapped ("moderation": deletes, pins, caption edits, approvals).
CAPS: dict[str, tuple[str, int]] = {
    "post": ("private_posts_per_day", DAY),
    "story": ("private_stories_per_day", DAY),
    "dm": ("private_dm_per_hour", HOUR),
    "comment": ("private_comments_per_hour", HOUR),
    "like": ("private_likes_per_hour", HOUR),
    "follow": ("private_follows_per_day", DAY),
    "profile_edit": ("private_profile_edits_per_day", DAY),
}
USERNAME_FIELD = "private_username_change_days"
ACTIONS = (*CAPS, "moderation")

_KEEP_SECONDS = 30 * DAY  # longer than any window, so status can show history
_MAX_EVENTS = 50


class SafetyBlocked(Exception):
    """A private-API write refused up front by the write-safety guard."""

    def __init__(self, message: str, *, reason: str, retry_after: Optional[int] = None,
                 remediation: Optional[str] = None):
        super().__init__(message)
        self.reason = reason  # "cooldown" | "cap" | "username"
        self.retry_after = retry_after
        self.remediation = remediation

    def to_cli_error(self) -> Any:
        from clinstagram.models import CLIError, ExitCode

        return CLIError(
            exit_code=ExitCode.POLICY_BLOCKED,
            error=str(self),
            remediation=self.remediation,
            retry_after=self.retry_after,
        )


def _current_cli_context() -> Any:
    """The Typer/Click context of the command running now, or None. Typer >= 0.2x
    vendors Click as typer._click; older releases use the click package."""
    import importlib

    for mod in ("typer._click.globals", "click"):
        try:
            ctx = importlib.import_module(mod).get_current_context(silent=True)
        except Exception:
            continue
        if ctx is not None:
            return ctx
    return None


def raise_blocked(blocked: SafetyBlocked) -> None:
    """Inside the CLI: emit the POLICY_BLOCKED (exit 6) envelope and exit.
    Elsewhere: re-raise the SafetyBlocked exception."""
    ctx = _current_cli_context()
    if ctx is not None and isinstance(ctx.obj, dict) and "json" in ctx.obj:
        from clinstagram.commands._dispatch import output_error

        output_error(ctx, blocked.to_cli_error())  # raises typer.Exit(6)
    raise blocked


# ── Time formatting ────────────────────────────────────────────────


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _span(seconds: float) -> str:
    s = max(0, int(seconds))
    d, s = divmod(s, DAY)
    h, s = divmod(s, HOUR)
    m = s // 60
    parts = [f"{d}d"] if d else []
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _window_name(seconds: int) -> str:
    return "1h" if seconds == HOUR else ("24h" if seconds == DAY else _span(seconds))


# ── Block detection ────────────────────────────────────────────────

_RATE_NAMES = {"PleaseWaitFewMinutes", "ClientThrottledError", "RateLimitError"}
_FEEDBACK_RE = re.compile(r"feedback_required|feedback required", re.I)
_CHALLENGE_RE = re.compile(r"challenge_required|checkpoint_required", re.I)
_SPAM_RE = re.compile(r"\bspam\b|action[ _]blocked|restrict certain activity", re.I)
_RATE_RE = re.compile(
    r"\b429\b|too many requests|please wait a few minutes|throttl|rate.?limit", re.I
)


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    cur: Optional[BaseException] = exc
    while cur is not None and all(cur is not c for c in out):
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _status_code(exc: BaseException) -> Any:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return status if isinstance(status, int) else None


def classify_block(exc: BaseException) -> Optional[str]:
    """Return feedback | challenge | spam | rate when `exc` means Instagram pushed
    back on this account, else None. The 24 h kinds win over a plain 429."""
    chain = [c for c in _chain(exc) if type(c).__name__ not in POLICY_EXCEPTION_TYPES]
    if not chain:
        return None
    names = {type(c).__name__ for c in chain}
    text = " | ".join(str(c) for c in chain)
    if "FeedbackRequired" in names or _FEEDBACK_RE.search(text):
        return "feedback"
    if any("Challenge" in n or "Checkpoint" in n or "Captcha" in n for n in names) \
            or _CHALLENGE_RE.search(text):
        return "challenge"
    if _SPAM_RE.search(text):
        return "spam"
    if names & _RATE_NAMES or any(_status_code(c) == 429 for c in chain) or _RATE_RE.search(text):
        return "rate"
    return None


# ── Ledger ─────────────────────────────────────────────────────────


def _safe_account(account: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._@-]", "_", account.strip()) or "default"
    return "_" if name in (".", "..") else name


def ledger_path(account: str, config_dir: Optional[Path] = None) -> Path:
    from clinstagram.config import get_account_dir

    return get_account_dir(_safe_account(account), config_dir) / "safety.json"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def _empty() -> dict:
    return {"version": 1, "actions": [], "cooldown": None, "events": []}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        # Keep the damaged file for inspection rather than silently losing a cooldown.
        with contextlib.suppress(OSError):
            path.replace(path.with_suffix(f".corrupt-{int(time.time())}.json"))
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base.update(data)
    if not isinstance(base.get("actions"), list):
        base["actions"] = []
    if not isinstance(base.get("events"), list):
        base["events"] = []
    return base


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


@dataclass
class Reservation:
    id: str
    action: str


class SafetyGuard:
    """Caps, cooldowns and the action ledger for one account."""

    def __init__(self, account: str, limits: Any, config_dir: Optional[Path] = None,
                 clock: Callable[[], float] = time.time):
        self.account = account
        self.limits = limits
        self.path = ledger_path(account, config_dir)
        self.clock = clock

    # -- internals --

    def _cap(self, field: str) -> int:
        return int(getattr(self.limits, field))

    @contextlib.contextmanager
    def _ledger(self) -> Iterator[dict]:
        with _locked(self.path):
            data = _read(self.path)
            now = self.clock()
            data["actions"] = [
                a for a in data["actions"]
                if isinstance(a, dict) and now - float(a.get("ts", 0)) < _KEEP_SECONDS
            ]
            yield data
            _write(self.path, data)

    @staticmethod
    def _active_cooldown(data: dict, now: float) -> Optional[dict]:
        cd = data.get("cooldown")
        if isinstance(cd, dict) and float(cd.get("until", 0)) > now:
            return cd
        return None

    # -- public API --

    def begin(self, action: str, *, username_change: bool = False,
              source: str = "") -> Reservation:
        """Check the cooldown and caps, then record the write before it runs
        (so two processes can't both take the last slot). Raises SafetyBlocked."""
        if action not in ACTIONS:
            raise ValueError(f"unknown safety action: {action}")
        with self._ledger() as data:
            now = self.clock()
            cd = self._active_cooldown(data, now)
            if cd is not None:
                if os.environ.get(IGNORE_COOLDOWN_ENV) == "1":
                    sys.stderr.write(
                        f"WARNING: {IGNORE_COOLDOWN_ENV}=1 is overriding an active cooldown for "
                        f"account '{self.account}' ({cd.get('kind')}, until {_iso(cd['until'])}). "
                        "Writing through a block risks a longer restriction.\n"
                    )
                else:
                    raise SafetyBlocked(
                        f"Private-API writes for account '{self.account}' are paused until "
                        f"{_iso(cd['until'])} ({_span(cd['until'] - now)} left): Instagram "
                        f"returned {cd.get('kind')} on {cd.get('source') or 'a private call'} "
                        f"at {_iso(cd.get('started'))}. Detail: {cd.get('detail', '')}",
                        reason="cooldown",
                        retry_after=int(cd["until"] - now) + 1,
                        remediation=(
                            "Wait for the cooldown to end; reads still work and Graph API "
                            "backends are unaffected. Check: clinstagram safety status. "
                            f"Owner override (risky): {IGNORE_COOLDOWN_ENV}=1"
                        ),
                    )
            if action in CAPS:
                field, window = CAPS[action]
                cap = self._cap(field)
                recent = sorted(
                    float(a["ts"]) for a in data["actions"]
                    if a.get("action") == action and now - float(a["ts"]) < window
                )
                if cap >= 0 and len(recent) >= cap:
                    free_at = recent[len(recent) - cap] + window if cap else now + window
                    raise SafetyBlocked(
                        f"Safety cap reached for private-API {action} on account "
                        f"'{self.account}': {len(recent)} in the last {_window_name(window)} "
                        f"(rate_limits.{field} = {cap}). Next slot at {_iso(free_at)}.",
                        reason="cap",
                        retry_after=int(free_at - now) + 1,
                        remediation=(
                            f"Wait, or raise rate_limits.{field} in config.toml (-1 disables "
                            "the cap). Check: clinstagram safety status"
                        ),
                    )
            if username_change:
                days = self._cap(USERNAME_FIELD)
                window = days * DAY
                last = max(
                    (float(a["ts"]) for a in data["actions"] if a.get("username_change")),
                    default=None,
                )
                if days > 0 and last is not None and now - last < window:
                    free_at = last + window
                    raise SafetyBlocked(
                        f"Username was changed at {_iso(last)}; account '{self.account}' allows "
                        f"one username change per {days} days. Next allowed at {_iso(free_at)}.",
                        reason="username",
                        retry_after=int(free_at - now) + 1,
                        remediation=(
                            f"Wait, or change rate_limits.{USERNAME_FIELD} in config.toml "
                            "(0 disables). Renames in quick succession look like an account "
                            "takeover to Instagram."
                        ),
                    )
            rec: dict[str, Any] = {"id": uuid.uuid4().hex, "action": action, "ts": now}
            if source:
                rec["source"] = source
            if username_change:
                rec["username_change"] = True
            data["actions"].append(rec)
            return Reservation(rec["id"], action)

    def release(self, reservation: Reservation) -> None:
        """Drop a reservation whose write failed before taking effect."""
        with self._ledger() as data:
            data["actions"] = [a for a in data["actions"] if a.get("id") != reservation.id]

    def record_block(self, exc: BaseException, source: str = "") -> Optional[dict]:
        """If `exc` is Instagram pushing back, start (or extend) a cooldown."""
        kind = classify_block(exc)
        if kind is None:
            return None
        with self._ledger() as data:
            now = self.clock()
            until = now + COOLDOWN_SECONDS[kind]
            detail = str(exc).strip().replace("\n", " ")[:300]
            current = self._active_cooldown(data, now)
            event = {"kind": kind, "source": source, "started": now, "until": until,
                     "detail": detail}
            data["events"] = (data["events"] + [event])[-_MAX_EVENTS:]
            if current is None or until > float(current["until"]):
                data["cooldown"] = event
            return data["cooldown"]

    def clear_cooldown(self) -> None:
        with self._ledger() as data:
            data["cooldown"] = None

    def status(self) -> dict:
        with self._ledger() as data:
            now = self.clock()
            cd = self._active_cooldown(data, now)
            actions = list(data["actions"])
        caps = []
        for action, (field, window) in CAPS.items():
            cap = self._cap(field)
            recent = sorted(
                float(a["ts"]) for a in actions
                if a.get("action") == action and now - float(a["ts"]) < window
            )
            row: dict[str, Any] = {
                "action": action,
                "limit": cap if cap >= 0 else None,
                "window": _window_name(window),
                "used": len(recent),
                "config_key": f"rate_limits.{field}",
            }
            if cap >= 0:
                row["remaining"] = max(0, cap - len(recent))
                if len(recent) >= cap and recent:
                    row["next_slot_at"] = _iso(recent[len(recent) - cap] + window) if cap else None
            caps.append(row)
        days = self._cap(USERNAME_FIELD)
        last_rename = max(
            (float(a["ts"]) for a in actions if a.get("username_change")), default=None
        )
        rename_next = last_rename + days * DAY if last_rename is not None and days > 0 else None
        return {
            "account": self.account,
            "ledger": str(self.path),
            "cooldown": None if cd is None else {
                "kind": cd.get("kind"),
                "source": cd.get("source"),
                "started": _iso(cd.get("started")),
                "until": _iso(cd["until"]),
                "remaining": _span(cd["until"] - now),
                "detail": cd.get("detail"),
            },
            "ignore_cooldown_override": os.environ.get(IGNORE_COOLDOWN_ENV) == "1",
            "caps": caps,
            "username_change": {
                "min_days_between": days if days > 0 else None,
                "last_change": _iso(last_rename),
                "next_allowed": _iso(rename_next) if rename_next and rename_next > now else "now",
                "config_key": f"rate_limits.{USERNAME_FIELD}",
            },
            "scope": "private-API writes only; Graph API calls are exempt",
        }


def guard_from_click_context() -> Optional[SafetyGuard]:
    """Build the guard for the CLI invocation in progress (account, config caps,
    config dir from ctx.obj), or None outside the CLI."""
    ctx = _current_cli_context()
    obj = getattr(ctx, "obj", None) if ctx is not None else None
    if not isinstance(obj, dict) or "account" not in obj or "config" not in obj:
        return None
    return SafetyGuard(obj["account"], obj["config"].rate_limits, obj.get("config_dir"))
