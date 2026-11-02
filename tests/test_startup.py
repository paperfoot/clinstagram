"""Fast-startup plumbing: entry routing, pydantic-free config and envelope."""
import json
import subprocess
import sys

import pytest

from clinstagram.entry import target_group


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--json", "user", "info", "nasa"], "user"),
        (["--account", "news", "--json", "dm", "inbox"], "dm"),
        (["--account=news", "post", "photo", "x.jpg"], "post"),
        (["--fields", "id,code", "--full", "hashtag", "top", "x"], "hashtag"),
        (["--json", "doctor"], ""),
        (["activity"], ""),
        (["agent-info"], None),
        (["--help"], None),
        ([], None),
        (["usr", "info"], None),  # typo -> full app so Typer can suggest
        (["--install-completion"], None),
    ],
)
def test_target_group(argv, expected):
    assert target_group(argv) == expected


def test_version_fast_path_skips_typer():
    code = (
        "import sys; sys.argv=['clinstagram','--version'];"
        "from clinstagram.entry import run; run();"
        "print('typer' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
    assert out.splitlines()[-1] == "False"
    assert out.startswith("clinstagram ")


def test_cli_import_avoids_heavy_modules():
    code = (
        "import sys, clinstagram.cli;"
        "print(sorted(m for m in ('pydantic','httpx','rich','instagrapi','keyring') if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
    assert out.strip() == "[]"


def test_config_validates_assignment_and_ignores_unknown_keys():
    from clinstagram.config import ComplianceMode, GlobalConfig

    cfg = GlobalConfig.from_dict({"compliance_mode": "official-only", "bogus": 1,
                                  "rate_limits": {"private_dm_per_hour": "5", "nope": 2}})
    assert cfg.compliance_mode is ComplianceMode.OFFICIAL_ONLY
    assert cfg.rate_limits.private_dm_per_hour == 5
    with pytest.raises(ValueError):
        cfg.compliance_mode = "wide-open"
    with pytest.raises(ValueError):
        cfg.default_account = ""
    cfg.proxy = "none"
    assert cfg.proxy is None


def test_envelope_json_is_compact_utf8_and_serializes_odd_types():
    from datetime import datetime
    from pathlib import Path

    from clinstagram.models import CLIError, CLIResponse, ExitCode

    r = CLIResponse(data={"t": datetime(2026, 1, 2, 3, 4, 5), "p": Path("/x"), "s": "naïve 🧬"})
    text = r.to_json()
    assert '"s":"naïve 🧬"' in text and " " not in text.replace("naïve 🧬", "")
    assert json.loads(text)["data"]["t"] == "2026-01-02T03:04:05"
    e = json.loads(CLIError(exit_code=ExitCode.API_ERROR, error="x").to_json())
    assert e == {"exit_code": 4, "error": "x"}
