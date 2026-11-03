from __future__ import annotations

import sys
from dataclasses import MISSING, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Plain dataclasses instead of pydantic: every CLI run parses the config, and
# importing pydantic used to cost more than the rest of startup combined.


class ComplianceMode(str, Enum):
    OFFICIAL_ONLY = "official-only"
    HYBRID_SAFE = "hybrid-safe"
    PRIVATE_ENABLED = "private-enabled"


class BackendType(str, Enum):
    AUTO = "auto"
    GRAPH_IG = "graph_ig"
    GRAPH_FB = "graph_fb"
    PRIVATE = "private"


def _coerce_like(name: str, value: Any, default: Any) -> Any:
    """Coerce value to the type of the field default (bool / int / float / str)."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "1", "yes", "on"):
            return True
        if isinstance(value, str) and value.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{name} must be a boolean, got {value!r}")
    if isinstance(default, (int, float)) and isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string, got {value!r}")
    return value


class _Model:
    """Dataclass mixin: validate on every assignment (incl. __init__), dump to dict."""

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, self._coerce(name, value))

    def _coerce(self, name: str, value: Any) -> Any:
        for f in fields(self):  # type: ignore[arg-type]
            if f.name == name and f.default is not MISSING:
                return _coerce_like(name, value, f.default)
        return value

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Any:
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in (data or {}).items() if k in names})

    def to_dict(self) -> dict:
        out: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            out[f.name] = _dump(getattr(self, f.name))
        return out

    # Back-compat with the former pydantic models.
    def model_dump(self, mode: str = "python") -> dict:
        return self.to_dict()


def _dump(v: Any) -> Any:
    if isinstance(v, _Model):
        return v.to_dict()
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _dump(x) for k, x in v.items()}
    return v


@dataclass
class RateLimits(_Model):
    graph_dm_per_hour: int = 200
    private_dm_per_hour: int = 30
    private_follows_per_day: int = 20
    private_likes_per_hour: int = 20
    # Enforced per account by clinstagram.safety (rolling windows, -1 disables a cap).
    private_comments_per_hour: int = 5
    private_posts_per_day: int = 3
    private_stories_per_day: int = 8
    private_profile_edits_per_day: int = 1  # bio, name, username, picture, account type
    private_username_change_days: int = 14  # one username change per N days (0 disables)
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    request_jitter: bool = True


@dataclass
class MediaStaging(_Model):
    provider: str = "local-only"
    cleanup_after_publish: bool = True


@dataclass
class AccountEntry(_Model):
    """Registry row for a stored account. The keychain can't list its entries, so
    this is how `account list` (and fast routing) know what exists."""

    username: Optional[str] = None
    proxy: Optional[str] = None
    backends: list = field(default_factory=list)  # configured backends, e.g. ["private"]
    ig_user_id: Optional[str] = None  # IG user for Facebook-login / system-user Graph tokens

    def _coerce(self, name: str, value: Any) -> Any:
        if name == "proxy":
            return _normalize_proxy(value)
        if name == "backends":
            return [str(b) for b in (value or [])]
        return value


def _normalize_proxy(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "none")):
        return None
    if not isinstance(value, str):
        raise ValueError(f"proxy must be a string, got {value!r}")
    return value.strip()


# Keys that `config set` is allowed to modify directly.
WRITABLE_CONFIG_KEYS = frozenset({
    "default_account", "compliance_mode", "preferred_backend", "proxy",
})


@dataclass
class GlobalConfig(_Model):
    default_account: str = "default"
    compliance_mode: ComplianceMode = ComplianceMode.HYBRID_SAFE
    preferred_backend: BackendType = BackendType.AUTO
    proxy: Optional[str] = None
    rate_limits: RateLimits = field(default_factory=RateLimits)
    media_staging: MediaStaging = field(default_factory=MediaStaging)
    accounts: dict = field(default_factory=dict)  # name -> AccountEntry

    def _coerce(self, name: str, value: Any) -> Any:
        if name == "compliance_mode":
            return ComplianceMode(value)
        if name == "preferred_backend":
            return BackendType(value)
        if name == "default_account":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("default_account must be a non-empty string")
            return value.strip()
        if name == "proxy":
            return _normalize_proxy(value)
        if name == "accounts" and isinstance(value, dict):
            return {
                str(k): (v if isinstance(v, AccountEntry) else AccountEntry.from_dict(v))
                for k, v in value.items()
            }
        if name == "rate_limits" and isinstance(value, dict):
            return RateLimits.from_dict(value)
        if name == "media_staging" and isinstance(value, dict):
            return MediaStaging.from_dict(value)
        return value


DEFAULT_CONFIG_DIR = Path.home() / ".clinstagram"


def get_config_dir(override: Optional[Path] = None) -> Path:
    return override or DEFAULT_CONFIG_DIR


def get_account_dir(account: str, config_dir: Optional[Path] = None) -> Path:
    return get_config_dir(config_dir) / "accounts" / account


def ensure_dirs(config_dir: Optional[Path] = None) -> Path:
    d = get_config_dir(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "accounts").mkdir(exist_ok=True)
    (d / "logs").mkdir(exist_ok=True)
    return d


def load_config(config_dir: Optional[Path] = None) -> GlobalConfig:
    d = get_config_dir(config_dir)
    config_path = d / "config.toml"
    if not config_path.exists():
        ensure_dirs(config_dir)
        cfg = GlobalConfig()
        save_config(cfg, config_dir)
        return cfg
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return GlobalConfig.from_dict(data)


def _strip_none(obj: object) -> object:
    """Recursively remove None values — TOML cannot serialize them."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(i) for i in obj]
    return obj


def save_config(config: GlobalConfig, config_dir: Optional[Path] = None) -> None:
    import tomli_w

    d = ensure_dirs(config_dir)
    config_path = d / "config.toml"
    data = _strip_none(config.to_dict())
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)
