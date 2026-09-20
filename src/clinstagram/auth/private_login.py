"""Resilient private API login via instagrapi.

Handles session persistence, device UUID preservation, challenge resolution,
TOTP 2FA, proxy enforcement, and delay configuration.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default action delays (seconds) — reduces detection risk
DEFAULT_DELAY_RANGE = [1, 3]

# Device pool — a range of modern devices to derive per-account fingerprints from.
# Using a single static fingerprint across all installs enables mass-flagging.
_DEVICE_POOL = [
    {"android_version": 33, "android_release": "13.0.0", "dpi": "420dpi", "resolution": "1080x2400", "manufacturer": "Google", "device": "panther", "model": "Pixel 7", "cpu": "arm64-v8a"},
    {"android_version": 34, "android_release": "14.0.0", "dpi": "420dpi", "resolution": "1080x2400", "manufacturer": "Google", "device": "husky", "model": "Pixel 8 Pro", "cpu": "arm64-v8a"},
    {"android_version": 33, "android_release": "13.0.0", "dpi": "440dpi", "resolution": "1080x2340", "manufacturer": "samsung", "device": "dm1q", "model": "SM-S911B", "cpu": "arm64-v8a"},
    {"android_version": 34, "android_release": "14.0.0", "dpi": "420dpi", "resolution": "1080x2400", "manufacturer": "Google", "device": "cheetah", "model": "Pixel 7 Pro", "cpu": "arm64-v8a"},
    {"android_version": 33, "android_release": "13.0.0", "dpi": "420dpi", "resolution": "1080x2340", "manufacturer": "OnePlus", "device": "CPH2449", "model": "CPH2449", "cpu": "arm64-v8a"},
]


def _generate_device_for_account(account_name: str) -> dict[str, Any]:
    """Generate a deterministic device fingerprint for a given account.

    Uses HMAC of the account name with a machine-stable seed so that:
    - The same account always gets the same device on the same machine
    - Different accounts get different devices
    - Different machines produce different fingerprints
    """
    import hashlib
    import hmac
    import platform
    import uuid

    # Machine-stable seed: combine hostname + platform for determinism
    machine_seed = f"{platform.node()}:{platform.machine()}:{platform.system()}"
    digest = hmac.new(
        machine_seed.encode(), account_name.encode(), hashlib.sha256
    ).hexdigest()
    index = int(digest, 16) % len(_DEVICE_POOL)
    return dict(_DEVICE_POOL[index])

# Locale → (country_code, country) for common Instagram locales
_LOCALE_MAP = {
    "en_US": (1, "US"),
    "en_GB": (44, "GB"),
    "en_AU": (61, "AU"),
    "en_CA": (1, "CA"),
    "pt_BR": (55, "BR"),
    "es_ES": (34, "ES"),
    "fr_FR": (33, "FR"),
    "de_DE": (49, "DE"),
    "it_IT": (39, "IT"),
    "ja_JP": (81, "JP"),
    "ko_KR": (82, "KR"),
    "zh_CN": (86, "CN"),
    "nl_NL": (31, "NL"),
    "ru_RU": (7, "RU"),
    "tr_TR": (90, "TR"),
    "ar_SA": (966, "SA"),
    "hi_IN": (91, "IN"),
}


def _detect_system_locale() -> str:
    """Best-effort detection of system locale, fallback to en_US."""
    import locale as _locale

    try:
        loc = _locale.getlocale()[0]
        if loc and "_" in loc:
            return loc
    except Exception:
        pass
    return "en_US"


def _locale_to_country(loc: str) -> tuple[int, str]:
    """Map a locale string to (country_code, country_iso)."""
    if loc in _LOCALE_MAP:
        return _LOCALE_MAP[loc]
    # Extract country from locale suffix (e.g. en_GB → GB)
    parts = loc.split("_")
    if len(parts) == 2:
        country = parts[1].upper()
        return (0, country)
    return (0, "US")


@dataclass
class LoginResult:
    success: bool
    username: str = ""
    session_json: str = ""
    error: str = ""
    remediation: str = ""
    challenge_required: bool = False
    relogin: bool = False


@dataclass
class LoginConfig:
    username: str
    password: str = ""
    totp_seed: str = ""
    proxy: str = ""
    delay_range: list[int] = field(default_factory=lambda: list(DEFAULT_DELAY_RANGE))
    challenge_handler: Optional[Callable[[str], str]] = None
    locale: str = ""  # Empty = auto-detect from system
    timezone: str = ""  # Empty = auto-detect from system
    device_settings: dict[str, Any] = field(default_factory=dict)


def _challenge_code_handler(username: str, choice: str) -> str:
    """Default interactive challenge handler — prompts user for verification code."""
    import typer

    method = "SMS" if choice == "sms" else "email"
    code = typer.prompt(f"Enter the {method} verification code sent to your device")
    return code


def _configure_client(client: Any, config: LoginConfig) -> None:
    """Apply proxy, delays, device, locale, and TOTP to an instagrapi Client."""
    if config.proxy:
        client.set_proxy(config.proxy)

    client.delay_range = config.delay_range

    # Set device BEFORE login — uses set_device() so user agent is rebuilt
    # Per-account deterministic fingerprint prevents mass-flagging
    base_device = _generate_device_for_account(config.username)
    device = {**base_device, **(config.device_settings or {})}
    client.set_device(device)

    # Resolve locale — auto-detect if not provided
    locale = config.locale or _detect_system_locale()
    client.set_locale(locale)
    country_code, country = _locale_to_country(locale)
    client.set_country_code(country_code)
    client.set_country(country)

    # Resolve timezone — auto-detect if not provided
    if config.timezone:
        try:
            client.set_timezone_offset(int(config.timezone))
        except ValueError:
            client.set_timezone_offset(0)
    else:
        import time
        client.set_timezone_offset(-time.timezone)

    # Rebuild user agent with new device/locale settings
    client.set_user_agent()

    if config.totp_seed:
        client.totp_seed = config.totp_seed

    # Set challenge handler
    handler = config.challenge_handler or _challenge_code_handler
    client.challenge_code_handler = handler


def _validate_session(client: Any) -> bool:
    """Check if the current session is still valid by hitting a lightweight endpoint."""
    try:
        client.account_info()
        return True
    except Exception:
        return False


def _extract_uuids(settings: dict) -> dict:
    """Pull device UUIDs from settings for fingerprint preservation."""
    uuids = {}
    for key in ("uuid", "phone_id", "device_id", "advertising_id"):
        if key in settings:
            uuids[key] = settings[key]
    # Also preserve device settings (model, android version, etc.)
    if "device_settings" in settings:
        uuids["device_settings"] = settings["device_settings"]
    return uuids


def login_private(config: LoginConfig, existing_session: str = "") -> LoginResult:
    """
    Perform a resilient private API login.

    Flow:
    1. If existing session exists, try to restore it
    2. Validate with account_info()
    3. If validation fails, re-login with preserved device UUIDs
    4. If fresh login, perform full login with challenge handling
    5. Return session JSON for storage in keychain

    Parameters
    ----------
    config : LoginConfig
        Username, password, TOTP seed, proxy, etc.
    existing_session : str
        Previously stored session JSON from keychain (empty for first login).

    Returns
    -------
    LoginResult
        Contains success flag, session JSON for storage, and error details.
    """
    from instagrapi import Client
    from instagrapi.exceptions import (
        BadPassword,
        ChallengeRequired,
        LoginRequired,
        PleaseWaitFewMinutes,
        SentryBlock,
        TwoFactorRequired,
    )

    cl = Client()
    _configure_client(cl, config)

    # ── Try restoring existing session ──────────────────────────────
    if existing_session:
        try:
            session_data = json.loads(existing_session)
            cl.set_settings(session_data)

            if _validate_session(cl):
                logger.info("Session restored successfully for %s", config.username)
                new_session = json.dumps(cl.get_settings())
                return LoginResult(
                    success=True,
                    username=config.username,
                    session_json=new_session,
                )

            # Session invalid — re-login with preserved device fingerprint
            logger.info("Session expired for %s, re-authenticating with preserved UUIDs", config.username)
            old_settings = cl.get_settings()
            uuids = _extract_uuids(old_settings)

            if not config.password:
                return LoginResult(success=False, username=config.username, error="Session expired and no password provided for re-login")

            cl = Client()
            _configure_client(cl, config)
            cl.set_settings({})
            if uuids:
                cl.set_uuids(uuids)

            cl.login(config.username, config.password)

            if _validate_session(cl):
                new_session = json.dumps(cl.get_settings())
                return LoginResult(
                    success=True,
                    username=config.username,
                    session_json=new_session,
                    relogin=True,
                )

            return LoginResult(success=False, username=config.username, error="Re-login succeeded but session validation failed")

        except ChallengeRequired:
            return LoginResult(
                success=False,
                username=config.username,
                error="Instagram challenge required. Try again — the challenge handler will prompt you.",
                challenge_required=True,
            )
        except TwoFactorRequired:
            # Handle 2FA during session restore
            if config.totp_seed:
                try:
                    from instagrapi.mixins.totp import TOTPMixin

                    code = TOTPMixin.totp_generate_code(config.totp_seed)
                    # instagrapi handles the second step through login();
                    # two_factor_login was removed from current releases.
                    cl.login(config.username, config.password, verification_code=code)
                    if _validate_session(cl):
                        new_session = json.dumps(cl.get_settings())
                        return LoginResult(success=True, username=config.username, session_json=new_session)
                except Exception as exc:
                    return LoginResult(success=False, username=config.username, error=f"2FA during session restore failed: {exc}")
            return LoginResult(
                success=False,
                username=config.username,
                error="2FA required but no TOTP seed provided",
            )
        except LoginRequired:
            # Session completely dead — fall through to fresh login
            logger.info("Session completely expired for %s, performing fresh login", config.username)
            old_settings = cl.get_settings()
            uuids = _extract_uuids(old_settings)
            # Fall through to fresh login below, preserving UUIDs
            cl = Client()
            _configure_client(cl, config)
            if uuids:
                cl.set_uuids(uuids)
        except Exception as exc:
            logger.warning("Session restore failed for %s: %s", config.username, exc)
            # Fall through to fresh login
            cl = Client()
            _configure_client(cl, config)

    # ── Fresh login ─────────────────────────────────────────────────
    if not config.password:
        return LoginResult(success=False, username=config.username, error="Authentication required but no session or password provided.")

    try:
        cl.login(config.username, config.password)
    except TwoFactorRequired:
        if config.totp_seed:
            try:
                from instagrapi.mixins.totp import TOTPMixin

                code = TOTPMixin.totp_generate_code(config.totp_seed)
                # Pass the generated TOTP code through instagrapi's supported API.
                cl.login(config.username, config.password, verification_code=code)
            except Exception as exc:
                return LoginResult(success=False, username=config.username, error=f"2FA login failed: {exc}")
        else:
            return LoginResult(
                success=False,
                username=config.username,
                error="2FA required. Provide --totp-seed or set up TOTP in Instagram settings.",
            )
    except ChallengeRequired:
        return LoginResult(
            success=False,
            username=config.username,
            error="Instagram challenge required. Try again — the challenge handler will prompt you.",
            challenge_required=True,
        )
    except BadPassword:
        email_hint = (
            " If you normally sign in with an email address, retry with that exact email."
            if "@" not in config.username else ""
        )
        return LoginResult(
            success=False,
            username=config.username,
            error="Instagram rejected the login. This can mean the password is wrong, "
                  "the login identifier is wrong, or Instagram flagged this login fingerprint.",
            remediation="Verify the exact login identifier (username or email) and password." + email_hint,
        )
    except SentryBlock:
        return LoginResult(
            success=False,
            username=config.username,
            error="Instagram blocked this login as suspicious.",
            remediation="Wait a few minutes, then retry with a matching --locale or --proxy.",
        )
    except PleaseWaitFewMinutes:
        return LoginResult(
            success=False,
            username=config.username,
            error="Instagram asked you to wait before retrying.",
            remediation="Wait a few minutes before retrying. Do not spam login attempts.",
        )
    except Exception as exc:
        return LoginResult(success=False, username=config.username, error=f"Login failed: {exc}")

    # ── Validate ────────────────────────────────────────────────────
    if not _validate_session(cl):
        return LoginResult(success=False, username=config.username, error="Login succeeded but session validation failed")

    new_session = json.dumps(cl.get_settings())
    return LoginResult(
        success=True,
        username=config.username,
        session_json=new_session,
    )
