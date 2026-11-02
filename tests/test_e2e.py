import json

from typer.testing import CliRunner

from clinstagram import __version__
from clinstagram.cli import app

runner = CliRunner()


def test_full_workflow(tmp_path, monkeypatch):
    """Simulate: set config -> check status -> set mode -> verify."""
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")

    # Check initial config
    result = runner.invoke(app, ["--json", "config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["data"]["compliance_mode"] == "hybrid-safe"

    # Set mode
    result = runner.invoke(app, ["config", "mode", "official-only"])
    assert result.exit_code == 0

    # Verify persisted
    result = runner.invoke(app, ["--json", "config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["data"]["compliance_mode"] == "official-only"

    # Auth status (no backends configured)
    result = runner.invoke(app, ["--json", "auth", "status"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["data"]["backends"]["graph_ig"]["configured"] is False
    assert data["data"]["backends"]["graph_fb"]["configured"] is False
    assert data["data"]["backends"]["private"]["configured"] is False


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_placeholder_commands():
    """All command groups show help, not crash."""
    for group in ["post", "dm", "story", "comments", "analytics", "followers", "user", "like", "hashtag"]:
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0
