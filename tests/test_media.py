"""Tests for the media staging layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clinstagram.media import _is_url, cleanup_temp_files, resolve_media, _temp_files


class TestIsUrl:
    def test_http(self):
        assert _is_url("http://example.com/photo.jpg") is True

    def test_https(self):
        assert _is_url("https://cdn.instagram.com/v/image.png") is True

    def test_local_path(self):
        assert _is_url("/Users/me/photos/image.jpg") is False

    def test_relative_path(self):
        assert _is_url("./photo.jpg") is False

    def test_empty(self):
        assert _is_url("") is False


class TestResolveMediaUrlNeedsUrl:
    def test_url_passthrough(self):
        url = "https://cdn.example.com/media/photo.jpg"
        result = resolve_media(url, needs_url=True)
        assert result == url

    def test_url_with_query_params(self):
        url = "https://cdn.example.com/media/photo.jpg?token=abc&size=large"
        result = resolve_media(url, needs_url=True)
        assert result == url


def _mock_stream(data: bytes, headers: dict | None = None):
    """Create a mock for httpx.stream() context manager that yields data chunks."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = headers or {}
    mock_response.iter_bytes = MagicMock(return_value=iter([data]))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_response)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


class TestResolveMediaUrlNeedsPath:
    @patch("httpx.stream")
    def test_url_downloaded_to_temp(self, mock_stream):
        mock_stream.return_value = _mock_stream(b"fake image data")

        result = resolve_media("https://example.com/photo.jpg", needs_url=False)

        assert Path(result).suffix == ".jpg"
        assert Path(result).exists()
        assert Path(result).read_bytes() == b"fake image data"
        mock_stream.assert_called_once_with("GET", "https://example.com/photo.jpg", follow_redirects=True, timeout=30.0)

        # Cleanup
        Path(result).unlink(missing_ok=True)

    @patch("httpx.stream")
    def test_url_infers_extension(self, mock_stream):
        mock_stream.return_value = _mock_stream(b"fake video data")

        result = resolve_media("https://example.com/clip.mp4", needs_url=False)

        assert Path(result).suffix == ".mp4"
        Path(result).unlink(missing_ok=True)

    @patch("httpx.stream")
    def test_url_default_extension(self, mock_stream):
        mock_stream.return_value = _mock_stream(b"data")

        result = resolve_media("https://example.com/media", needs_url=False)

        assert Path(result).suffix == ".jpg"
        Path(result).unlink(missing_ok=True)


class TestResolveMediaLocalPath:
    def test_local_path_passthrough(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"image data")

        result = resolve_media(str(img), needs_url=False)
        assert result == str(img)

    def test_local_path_not_found(self):
        with pytest.raises(FileNotFoundError, match="Media file not found"):
            resolve_media("/nonexistent/path/photo.jpg", needs_url=False)

    def test_local_path_needs_url_raises(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"image data")

        with pytest.raises(ValueError, match="Graph API requires a public URL"):
            resolve_media(str(img), needs_url=True)


class TestCleanupTempFiles:
    @patch("httpx.stream")
    def test_cleanup_removes_files(self, mock_stream):
        mock_stream.return_value = _mock_stream(b"data")

        result = resolve_media("https://example.com/photo.jpg", needs_url=False)
        temp_path = Path(result)
        assert temp_path.exists()

        cleanup_temp_files()

        assert not temp_path.exists()
        assert len(_temp_files) == 0

    def test_cleanup_on_empty_is_safe(self):
        # Should not raise even when no temp files exist
        _temp_files.clear()
        cleanup_temp_files()
        assert len(_temp_files) == 0
