"""Compact-by-default output, --full and --fields."""
import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from clinstagram.cli import app
from clinstagram.commands._dispatch import compact_data, parse_fields, project_data

CDN = "https://instagram.flhr2-3.fna.fbcdn.net/v/t51/abc.jpg?stp=x&oh=y"

MEDIA = {
    "id": "1", "code": "Abc", "caption": "senolytics in mice", "like_count": 3,
    "thumbnail_url": CDN, "video_url": CDN, "location": None,
    "user": {"id": "9", "username": "lab", "profile_picture_url": CDN, "biography": None},
    "resources": [{"id": "2", "thumbnail_url": CDN, "media_type": 1}],
    "permalink": "https://www.instagram.com/p/Abc/",
}


def test_compact_strips_cdn_and_nulls_recursively_but_keeps_permalinks():
    out = compact_data([MEDIA])[0]
    assert out == {
        "id": "1", "code": "Abc", "caption": "senolytics in mice", "like_count": 3,
        "user": {"id": "9", "username": "lab"},
        "resources": [{"id": "2", "media_type": 1}],
        "permalink": "https://www.instagram.com/p/Abc/",
    }


def test_unknown_key_holding_cdn_url_is_dropped_too():
    assert compact_data({"weird_pic": CDN, "site": "https://agingresearch.news"}) == {
        "site": "https://agingresearch.news"
    }


def test_local_paths_survive_compaction():
    dl = {"media_id": "1", "files": ["/tmp/a.mp4"], "output_dir": "/tmp"}
    assert compact_data(dl) == dl


def test_project_dotted_and_through_lists():
    paths = parse_fields("code, user.username,resources.id")
    assert project_data([MEDIA], paths) == [
        {"code": "Abc", "user": {"username": "lab"}, "resources": [{"id": "2"}]}
    ]


def _invoke(monkeypatch, tmp_path, args, payload):
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    backend = MagicMock()
    backend.name = "private"
    backend.hashtag_top.return_value = payload
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend", lambda ctx, n, f=None: backend)
    return CliRunner().invoke(app, ["--json", *args, "hashtag", "top", "x"],
                              env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"})


def test_cli_default_is_compact(monkeypatch, tmp_path):
    r = _invoke(monkeypatch, tmp_path, [], [MEDIA])
    item = json.loads(r.stdout)["data"][0]
    assert "thumbnail_url" not in item and "location" not in item and item["code"] == "Abc"


def test_cli_full_keeps_everything(monkeypatch, tmp_path):
    r = _invoke(monkeypatch, tmp_path, ["--full"], [MEDIA])
    assert json.loads(r.stdout)["data"][0] == MEDIA


def test_cli_fields_projects_at_full_fidelity(monkeypatch, tmp_path):
    r = _invoke(monkeypatch, tmp_path, ["--fields", "code,thumbnail_url"], [MEDIA])
    assert json.loads(r.stdout)["data"] == [{"code": "Abc", "thumbnail_url": CDN}]


def test_cli_unknown_field_is_one_clean_user_error(monkeypatch, tmp_path):
    r = _invoke(monkeypatch, tmp_path, ["--fields", "nope"], [MEDIA])
    assert r.exit_code == 1
    lines = [line for line in r.output.splitlines() if line.strip().startswith("{")]
    assert len(lines) == 1, r.output
    err = json.loads(lines[0])
    assert "nope" in err["error"] and "code" in err["remediation"]


def test_agent_info_documents_flags_and_exit_codes():
    data = json.loads(CliRunner().invoke(app, ["agent-info"]).stdout)
    assert {"--full", "--fields"} <= set(data["global_flags"])
    assert data["exit_codes"]["6"].startswith("POLICY_BLOCKED")
