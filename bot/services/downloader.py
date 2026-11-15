"""Media metadata extraction and downloading service powered by yt-dlp and ffmpeg."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp
from loguru import logger
from yt_dlp.utils import DownloadError

from bot.config import settings

URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

TARGET_RESOLUTIONS = (1080, 720, 480, 360)

SINGLE_STREAM_PLATFORMS = {"TikTok", "Instagram", "Pinterest"}


class UserFriendlyDownloadError(Exception):
    """Exception carrying a clean, user-facing error message in Russian."""

    def __init__(self, user_message: str, original_error: Exception | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.original_error = original_error


@dataclass(slots=True)
class QualityOption:
    """Represents a selectable video resolution button."""

    height: int
    label: str
    approx_size_mb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "height": self.height,
            "label": self.label,
            "approx_size_mb": self.approx_size_mb,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityOption:
        return cls(
            height=int(data["height"]),
            label=str(data["label"]),
            approx_size_mb=int(data["approx_size_mb"]) if data.get("approx_size_mb") else None,
        )


@dataclass(slots=True)
class MediaMetadata:
    """Extracted metadata for a media URL."""

    url: str
    platform: str
    title: str
    uploader: str
    duration: int
    has_multiple_qualities: bool
    qualities: list[QualityOption] = field(default_factory=list)
    thumbnail_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "platform": self.platform,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "has_multiple_qualities": self.has_multiple_qualities,
            "qualities": [q.to_dict() for q in self.qualities],
            "thumbnail_url": self.thumbnail_url,
        }


@dataclass(slots=True)
class DownloadedMedia:
    """Result of a completed media download ready for Telegram upload."""

    file_path: Path
    temp_dir: Path
    media_type: str  # "video" or "audio"
    quality_label: str
    title: str
    uploader: str
    width: int
    height: int
    duration: int
    file_size: int


def extract_first_url(text: str | None) -> str | None:
    """Extract the first HTTP/HTTPS URL from message text."""
    if not text:
        return None
    match = URL_REGEX.search(text.strip())
    if not match:
        return None
    return match.group(0).rstrip(".,;!)>\"'")


def detect_platform(url: str, extractor: str | None = None) -> str:
    """Identify the social media platform from URL host or yt-dlp extractor."""
    host = urlparse(url).netloc.lower()
    ext = (extractor or "").lower()

    if "tiktok.com" in host or "tiktok" in ext:
        return "TikTok"
    if "instagram.com" in host or "ddinstagram.com" in host or "instagram" in ext:
        return "Instagram"
    if "youtube.com" in host or "youtu.be" in host or "youtube" in ext:
        return "YouTube"
    if "twitter.com" in host or "x.com" in host or "t.co" in host or "twitter" in ext:
        return "X (Twitter)"
    if "vk.com" in host or "vkvideo.ru" in host or "vk.ru" in host or "vk" in ext:
        return "VK"
    if "pinterest." in host or "pin.it" in host or "pinterest" in ext:
        return "Pinterest"
    if "reddit.com" in host or "redd.it" in host or "reddit" in ext:
        return "Reddit"
    if "twitch.tv" in host or "twitch" in ext:
        return "Twitch"
    if "rutube.ru" in host or "rutube" in ext:
        return "Rutube"
    if "soundcloud.com" in host or "soundcloud" in ext:
        return "SoundCloud"
    if "vimeo.com" in host or "vimeo" in ext:
        return "Vimeo"
    return "Other"


def translate_download_error(exc: Exception) -> UserFriendlyDownloadError:
    """Map raw yt-dlp / network exceptions into clear human-readable Russian messages."""
    if isinstance(exc, UserFriendlyDownloadError):
        return exc

    raw = str(exc).lower()

    if any(
        token in raw
        for token in (
            "private video",
            "private account",
            "this video is private",
            "status code 403",
            "only available for registered users",
            "this content is private",
        )
    ):
        return UserFriendlyDownloadError(
            "🔒 Контент недоступен: это приватный аккаунт или закрытое видео.",
            exc,
        )

    if any(
        token in raw
        for token in (
            "sign in to confirm your age",
            "age-restricted",
            "age restricted",
            "nsfw",
            "login required",
            "cookies",
            "sign in to confirm you’re not a bot",
            "sign in to confirm you're not a bot",
            "authentication",
        )
    ):
        return UserFriendlyDownloadError(
            "🔞 Контент имеет возрастные ограничения (18+) или требует авторизации (cookies).",
            exc,
        )

    if any(
        token in raw
        for token in (
            "video unavailable",
            "has been removed",
            "was deleted",
            "not found",
            "http error 404",
            "status code 404",
            "does not exist",
            "post not found",
        )
    ):
        return UserFriendlyDownloadError(
            "❌ Видео удалено автором или больше недоступно на платформе.",
            exc,
        )

    if any(
        token in raw
        for token in (
            "not available in your country",
            "geo-restricted",
            "georestricted",
            "blocked in your country",
        )
    ):
        return UserFriendlyDownloadError(
            "🌍 Это видео недоступно в регионе расположения сервера.",
            exc,
        )

    if any(
        token in raw
        for token in (
            "unsupported url",
            "no video formats found",
            "there is no video in this post",
            "unable to extract",
        )
    ):
        return UserFriendlyDownloadError(
            "⚠️ Ссылка не поддерживается или публикация не содержит видео/аудио.",
            exc,
        )

    if "file is larger than max-filesize" in raw or "exceeds maximum" in raw:
        return UserFriendlyDownloadError(
            f"📦 Размер файла превышает лимит ({settings.max_file_size_mb} МБ). "
            "Попробуйте выбрать более низкое разрешение или аудио.",
            exc,
        )

    return UserFriendlyDownloadError(
        "⚠️ Не удалось обработать ссылку. Проверьте корректность адреса и попробуйте снова.",
        exc,
    )


def _build_base_ydl_opts() -> dict[str, Any]:
    """Construct base yt-dlp options including proxy and cookies if configured."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        },
    }
    if settings.proxy_url.strip():
        opts["proxy"] = settings.proxy_url.strip()

    cookies_path = settings.valid_cookies_path
    if cookies_path:
        opts["cookiefile"] = cookies_path

    return opts


