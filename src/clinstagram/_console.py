"""Lazily constructed rich Console: importing rich costs ~0.1 s and JSON runs never print with it."""
from __future__ import annotations

from typing import Any


class _LazyConsole:
    _console: Any = None

    def __getattr__(self, name: str) -> Any:
        if _LazyConsole._console is None:
            from rich.console import Console

            _LazyConsole._console = Console()
        return getattr(_LazyConsole._console, name)


console = _LazyConsole()
