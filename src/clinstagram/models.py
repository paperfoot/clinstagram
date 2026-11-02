from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Optional

# Plain dataclasses + json instead of pydantic: the envelope is built on every
# run and pydantic's import cost dominated CLI startup.


class ExitCode(IntEnum):
    SUCCESS = 0
    USER_ERROR = 1
    AUTH_ERROR = 2
    RATE_LIMITED = 3
    API_ERROR = 4
    CHALLENGE_REQUIRED = 5
    POLICY_BLOCKED = 6
    CAPABILITY_UNAVAILABLE = 7


def _json_default(obj: Any) -> Any:
    """Serialize what backends hand back: datetimes, enums, paths, URLs, models."""
    if isinstance(obj, Enum):
        return obj.value
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        return iso()
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return str(obj)


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default)


@dataclass
class CLIResponse:
    exit_code: ExitCode = ExitCode.SUCCESS
    data: Any = None
    backend_used: Optional[str] = None

    def to_json(self) -> str:
        return dumps({"exit_code": int(self.exit_code), "data": self.data, "backend_used": self.backend_used})


@dataclass
class CLIError:
    exit_code: ExitCode
    error: str
    remediation: Optional[str] = None
    retry_after: Optional[int] = None
    challenge_type: Optional[str] = None
    challenge_url: Optional[str] = None

    def to_json(self) -> str:
        payload = {
            "exit_code": int(self.exit_code),
            "error": self.error,
            "remediation": self.remediation,
            "retry_after": self.retry_after,
            "challenge_type": self.challenge_type,
            "challenge_url": self.challenge_url,
        }
        return dumps({k: v for k, v in payload.items() if v is not None})
