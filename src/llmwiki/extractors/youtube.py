"""YouTube transcripts.

Capture stores the transcript JSON under ``raw/`` so extraction stays a pure
function of stored bytes - the transcript is the source, not the video.
"""

from __future__ import annotations

import json
import re

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


def fetch_transcript(url: str, proxy_url: str | None = None) -> bytes:
    """Fetch the transcript at capture time, stored verbatim as the raw source.

    ``proxy_url`` (``YOUTUBE_PROXY_URL``) routes the request through an HTTP(S)
    proxy: YouTube refuses transcript requests from most cloud-provider egress
    IPs, so a service deployed on one needs a residential/rotating proxy to
    capture videos at all. Left unset, the request goes out directly - fine for
    a laptop, blocked from a datacenter.

    A block is reported as a one-line :class:`ExtractionError` that names the
    fix rather than the library's multi-paragraph explanation, and nothing is
    stored: the URL stays capturable once the proxy is configured (raw/ is
    immutable and content-addressed by URL, so an empty placeholder would have
    pinned the failure forever).
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import IpBlocked, RequestBlocked

    identifier = video_id(url)
    proxy_config = None
    if proxy_url:
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    try:
        segments = YouTubeTranscriptApi(proxy_config=proxy_config).fetch(identifier).to_raw_data()
    except (RequestBlocked, IpBlocked) as exc:
        via = f"via proxy {proxy_url}" if proxy_url else "directly (no YOUTUBE_PROXY_URL set)"
        raise ExtractionError(
            f"YouTube blocked the transcript request for {identifier} sent {via}; "
            "cloud egress IPs are routinely refused - set YOUTUBE_PROXY_URL to a "
            "residential/rotating proxy and resend the link"
        ) from exc
    except Exception as exc:
        text = str(exc).strip()
        first_line = text.splitlines()[0] if text else type(exc).__name__
        raise ExtractionError(f"no transcript available for {identifier}: {first_line}") from exc
    return json.dumps(segments, ensure_ascii=False).encode("utf-8")


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
