"""Video uploads get an ffmpeg thumbnail; instagrapi prints never reach JSON stdout."""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from clinstagram.backends import private as priv
from clinstagram.backends.private import PrivateBackend, PrivateAPIError, ffmpeg_thumbnail
from clinstagram.cli import app

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def tiny_mp4(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=2",
         "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


def _media():
    m = MagicMock()
    m.pk, m.id, m.code, m.media_type, m.caption_text = 1, "1_2", "abc", 2, "c"
    m.user = None
    return m


def test_ffmpeg_thumbnail_extracts_a_frame(tiny_mp4, tmp_path):
    out = ffmpeg_thumbnail(tiny_mp4, tmp_path / "t.jpg")
    assert out.stat().st_size > 0


def test_ffmpeg_missing_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(priv.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg was not found"):
        ffmpeg_thumbnail(tmp_path / "v.mp4", tmp_path / "t.jpg")


@pytest.mark.parametrize("method,client_call", [
    ("post_reel", "clip_upload"),
    ("post_video", "video_upload"),
])
def test_video_posts_pass_temp_thumbnail_and_clean_up(tiny_mp4, method, client_call):
    cl = MagicMock()
    seen = {}

    def upload(**kw):
        seen["thumb"] = kw["thumbnail"]
        assert kw["thumbnail"].exists() and kw["thumbnail"].stat().st_size > 0
        return _media()

    getattr(cl, client_call).side_effect = upload
    getattr(PrivateBackend(client=cl), method)(str(tiny_mp4), caption="hi")
    assert not seen["thumb"].exists(), "temp thumbnail must be removed after upload"


def test_story_video_passes_thumbnail(tiny_mp4):
    cl = MagicMock()
    story = MagicMock(pk=5, id="5_1", media_type=2, thumbnail_url=None, video_url=None, taken_at=None)
    cl.video_upload_to_story.return_value = story
    PrivateBackend(client=cl).story_post_video(str(tiny_mp4))
    assert "thumbnail" in cl.video_upload_to_story.call_args.kwargs


def test_explicit_thumbnail_is_used_untouched(tmp_path):
    thumb = tmp_path / "mine.jpg"
    thumb.write_bytes(b"x")
    cl = MagicMock()
    cl.clip_upload.return_value = _media()
    PrivateBackend(client=cl).post_reel(str(tmp_path / "v.mp4"), thumbnail=str(thumb))
    assert cl.clip_upload.call_args.kwargs["thumbnail"] == thumb
    assert thumb.exists()


def test_reel_without_ffmpeg_fails_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(priv.shutil, "which", lambda name: None)
    cl = MagicMock()
    with pytest.raises(PrivateAPIError, match="ffmpeg"):
        PrivateBackend(client=cl).post_reel(str(tmp_path / "v.mp4"))
    cl.clip_upload.assert_not_called()


def test_carousel_video_thumbnails_route_through_ffmpeg_and_are_removed(tiny_mp4):
    import instagrapi.utils.video as igvideo

    cl = MagicMock()

    def album_upload(paths, caption=""):
        # what instagrapi does internally for a video item without a thumbnail
        igvideo.generate_video_thumbnail(paths[0], Path(f"{paths[0]}.jpg"))
        assert Path(f"{paths[0]}.jpg").stat().st_size > 0
        return _media()

    cl.album_upload.side_effect = album_upload
    PrivateBackend(client=cl).post_carousel([str(tiny_mp4)], caption="c")
    assert getattr(igvideo.generate_video_thumbnail, "_clinstagram", False)
    assert not Path(f"{tiny_mp4}.jpg").exists()


def test_backend_prints_do_not_corrupt_json_stdout(monkeypatch, tmp_path):
    from clinstagram.auth.keychain import SecretsStore

    secrets = SecretsStore(backend="memory")
    secrets.set("default", "private_session", "{}")
    backend = MagicMock()
    backend.name = "private"

    def noisy(*a, **k):
        print('Analyzing CLIP file "x.mp4"')
        return {"name": "nasa"}

    backend.user_info.side_effect = noisy
    monkeypatch.setattr("clinstagram.commands._dispatch._get_secrets", lambda ctx: secrets)
    monkeypatch.setattr("clinstagram.commands._dispatch._instantiate_backend", lambda ctx, n, f=None: backend)
    runner = CliRunner()
    result = runner.invoke(app, ["--json", "user", "info", "nasa"],
                           env={"CLINSTAGRAM_CONFIG_DIR": str(tmp_path), "CLINSTAGRAM_TEST_MODE": "1"})
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["name"] == "nasa"
    assert "Analyzing CLIP" in result.stderr
