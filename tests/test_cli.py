import json

from typer.testing import CliRunner
from clinstagram import __version__
from clinstagram.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "instagram" in result.stdout.lower() or "clinstagram" in result.stdout.lower()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_agent_info():
    result = runner.invoke(app, ["agent-info"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["name"] == "clinstagram"
    assert data["version"] == __version__
    assert "commands" in data
    assert "auth status" in data["commands"]
    assert "doctor" in data["commands"]


def test_doctor_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "data" in data
    assert "checks" in data["data"]
    assert "summary" in data["data"]


def test_update_check_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")
    # Note: this will attempt to hit PyPI, which might be flaky in CI.
    # In a real codebase, we'd mock the httpx request.
    result = runner.invoke(app, ["--json", "update", "--check"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "data" in data
    assert "current_version" in data["data"]
    assert "status" in data["data"]


def test_auth_status_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")
    result = runner.invoke(app, ["--json", "auth", "status"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "data" in data
    assert "backends" in data["data"]


def test_config_show_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")
    result = runner.invoke(app, ["--json", "config", "show"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "data" in data
    assert "compliance_mode" in data["data"]


def test_config_mode_set(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINSTAGRAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLINSTAGRAM_TEST_MODE", "1")
    result = runner.invoke(app, ["config", "mode", "official-only"])
    assert result.exit_code == 0
