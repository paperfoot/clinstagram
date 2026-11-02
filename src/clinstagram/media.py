from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse


_temp_files: list[Path] = []

# Max download size: 100 MB (Instagram's limits are ~100 MB for video)
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


def is_url(source: str) -> bool:
    """Return True if source looks like an HTTP(S) URL."""
    return source.startswith("http://") or source.startswith("https://")


def _is_url(source: str) -> bool:
    """Backward-compatible alias for older tests/imports."""
    return is_url(source)


def resolve_media(source: str, needs_url: bool) -> str:
    """
    Resolve a media source for the appropriate backend.

    Parameters
    ----------
    source : str
        Either a local file path or an HTTP(S) URL.
    needs_url : bool
        True if the backend needs a public URL (Graph API).
        False if the backend needs a local file path (Private API).

    Returns
    -------
    str
        The resolved media path or URL.

    Raises
    ------
    FileNotFoundError
        If source is a local path but the file does not exist.
    ValueError
        If a local path is given but the backend requires a URL.
    httpx.HTTPStatusError
        If downloading a URL fails.
    """
    if is_url(source):
        if needs_url:
            return source

        # Download to a temp file for the private backend using streaming
        # to avoid buffering 100MB+ videos entirely in memory.
        parsed = urlparse(source)
        suffix = Path(parsed.path).suffix or ".jpg"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        downloaded = 0
        try:
            import httpx

            with httpx.stream("GET", source, follow_redirects=True, timeout=30.0) as response:
                response.raise_for_status()
                # Early rejection via Content-Length if available
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"Media too large: {content_length} bytes (max {MAX_DOWNLOAD_BYTES})")
                for chunk in response.iter_bytes(chunk_size=65536):
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"Media too large: >{downloaded} bytes (max {MAX_DOWNLOAD_BYTES})")
                    tmp.write(chunk)
        except Exception:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise
        tmp.close()
        path = Path(tmp.name)
        _temp_files.append(path)
        return str(path)

    # Local path
    local = Path(source)
    if not local.exists():
        raise FileNotFoundError(f"Media file not found: {source}")

    if needs_url:
        raise ValueError(
            "Graph API requires a public URL. "
            "Upload your media first or use --backend private"
        )

    return str(local)


def cleanup_temp_files() -> None:
    """Remove any temporary files created during media staging."""
    for path in _temp_files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    _temp_files.clear()
