"""
YouTube Live Stream URL Validator and Compliant Resolver.

Provides:
1. Strict, compliant URL validation for YouTube Live streams.
2. Extraction of authorized, publicly available live HLS (.m3u8) streams using yt-dlp.
3. Fallback and clear, actionable guidance if the stream is offline, private, or protected.
4. Deterministic test mocking support for automated offline test suites.
"""

import re
import os
import sys
import io
import contextlib
import warnings
import urllib.parse
from typing import Tuple, Optional, Dict, Any
from backend.app.core.logging import logger

warnings.filterwarnings("ignore")

# Regex patterns for supported YouTube URL formats
YOUTUBE_WATCH_REGEX = re.compile(
    r'^(https?://)?(www\.|m\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/live/|youtube\.com/embed/)([a-zA-Z0-9_-]{6,15})'
)
DIRECT_HLS_REGEX = re.compile(
    r'^https?://.*\.m3u8(\?.*)?$', re.IGNORECASE
)

# Registry for test mock resolvers (for deterministic automated test harnesses)
_MOCK_YOUTUBE_STREAMS: Dict[str, str] = {}


class _SilentYtdlLogger:
    """Silences all standard output from yt-dlp to avoid broken pipe / dead TTY crashes."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg):
        logger.warning(f"[yt-dlp] {msg}")


def register_mock_youtube_stream(url_or_id: str, resolved_target: str):
    """
    Registers a mock mapping for automated tests, ensuring deterministic test runs
    without live internet or fragile third-party dependencies.
    """
    _MOCK_YOUTUBE_STREAMS[url_or_id.strip()] = resolved_target


def clear_mock_youtube_streams():
    """Clears all registered test mocks."""
    _MOCK_YOUTUBE_STREAMS.clear()


def is_youtube_url(url: str) -> bool:
    """Checks if a given string is a candidate YouTube or YouTube Live URL."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if u.startswith("mock://youtube.com/"):
        return True
    return bool(YOUTUBE_WATCH_REGEX.search(u))


