from __future__ import annotations

from typing import Optional

from clinstagram.auth.keychain import SecretsStore
from clinstagram.backends.capabilities import (
    GROWTH_ACTIONS,
    READ_ONLY_FEATURES,
    Feature,
    can_backend_do,
)
from clinstagram.config import ComplianceMode

BACKEND_PREFERENCE = ["graph_ig", "graph_fb", "private"]


class Router:
    def __init__(
        self,
        account: str,
        compliance_mode: ComplianceMode,
        secrets: SecretsStore,
        available: Optional[list[str]] = None,
    ):
        self.account = account
        self.compliance_mode = compliance_mode
        self.secrets = secrets
        # Pre-resolved configured backends (from the account registry); None = ask the keychain.
        self._available = available

    def _available_backends(self) -> list[str]:
        if self._available is not None:
            return [b for b in BACKEND_PREFERENCE if b in self._available]
        return [b for b in BACKEND_PREFERENCE if self.secrets.has_backend(self.account, b)]

    def _is_allowed_by_policy(self, backend: str, feature: Feature) -> bool:
        if self.compliance_mode == ComplianceMode.OFFICIAL_ONLY:
            return backend != "private"

        if self.compliance_mode == ComplianceMode.HYBRID_SAFE:
            if backend == "private":
                if feature in GROWTH_ACTIONS:
                    return False
                return feature in READ_ONLY_FEATURES
            return True

        return True

    def route(self, feature: Feature) -> Optional[str]:
        available = self._available_backends()
        for backend in BACKEND_PREFERENCE:
            if backend not in available:
                continue
            if not can_backend_do(backend, feature):
                continue
            if not self._is_allowed_by_policy(backend, feature):
                continue
            return backend
        return None