def _estimate_stream_bytes(fmt: dict[str, Any], duration: int) -> int:
    """Estimate format size in bytes from filesize, filesize_approx, or bitrate."""
    exact = fmt.get("filesize") or fmt.get("filesize_approx")
    if isinstance(exact, (int, float)) and exact > 0:
        return int(exact)

    tbr = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if isinstance(tbr, (int, float)) and tbr > 0 and duration > 0:
        return int((float(tbr) * 1000.0 / 8.0) * duration)

    return 0


def _extract_qualities(
    formats: list[dict[str, Any]],
    duration: int,
    platform: str,
) -> list[QualityOption]:
    """Extract available standard video resolutions (1080p, 720p, 480p, 360p) <= 1950 MB."""
    if platform in SINGLE_STREAM_PLATFORMS:
        return []

    max_bytes = settings.max_file_size_bytes

    # Find best audio stream size for adaptive (video-only) formats
    best_audio_bytes = 0
    for fmt in formats:
        vcodec = str(fmt.get("vcodec") or "none")
        acodec = str(fmt.get("acodec") or "none")
        if vcodec == "none" and acodec != "none":
            a_size = _estimate_stream_bytes(fmt, duration)
            if a_size > best_audio_bytes:
                best_audio_bytes = a_size

    if best_audio_bytes == 0 and duration > 0:
        # Assume ~128 kbps audio if not explicitly reported
        best_audio_bytes = int((128.0 * 1000.0 / 8.0) * duration)

    # Map each target resolution to the best candidate video format
    best_per_target: dict[int, int] = {}

    for fmt in formats:
        vcodec = str(fmt.get("vcodec") or "none")
        if vcodec == "none":
            continue

        height = fmt.get("height")
        width = fmt.get("width")
        if not isinstance(height, int) or height <= 0:
            continue

        # Normalize resolution bucket using the shorter dimension for vertical videos
        short_side = min(width, height) if isinstance(width, int) and width > 0 else height

        matched_target: int | None = None
        for target in TARGET_RESOLUTIONS:
            if abs(height - target) <= 60 or abs(short_side - target) <= 60:
                matched_target = target
                break

        if matched_target is None:
            continue

        stream_bytes = _estimate_stream_bytes(fmt, duration)
        acodec = str(fmt.get("acodec") or "none")
        total_bytes = stream_bytes + (0 if acodec != "none" else best_audio_bytes)

        if total_bytes > max_bytes:
            continue

        prev = best_per_target.get(matched_target, 0)
        # Prefer formats with known size, preferring H.264/MP4 within reasonable bounds
        if prev == 0 or (total_bytes > 0 and (prev == 0 or stream_bytes > 0)):
            if "avc1" in vcodec or "h264" in vcodec or prev == 0:
                best_per_target[matched_target] = total_bytes

    options: list[QualityOption] = []
    for res in TARGET_RESOLUTIONS:
        if res in best_per_target:
            size_bytes = best_per_target[res]
            size_mb = max(1, round(size_bytes / (1024 * 1024))) if size_bytes > 0 else None
            label = f"🎬 {res}p (~{size_mb} МБ)" if size_mb else f"🎬 {res}p"
            options.append(QualityOption(height=res, label=label, approx_size_mb=size_mb))

    return options