def validate_youtube_url(url: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates a YouTube URL and extracts the canonical video identifier.
    Returns:
        (is_valid: bool, video_id: Optional[str], error_message: Optional[str])
    """
    if not url or not isinstance(url, str):
        return False, None, "URL cannot be empty."

    url = url.strip()

    # Support mock test URLs
    if url.startswith("mock://youtube.com/"):
        parts = url.split("/")
        vid_id = parts[-1] if parts else "mock_stream"
        return True, vid_id, None

    # Check for direct HLS playlist if user passed direct live manifest
    if DIRECT_HLS_REGEX.match(url):
        return True, "direct_hls", None

    match = YOUTUBE_WATCH_REGEX.search(url)
    if not match:
        return False, None, (
            "Invalid YouTube Live URL format. "
            "Please provide a standard link like https://www.youtube.com/watch?v=... or https://youtube.com/live/..."
        )

    video_id = match.group(4)
    if not video_id or len(video_id) < 6:
        return False, None, "Could not extract a valid YouTube video ID from the provided URL."

    return True, video_id, None

def extract_youtube_video_id(url: str) -> Optional[str]:
    is_valid, video_id, _ = validate_youtube_url(url)
    return video_id if is_valid else None


def resolve_youtube_live_stream(
    url: str,
    timeout: float = 15.0
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Compliantly extracts the direct live HLS stream URL for OpenCV / FFmpeg ingestion.
    Does NOT bypass authentication, DRM, or access controls.
    
    Returns:
        (success: bool, stream_url: Optional[str], error_message: Optional[str])
    """
    is_valid, video_id, val_err = validate_youtube_url(url)
    if not is_valid:
        return False, None, val_err

    # Check test mocks first (for automated offline test suites)
    clean_url = url.strip()
    if clean_url in _MOCK_YOUTUBE_STREAMS:
        mock_target = _MOCK_YOUTUBE_STREAMS[clean_url]
        logger.info(f"[YouTubeResolver] Resolved mock stream: {clean_url} -> {mock_target}")
        return True, mock_target, None

    if video_id and video_id in _MOCK_YOUTUBE_STREAMS:
        mock_target = _MOCK_YOUTUBE_STREAMS[video_id]
        logger.info(f"[YouTubeResolver] Resolved mock stream for ID {video_id} -> {mock_target}")
        return True, mock_target, None

    # If it's already a direct HLS manifest
    if DIRECT_HLS_REGEX.match(clean_url):
        return True, clean_url, None

    # Use yt-dlp to obtain authorized public manifest
    try:
        import yt_dlp
    except ImportError:
        logger.error("[YouTubeResolver] yt-dlp library is not installed in the environment.")
        return False, None, (
            "The YouTube stream resolution engine is currently unavailable (yt-dlp missing). "
            "Please connect the original RTSP or HLS stream directly."
        )

    ydl_opts = {
        'format': 'best[height<=720][protocol^=m3u8]/bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'logger': _SilentYtdlLogger(),
        'socket_timeout': max(timeout, 15.0),
        'extract_flat': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        }
    }

    # Prepare canonical target for best extraction compatibility
    target_urls = []
    if video_id and video_id not in ("direct_hls", "mock_stream"):
        target_urls.append(f"https://www.youtube.com/watch?v={video_id}")
    if clean_url not in target_urls:
        target_urls.append(clean_url)

    last_error: Optional[str] = None

    for target_url in target_urls:
        try:
            logger.info(f"[YouTubeResolver] Resolving live HLS stream for: {target_url}")
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(target_url, download=False)

            if not info:
                last_error = "Could not retrieve stream metadata from YouTube."
                continue

            stream_url = info.get("url")
            if not stream_url:
                formats = info.get("formats", [])
                for f in reversed(formats):
                    f_url = f.get("url", "")
                    if ".m3u8" in f_url or f.get("protocol", "").startswith("m3u8"):
                        stream_url = f_url
                        break
                if not stream_url and formats:
                    stream_url = formats[-1].get("url")

            if stream_url:
                logger.info(f"[YouTubeResolver] Successfully obtained live stream manifest: {stream_url[:60]}...")
                return True, stream_url, None
            else:
                last_error = (
                    "No compatible live video playback stream was found for this YouTube link. "
                    "Please verify the stream is currently broadcasting live, or connect via RTSP."
                )

        except yt_dlp.utils.DownloadError as de:
            err_str = str(de).lower()
            logger.warning(f"[YouTubeResolver] DownloadError resolving {target_url}: {de}")
            if "private" in err_str:
                reason = "This YouTube video is marked Private or requires authentication."
            elif "members-only" in err_str or "membership" in err_str:
                reason = "This stream is restricted to channel members."
            elif "sign in" in err_str or "login" in err_str:
                reason = "This YouTube stream requires account sign-in."
            elif "age" in err_str:
                reason = "This stream has age-gate restrictions."
            elif "offline" in err_str or "ended" in err_str or "not live" in err_str:
                reason = "The live broadcast has ended or is currently offline."
            else:
                reason = f"Platform response: {str(de).split(':')[-1].strip()}."
            last_error = f"Unable to access YouTube Live stream: {reason} Please connect the camera via direct RTSP or HLS stream."

        except (OSError, IOError) as ioe:
            logger.warning(f"[YouTubeResolver] I/O error during extraction ({ioe}), continuing...")
            last_error = f"Network or I/O interruption during stream extraction: {ioe}"

        except Exception as e:
            logger.error(f"[YouTubeResolver] Unexpected exception resolving {target_url}: {e}")
            last_error = f"An error occurred while resolving the YouTube live stream ({e})."

    return False, None, (
        last_error or (
            "Unable to connect to YouTube Live stream. "
            "Please ensure the stream is currently broadcasting live, or connect via RTSP."
        )
    )

