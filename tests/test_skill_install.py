from pathlib import Path

from clinstagram.commands import skill_install


def test_installs_skill_folder_and_removes_legacy_flat_file(tmp_path, monkeypatch):
    monkeypatch.setitem(skill_install._AGENT_DIRS, "claude", tmp_path)
    (tmp_path / "clinstagram.md").write_text("old")
    src = skill_install._find_skill_source()
    assert src is not None and src.name == "SKILL.md"

    out = skill_install._install_for_agent("claude", src)
    assert out["status"] == "installed"
    assert (tmp_path / "clinstagram" / "SKILL.md").read_bytes() == src.read_bytes()
    assert not (tmp_path / "clinstagram.md").exists()
    assert skill_install._install_for_agent("claude", src)["status"] == "already_installed"


def test_outdated_copy_is_reported_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setitem(skill_install._AGENT_DIRS, "claude", tmp_path)
    (tmp_path / "clinstagram").mkdir()
    (tmp_path / "clinstagram" / "SKILL.md").write_text("stale")
    out = skill_install._install_for_agent("claude", skill_install._find_skill_source())
    assert out["status"] == "outdated" and "--force" in out["hint"]
