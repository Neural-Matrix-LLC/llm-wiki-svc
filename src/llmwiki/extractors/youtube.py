"""YouTube transcripts.

Capture stores the transcript JSON under ``raw/`` so extraction stays a pure
function of stored bytes - the transcript is the source, not the video.

Two ways to fetch that JSON, chosen by configuration (2026-09-20):

* ``youtube-transcript-api`` (default) - one small request to YouTube's caption
  endpoint, optionally through ``YOUTUBE_PROXY_URL``. Cloud egress IPs are
  refused without the proxy.
* ``yt-dlp`` with a logged-in session's ``YOUTUBE_COOKIES_PATH`` - the route
  the FUND-financial-Research agents settled on. No proxy to pay for, but the
  account behind the cookies can be banned and the file expires. Cookies win
  when both are set.

Both produce the same segment list (``text``/``start``/``duration``), so the
stored raw object and :class:`YouTubeExtractor` do not know which one ran.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile

from llmwiki.extractors.base import ExtractionError
from llmwiki.extractors.text import normalize
from llmwiki.models.source import ExtractedDoc, SourceMeta

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/|/v/)([A-Za-z0-9_-]{11})")
_YOUTUBE_HOST = re.compile(
    r"^(?:https?://)?(?:[a-z0-9-]+\.)*(?:youtube\.com|youtube-nocookie\.com|youtu\.be)/",
    re.IGNORECASE,
)


def is_youtube_url(url: str) -> bool:
    """True for any YouTube URL a video id can be read out of.

    Host *and* id are both required: a channel or playlist page is a web page,
    not a transcript, and must fall through to the web extractor rather than
    fail later in ``fetch_transcript``.
    """
    stripped = url.strip()
    return bool(_YOUTUBE_HOST.match(stripped) and _VIDEO_ID.search(stripped))


def video_id(url: str) -> str:
    """Pull the 11-character video id out of any common YouTube URL shape."""
    match = _VIDEO_ID.search(url)
    if not match:
        raise ExtractionError(f"no YouTube video id in {url!r}")
    return match.group(1)


def fetch_video_title(url: str) -> str:
    """Read the public video title via oEmbed. Empty string if the lookup fails.

    Capture stores this on ``meta.json`` once; extraction stays a pure function
    of the stored transcript bytes and does not touch the network.
    """
    import httpx

    try:
        identifier = video_id(url)
    except ExtractionError:
        return ""
    oembed = (
        "https://www.youtube.com/oembed"
        f"?url=https://www.youtube.com/watch?v={identifier}&format=json"
    )
    try:
        response = httpx.get(
            oembed,
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "llmwiki/0.9 (+research knowledge base)"},
        )
        response.raise_for_status()
        title = response.json().get("title")
    except Exception:
        return ""
    return str(title or "").strip()


def fetch_transcript(
    url: str, proxy_url: str | None = None, cookies_path: str | None = None
) -> bytes:
    """Fetch the transcript at capture time, stored verbatim as the raw source.

    ``cookies_path`` (``YOUTUBE_COOKIES_PATH``) selects the yt-dlp route: the
    captions are read with a logged-in session, which YouTube accepts from a
    cloud IP where an anonymous request is refused. Otherwise
    ``youtube-transcript-api`` is used, through ``proxy_url``
    (``YOUTUBE_PROXY_URL``) when set - the library's own answer to the block.
    yt-dlp honours the proxy too, if both are configured.

    A block is reported as a one-line :class:`ExtractionError` that names the
    fix rather than the library's multi-paragraph explanation, and nothing is
    stored: the URL stays capturable once the fix is in place (raw/ is
    immutable and content-addressed by URL, so an empty placeholder would have
    pinned the failure forever).
    """
    identifier = video_id(url)
    if cookies_path:
        segments = _segments_via_yt_dlp(identifier, cookies_path, proxy_url)
    else:
        segments = _segments_via_transcript_api(identifier, proxy_url)
    return json.dumps(segments, ensure_ascii=False).encode("utf-8")


def _segments_via_transcript_api(identifier: str, proxy_url: str | None) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import IpBlocked, RequestBlocked

    proxy_config = None
    if proxy_url:
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    try:
        fetched = YouTubeTranscriptApi(proxy_config=proxy_config).fetch(identifier)
        return list(fetched.to_raw_data())
    except (RequestBlocked, IpBlocked) as exc:
        via = f"via proxy {proxy_url}" if proxy_url else "directly (no YOUTUBE_PROXY_URL set)"
        raise ExtractionError(
            f"YouTube blocked the transcript request for {identifier} sent {via}; "
            "cloud egress IPs are routinely refused - set YOUTUBE_PROXY_URL to a "
            "residential/rotating proxy, or YOUTUBE_COOKIES_PATH to a logged-in "
            "session's cookies.txt, and resend the link"
        ) from exc
    except Exception as exc:
        raise ExtractionError(
            f"no transcript available for {identifier}: {_first_line(exc)}"
        ) from exc


def _segments_via_yt_dlp(
    identifier: str, cookies_path: str, proxy_url: str | None
) -> list[dict]:
    """Captions through yt-dlp with a logged-in session - no audio, no ffmpeg.

    Only the metadata call and one caption download happen; ``skip_download``
    keeps the video itself off the wire. yt-dlp writes refreshed cookies back
    to ``cookiefile`` after a successful request, so the configured file (a
    read-only mount in Docker) is copied to a temp file for the call.
    """
    import yt_dlp
    from yt_dlp.utils import YoutubeDLError

    if not os.path.isfile(cookies_path):
        raise ExtractionError(
            f"YOUTUBE_COOKIES_PATH={cookies_path} is not a file; export a Netscape-format "
            "cookies.txt from a browser logged in to YouTube, or unset it to use "
            "youtube-transcript-api"
        )
    fd, writable_cookies = tempfile.mkstemp(prefix="llmwiki_yt_cookies_", suffix=".txt")
    os.close(fd)
    shutil.copyfile(cookies_path, writable_cookies)
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "cookiefile": writable_cookies,
    }
    if proxy_url:
        opts["proxy"] = proxy_url
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={identifier}", download=False)
            track = pick_caption_track(info or {})
            if track is None:
                raise ExtractionError(
                    f"no captions (manual or automatic) on {identifier}; yt-dlp saw none "
                    "in json3 format - the video has no transcript to capture"
                )
            payload = ydl.urlopen(track["url"]).read()
    except YoutubeDLError as exc:
        reason = _first_line(exc)
        hint = ""
        if "sign in" in reason.lower() or "bot" in reason.lower() or "403" in reason:
            hint = (
                " - YouTube did not accept the session; the cookies in "
                "YOUTUBE_COOKIES_PATH have probably expired, re-export them"
            )
        raise ExtractionError(
            f"yt-dlp could not read captions for {identifier}: {reason}{hint}"
        ) from exc
    finally:
        try:
            os.unlink(writable_cookies)
        except OSError:
            pass
    try:
        return segments_from_json3(json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError(f"caption track for {identifier} is not json3") from exc


def pick_caption_track(info: dict) -> dict | None:
    """Choose one json3 caption track from a yt-dlp info dict, or ``None``.

    Manual subtitles before automatic captions (a human wrote them); within
    either, ``en`` first, then any ``en-*`` variant, then whatever the video
    has. Pure function, so the preference is unit-testable without yt-dlp.
    """
    for kind in ("subtitles", "automatic_captions"):
        tracks = info.get(kind) or {}
        if not tracks:
            continue
        languages = sorted(
            tracks,
            key=lambda lang: (lang != "en", not lang.startswith("en"), lang),
        )
        for lang in languages:
            for fmt in tracks[lang] or []:
                if fmt.get("ext") == "json3" and fmt.get("url"):
                    return {"kind": kind, "lang": lang, "url": fmt["url"]}
    return None


def segments_from_json3(payload: dict) -> list[dict]:
    """YouTube's json3 caption events -> the ``text``/``start``/``duration`` list.

    The same shape ``youtube-transcript-api`` returns from ``to_raw_data()``,
    so ``raw/`` looks identical whichever route fetched it. Events without
    text (window definitions, bare newlines) are dropped.
    """
    segments: list[dict] = []
    for event in payload.get("events") or []:
        text = "".join(str(seg.get("utf8", "")) for seg in event.get("segs") or [])
        text = text.strip()
        if not text:
            continue
        segments.append(
            {
                "text": text,
                "start": float(event.get("tStartMs", 0)) / 1000.0,
                "duration": float(event.get("dDurationMs", 0)) / 1000.0,
            }
        )
    return segments


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


class YouTubeExtractor:
    """Timestamped segments to paragraphs, with timestamps kept as section markers."""

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        try:
            segments = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtractionError(f"transcript for {meta.source_id} is not valid JSON") from exc
        if not segments:
            raise ExtractionError(f"empty transcript for {meta.source_id}")

        paragraphs: list[str] = []
        buffer: list[str] = []
        start = float(segments[0].get("start", 0.0))
        for segment in segments:
            buffer.append(str(segment.get("text", "")).strip())
            if float(segment.get("start", 0.0)) - start >= 60.0:
                paragraphs.append(f"### {_stamp(start)}\n\n{' '.join(buffer).strip()}")
                buffer = []
                start = float(segment.get("start", 0.0))
        if buffer:
            paragraphs.append(f"### {_stamp(start)}\n\n{' '.join(buffer).strip()}")

        return ExtractedDoc(
            source_id=meta.source_id,
            title=meta.title or f"YouTube transcript {meta.url or meta.source_id}",
            text=normalize("\n\n".join(paragraphs)),
            modality="youtube",
            url=meta.url,
            extra={"segment_count": len(segments)},
        )


def _stamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
