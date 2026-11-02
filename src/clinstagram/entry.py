"""Console-script entry point that keeps startup fast.

Typer builds every registered command before it parses argv, which made even
`clinstagram --version` take most of a second. Here we answer `--version`
without importing Typer, and register only the command group the invocation
targets. Anything unusual (help, completion, typos, agent-info) registers
everything so behaviour matches `clinstagram.cli:app` exactly.
"""
from __future__ import annotations

import sys

GROUPS = frozenset({
    "auth", "config", "profile", "post", "dm", "story", "comments", "analytics",
    "followers", "user", "like", "hashtag", "media", "skill", "account",
})
# Top-level commands that need no command group at all.
STANDALONE = frozenset({"doctor", "update", "activity"})
_GLOBAL_FLAGS = frozenset({"--json", "--dry-run", "--enable-growth-actions", "--full"})
_GLOBAL_VALUE_OPTS = frozenset({"--account", "--backend", "--proxy", "--fields"})

ALL = None  # sentinel: register every group


def target_group(argv: list[str]) -> "str | None":
    """Return the group to register, "" for none, or None for all."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_FLAGS:
            i += 1
            continue
        if tok in _GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if tok.split("=", 1)[0] in _GLOBAL_VALUE_OPTS and "=" in tok:
            i += 1
            continue
        if tok.startswith("-"):
            return ALL  # --help, --version, completion flags, unknown options
        if tok in GROUPS:
            return tok
        if tok in STANDALONE:
            return ""
        return ALL  # agent-info, typos ("did you mean"), anything else
    return ALL


def run() -> None:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        from clinstagram import __version__

        sys.stdout.write(f"clinstagram {__version__}\n")
        return
    import clinstagram

    clinstagram._only_group = target_group(argv)
    from clinstagram.cli import app

    app()