def _sync_extract_metadata(url: str) -> MediaMetadata:
    """Synchronously extract metadata via yt-dlp (intended to run in asyncio.to_thread)."""
    ydl_opts = _build_base_ydl_opts()
    ydl_opts["extract_flat"] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("Metadata extraction failed for {}: {}", url, exc)
        raise translate_download_error(exc) from exc

    if not info:
        raise UserFriendlyDownloadError(
            "⚠️ Не удалось получить метаданные по ссылке. Проверьте адрес."
        )

    # Unwrap playlist / carousel entries if needed
    if info.get("_type") == "playlist" or "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise UserFriendlyDownloadError(
                "⚠️ Публикация не содержит доступных медиафайлов."
            )
        info = entries[0]

    extractor = str(info.get("extractor_key") or info.get("extractor") or "")
    platform = detect_platform(url, extractor)
    title = str(info.get("title") or info.get("description") or f"{platform} Media").strip()
    if len(title) > 120:
        title = title[:117] + "..."
    uploader = str(info.get("uploader") or info.get("channel") or platform).strip()
    duration = int(float(info.get("duration") or 0))
    thumbnail_url = info.get("thumbnail")

    formats = info.get("formats") or []
    qualities = _extract_qualities(formats, duration, platform)
    has_multiple = len(qualities) >= 2 and platform not in SINGLE_STREAM_PLATFORMS

    return MediaMetadata(
        url=url,
        platform=platform,
        title=title,
        uploader=uploader,
        duration=duration,
        has_multiple_qualities=has_multiple,
        qualities=qualities,
        thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
    )


async def extract_media_info(url: str) -> MediaMetadata:
    """Asynchronously extract metadata and available qualities for a URL."""
    return await asyncio.to_thread(_sync_extract_metadata, url)


