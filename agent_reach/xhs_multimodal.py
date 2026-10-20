"""Image/video enrichment for XiaoHongShu notes.

OpenCLI's note output carries no media URLs, so callers pass either a note
JSON that already has them or local files downloaded through the browser
session.  OCR shells out to the optional ``tesseract`` binary; video
transcription reuses Agent Reach's Whisper routing.
"""

from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests

from agent_reach.transcribe import TranscribeError, transcribe
from agent_reach.utils.url import domain_matches, normalize_public_http_url

MAX_MEDIA_BYTES = 25 * 1024 * 1024
MEDIA_HOSTS = ("xiaohongshu.com", "xhscdn.com", "xhscdn.net")

# CDNs want a browser UA and referer; redirects are followed by hand so every
# hop is re-checked against the allowlist.
_MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
}
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 3
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv", ".mp3", ".m4a"})


class XhsMultimodalError(RuntimeError):
    """Raised when one media item cannot be enriched."""


# One failed item must never abort the note, so these stay per-item.
_MEDIA_ERRORS = (
    XhsMultimodalError,
    TranscribeError,
    requests.RequestException,
    OSError,
    ValueError,
)


def _checked_media_url(url: str) -> str:
    """Return the normalized URL, or raise if it is not an XHS CDN target."""
    try:
        normalized = normalize_public_http_url(url)
    except ValueError as exc:
        raise XhsMultimodalError("media URL must be a public HTTP(S) URL") from exc
    host = urlparse(normalized).hostname or ""
    if not domain_matches(host, *MEDIA_HOSTS):
        raise XhsMultimodalError("media URL host is not a XiaoHongShu CDN host")
    return normalized


def _open_media(url: str) -> requests.Response:
    """GET an allowlisted media URL, re-checking every redirect target."""
    current = _checked_media_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        response = requests.get(
            current,
            stream=True,
            timeout=(10, 30),
            allow_redirects=False,
            headers=_MEDIA_HEADERS,
        )
        if response.status_code in _REDIRECT_CODES:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise XhsMultimodalError("media URL redirected without a location")
            current = _checked_media_url(urljoin(current, location))
            continue
        response.raise_for_status()
        return response
    raise XhsMultimodalError("media URL redirected too many times")


def _too_large() -> XhsMultimodalError:
    limit = MAX_MEDIA_BYTES // (1024 * 1024)
    return XhsMultimodalError(f"media file exceeds {limit} MB limit")


def _download(url: str, directory: Path, index: int = 0) -> Path:
    """Download one media file, capped at MAX_MEDIA_BYTES."""
    with _open_media(url) as response:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_MEDIA_BYTES:
            raise _too_large()
        suffix = Path(urlparse(url).path).suffix.lower() or ".bin"
        target = directory / f"media-{index}{suffix}"
        size = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    target.unlink(missing_ok=True)
                    raise _too_large()
                handle.write(chunk)
    return target


def _first_value(value: Any) -> str | None:
    """Return the first HTTP(S) string found in a nested payload."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("url", "url_default", "original", "play_url", "video_url"):
            found = _first_value(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = _first_value(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_value(item)
            if found:
                return found
    return None


def extract_media(note: dict[str, Any]) -> tuple[list[str], str | None]:
    """Extract image URLs and one video URL from common note shapes."""
    inner = note.get("note_card") or note.get("note") or note
    if not isinstance(inner, dict):
        return [], None
    images: list[str] = []
    for key in ("images", "image_list", "images_list"):
        value = inner.get(key)
        if isinstance(value, list):
            for item in value:
                url = _first_value(item)
                if url and url not in images:
                    images.append(url)
    video = None
    for key in ("video_url", "video", "video_info", "video_data"):
        video = _first_value(inner.get(key))
        if video:
            break
    return images, video


def normalize_note_payload(payload: Any) -> dict[str, Any]:
    """Convert OpenCLI's ``[{field, value}, ...]`` output to a note object."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        fields = {str(item["field"]): item.get("value") for item in payload if "field" in item}
        if fields:
            return fields
    raise XhsMultimodalError("OpenCLI output is not a single note object")


def _ocr_image(path: Path, language: str) -> str:
    """Run local Tesseract on one file and return its text."""
    try:
        process = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", language],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise XhsMultimodalError("tesseract not found; install it to enable image OCR") from exc
    except subprocess.TimeoutExpired as exc:
        raise XhsMultimodalError("tesseract timed out") from exc
    if process.returncode:
        detail = (process.stderr or "").strip()[:240] or "unknown error"
        raise XhsMultimodalError(f"tesseract failed: {detail}")
    return process.stdout.strip()


def _split_local_media(files: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Split downloaded files into OCR images and transcribable videos."""
    images: list[Path] = []
    videos: list[Path] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            images.append(path)
        elif suffix in _VIDEO_SUFFIXES:
            videos.append(path)
    return images, videos


def enrich_note(
    note: dict[str, Any],
    *,
    ocr: bool = True,
    transcribe_video: bool = True,
    language: str = "chi_sim+eng",
    transcriber: Callable[[str], str] = transcribe,
    media_files: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Return a copy of *note* with OCR text and the video transcript attached.

    Items fail independently: each error lands next to its own item so the note
    and the successful items stay usable.
    """
    result = copy.deepcopy(note)
    images, video = extract_media(result)
    local_images, local_videos = _split_local_media(media_files or ())
    multimodal: dict[str, Any] = {"images": [], "video": None}
    with tempfile.TemporaryDirectory(prefix="agent-reach-xhs-") as temp:
        directory = Path(temp)
        if ocr:
            for index, url in enumerate(images):
                item: dict[str, Any] = {"index": index, "url": url}
                try:
                    item["text"] = _ocr_image(_download(url, directory, index), language)
                except _MEDIA_ERRORS as exc:
                    item["error"] = str(exc)
                multimodal["images"].append(item)
            for path in local_images:
                item = {"index": len(multimodal["images"]), "file": path.name}
                try:
                    item["text"] = _ocr_image(path, language)
                except _MEDIA_ERRORS as exc:
                    item["error"] = str(exc)
                multimodal["images"].append(item)
        video_source = video or (str(local_videos[0]) if local_videos else None)
        if video_source and transcribe_video:
            label = "url" if video else "file"
            video_item: dict[str, Any] = {label: video or Path(video_source).name}
            try:
                video_item["transcript"] = transcriber(video_source)
            except _MEDIA_ERRORS as exc:
                video_item["error"] = str(exc)
            multimodal["video"] = video_item
    result["multimodal"] = multimodal
    return result


__all__ = [
    "MAX_MEDIA_BYTES",
    "MEDIA_HOSTS",
    "XhsMultimodalError",
    "enrich_note",
    "extract_media",
    "normalize_note_payload",
]
