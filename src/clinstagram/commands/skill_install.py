"""Install clinstagram skills for AI coding agents (Claude Code, Gemini CLI, Cursor, etc.)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer

from clinstagram.commands._dispatch import make_subgroup, output_success

skill_app = make_subgroup("Install AI agent skills")

# Agent → skills directory mapping
_AGENT_DIRS = {
    "claude": Path.home() / ".claude" / "skills",
    "gemini": Path.home() / ".gemini" / "skills",
    "cursor": Path.cwd() / ".cursor" / "skills",
}


def _find_skill_source() -> Path | None:
    """SKILL.md: packaged into the wheel as clinstagram/SKILL.md, or at the repo root in development."""
    import importlib.resources
    try:
        ref = importlib.resources.files("clinstagram") / "SKILL.md"
        if ref.is_file():  # type: ignore[union-attr]
            return Path(str(ref))
    except Exception:
        pass
    skill_md = Path(__file__).resolve().parents[3] / "SKILL.md"
    if skill_md.exists():
        return skill_md
    return None


def _detect_agents() -> list[str]:
    """Detect which AI coding agents are installed by checking for their config directories."""
    found = []
    for name, path in _AGENT_DIRS.items():
        # Check parent directory exists (e.g., ~/.claude/ exists)
        if path.parent.exists():
            found.append(name)
    return found


def _install_for_agent(agent: str, source: Path, force: bool = False) -> dict:
    """Install as <skills>/clinstagram/SKILL.md, the folder layout agents load skills from."""
    target_dir = _AGENT_DIRS[agent] / "clinstagram"
    target_file = target_dir / "SKILL.md"
    legacy = _AGENT_DIRS[agent] / "clinstagram.md"  # flat file older versions wrote; agents ignore it

    if target_file.exists() and not force and target_file.read_bytes() == source.read_bytes():
        return {"agent": agent, "status": "already_installed", "path": str(target_file)}
    if target_file.exists() and not force:
        return {"agent": agent, "status": "outdated", "path": str(target_file),
                "hint": "Run: clinstagram skill install-skill --force"}

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target_file)
    if legacy.exists():
        legacy.unlink()
    return {"agent": agent, "status": "installed", "path": str(target_file)}


@skill_app.command("install-skill")
def install_skill(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(
        None, "--agent", "-a",
        help="Target agent: claude, gemini, cursor, or 'all' (auto-detect if omitted)",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing skill files"),
):
    """Install clinstagram skill for AI coding agents.

    Auto-detects installed agents (Claude Code, Gemini CLI, Cursor) and copies
    the skill definition so agents can use clinstagram commands natively.
    """
    source = _find_skill_source()
    if not source:
        typer.echo("Error: Could not find skill file (clinstagram.md or SKILL.md)", err=True)
        raise typer.Exit(1)

    if agent and agent != "all":
        if agent not in _AGENT_DIRS:
            typer.echo(f"Error: Unknown agent '{agent}'. Choose: {', '.join(_AGENT_DIRS)}", err=True)
            raise typer.Exit(1)
        agents = [agent]
    elif agent == "all":
        agents = list(_AGENT_DIRS.keys())
    else:
        agents = _detect_agents()
        if not agents:
            typer.echo("No AI coding agents detected. Use --agent to specify one.", err=True)
            raise typer.Exit(1)

    results = []
    for a in agents:
        result = _install_for_agent(a, source, force=force)
        results.append(result)
        if not ctx.obj.get("json"):
            status = result["status"]
            icon = "+" if status == "installed" else "="
            typer.echo(f"  [{icon}] {a}: {result['path']} ({status})")

    if ctx.obj.get("json"):
        output_success(ctx, {"skills_installed": results, "source": str(source)})