def _probe_media_file(file_path: Path) -> dict[str, Any]:
    """Run ffprobe on a downloaded file to inspect codecs, dimensions, and duration."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,duration:format=duration",
        "-of",
        "json",
        str(file_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return {}
        return json.loads(proc.stdout)
    except Exception as exc:
        logger.debug("ffprobe failed on {}: {}", file_path, exc)
        return {}


def _ensure_h264_faststart_mp4(source_path: Path) -> tuple[Path, int, int, int]:
    """Ensure the video file is MP4 (H.264 + AAC) with +faststart and return (path, w, h, dur)."""
    probe = _probe_media_file(source_path)
    streams = probe.get("streams") or []
    fmt_info = probe.get("format") or {}

    vcodec = ""
    acodec = ""
    width = 0
    height = 0
    duration = int(float(fmt_info.get("duration") or 0))

    for stream in streams:
        stype = stream.get("codec_type")
        if stype == "video" and not vcodec:
            vcodec = str(stream.get("codec_name") or "").lower()
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if not duration and stream.get("duration"):
                duration = int(float(stream["duration"]))
        elif stype == "audio" and not acodec:
            acodec = str(stream.get("codec_name") or "").lower()

    needs_video_reencode = vcodec not in ("h264", "avc1", "")
    needs_audio_reencode = bool(acodec) and acodec not in ("aac", "mp4a", "mp3")
    is_mp4_ext = source_path.suffix.lower() == ".mp4"

    if not needs_video_reencode and not needs_audio_reencode and is_mp4_ext:
        return source_path, width, height, duration

    converted_path = source_path.with_name(f"{source_path.stem}_h264.mp4")
    v_args = (
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        if needs_video_reencode
        else ["-c:v", "copy"]
    )
    a_args = (
        ["-c:a", "aac", "-b:a", "192k"]
        if needs_audio_reencode
        else (["-c:a", "copy"] if acodec else [])
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        *v_args,
        *a_args,
        "-movflags",
        "+faststart",
        str(converted_path),
    ]
    logger.info(
        "Normalizing video container/codecs (vcodec={}, acodec={}) -> H.264/AAC MP4",
        vcodec,
        acodec,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, check=False)
    if proc.returncode == 0 and converted_path.exists() and converted_path.stat().st_size > 0:
        source_path.unlink(missing_ok=True)
        reprobe = _probe_media_file(converted_path)
        for s in reprobe.get("streams") or []:
            if s.get("codec_type") == "video":
                width = int(s.get("width") or width)
                height = int(s.get("height") or height)
        return converted_path, width, height, duration

    logger.warning("ffmpeg conversion returned {}: {}", proc.returncode, proc.stderr[-400:])
    return source_path, width, height, duration


def _sync_download_media(
    url: str,
    choice: str,
    req_id: str,
    fallback_title: str = "Media",
    fallback_duration: int = 0,
) -> DownloadedMedia:
    """Synchronously download video or audio into an isolated temp folder."""
    temp_dir = settings.temp_downloads_dir / req_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = _build_base_ydl_opts()
    ydl_opts["outtmpl"] = str(temp_dir / "media_%(id)s.%(ext)s")
    ydl_opts["max_filesize"] = settings.max_file_size_bytes
    ydl_opts["trim_file_name"] = 80

    is_audio = choice == "audio"

    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
        quality_label = "MP3"
    else:
        if choice in ("1080", "720", "480", "360"):
            h = int(choice)
            ydl_opts["format"] = (
                f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={h}]+bestaudio/"
                f"best[height<={h}]/best"
            )
            quality_label = f"{h}p"
        else:
            ydl_opts["format"] = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/best"
            )
            quality_label = "HD"

        # Prefer H.264 + AAC so merging to MP4 is fast and iOS/Android compatible
        ydl_opts["format_sort"] = ["vcodec:h264", "acodec:m4a", "res", "br"]
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessor_args"] = {"ffmpeg": ["-movflags", "+faststart"]}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error("Download error for {} (choice={}): {}", url, choice, exc)
        raise translate_download_error(exc) from exc

    if info and ("entries" in info or info.get("_type") == "playlist"):
        entries = [e for e in (info.get("entries") or []) if e]
        if entries:
            info = entries[0]

    # Locate the downloaded output file inside temp_dir
    candidates = [
        p
        for p in temp_dir.iterdir()
        if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))
    ]
    if not candidates:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise UserFriendlyDownloadError(
            "⚠️ Не удалось сохранить медиафайл. Попробуйте другое качество."
        )

    # Pick the largest produced file
    output_file = max(candidates, key=lambda p: p.stat().st_size)

    title = str((info or {}).get("title") or fallback_title).strip()
    uploader = str((info or {}).get("uploader") or (info or {}).get("channel") or "LissSave").strip()
    info_width = int((info or {}).get("width") or 0)
    info_height = int((info or {}).get("height") or 0)
    info_duration = int(float((info or {}).get("duration") or fallback_duration or 0))

    if is_audio:
        probe = _probe_media_file(output_file)
        fmt_dur = (probe.get("format") or {}).get("duration")
        duration = int(float(fmt_dur)) if fmt_dur else info_duration
        width, height = 0, 0
    else:
        output_file, probed_w, probed_h, probed_dur = _ensure_h264_faststart_mp4(output_file)
        width = probed_w or info_width or 1280
        height = probed_h or info_height or 720
        duration = probed_dur or info_duration

    file_size = output_file.stat().st_size
    if file_size > settings.max_file_size_bytes:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise UserFriendlyDownloadError(
            f"📦 Итоговый размер файла ({file_size // (1024 * 1024)} МБ) "
            f"превышает лимит ({settings.max_file_size_mb} МБ)."
        )

    return DownloadedMedia(
        file_path=output_file,
        temp_dir=temp_dir,
        media_type="audio" if is_audio else "video",
        quality_label=quality_label,
        title=title[:100] if title else "Media",
        uploader=uploader[:64] if uploader else "LissSave",
        width=width,
        height=height,
        duration=duration,
        file_size=file_size,
    )


async def download_media(
    url: str,
    choice: str,
    req_id: str,
    fallback_title: str = "Media",
    fallback_duration: int = 0,
) -> DownloadedMedia:
    """Asynchronously download and post-process the requested media."""
    return await asyncio.to_thread(
        _sync_download_media,
        url,
        choice,
        req_id,
        fallback_title,
        fallback_duration,
    )
