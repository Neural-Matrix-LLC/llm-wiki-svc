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

A video with no captions at all falls through to a third, optional tier when
``YOUTUBE_WHISPER_MODEL`` is set: yt-dlp downloads the audio (with the same
cookies/proxy) and a local Whisper model transcribes it. That is minutes of
CPU per video and a ~2 GB dependency, so it is an extra (``llmwiki[whisper]``)
and off by default. It runs only for "no captions", never for a block - a bad
proxy or expired cookies are reported, not papered over.

All three produce the same segment list (``text``/``start``/``duration``), so
the stored raw object and :class:`YouTubeExtractor` do not know which one ran.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterator

from llmwiki.extractors.base import ExtractionError
from llmwiki.extractors.text import normalize
from llmwiki.models.source import ExtractedDoc, SourceMeta

logger = logging.getLogger(__name__)


class NoCaptions(ExtractionError):
    """The video is reachable but has no caption track - the one failure Whisper can fix."""

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
    url: str,
    proxy_url: str | None = None,
    cookies_path: str | None = None,
    whisper_model: str | None = None,
) -> bytes:
    """Fetch the transcript at capture time, stored verbatim as the raw source.

    ``cookies_path`` (``YOUTUBE_COOKIES_PATH``) selects the yt-dlp route: the
    captions are read with a logged-in session, which YouTube accepts from a
    cloud IP where an anonymous request is refused. Otherwise
    ``youtube-transcript-api`` is used, through ``proxy_url``
    (``YOUTUBE_PROXY_URL``) when set - the library's own answer to the block.
    yt-dlp honours the proxy too, if both are configured.

    ``whisper_model`` (``YOUTUBE_WHISPER_MODEL``) adds the last resort for a
    video that simply has no captions: download the audio and transcribe it
    locally. Slow and heavy, so opt-in; see the module docstring.

    A block is reported as a one-line :class:`ExtractionError` that names the
    fix rather than the library's multi-paragraph explanation, and nothing is
    stored: the URL stays capturable once the fix is in place (raw/ is
    immutable and content-addressed by URL, so an empty placeholder would have
    pinned the failure forever).
    """
    identifier = video_id(url)
    try:
        if cookies_path:
            segments = _segments_via_yt_dlp(identifier, cookies_path, proxy_url)
        else:
            segments = _segments_via_transcript_api(identifier, proxy_url)
    except NoCaptions as exc:
        if not whisper_model:
            raise ExtractionError(
                f"{exc}; set YOUTUBE_WHISPER_MODEL (e.g. base) to transcribe the audio instead"
            ) from exc
        logger.info("youtube %s has no captions; transcribing audio with whisper %s",
                    identifier, whisper_model)
        segments = _segments_via_whisper(identifier, whisper_model, cookies_path, proxy_url)
    return json.dumps(segments, ensure_ascii=False).encode("utf-8")


def _segments_via_transcript_api(identifier: str, proxy_url: str | None) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        IpBlocked,
        NoTranscriptFound,
        RequestBlocked,
        TranscriptsDisabled,
    )

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
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        raise NoCaptions(f"no captions (manual or automatic) on {identifier}") from exc
    except Exception as exc:
        raise ExtractionError(
            f"no transcript available for {identifier}: {_first_line(exc)}"
        ) from exc


def _segments_via_yt_dlp(
    identifier: str, cookies_path: str, proxy_url: str | None
) -> list[dict]:
    """Captions through yt-dlp with a logged-in session - no audio, no ffmpeg.

    Only the metadata call and one caption download happen; ``skip_download``
    keeps the video itself off the wire.
    """
    import yt_dlp
    from yt_dlp.utils import YoutubeDLError

    with _yt_dlp_opts(cookies_path, proxy_url, skip_download=True) as opts:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(_watch_url(identifier), download=False)
                track = pick_caption_track(info or {})
                if track is None:
                    raise NoCaptions(
                        f"no captions (manual or automatic) on {identifier}; yt-dlp saw "
                        "none in json3 format"
                    )
                payload = ydl.urlopen(track["url"]).read()
        except YoutubeDLError as exc:
            raise _yt_dlp_failure(identifier, "read captions for", exc) from exc
    try:
        return segments_from_json3(json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError(f"caption track for {identifier} is not json3") from exc


def _segments_via_whisper(
    identifier: str, model_name: str, cookies_path: str | None, proxy_url: str | None
) -> list[dict]:
    """Audio through yt-dlp, text through a local Whisper model (``llmwiki[whisper]``).

    ``bestaudio`` only - no video stream - into a temp dir that is removed
    whatever happens. ffmpeg must be on ``PATH``: yt-dlp may need it to
    extract the audio container and Whisper needs it to decode it.
    """
    import yt_dlp
    from yt_dlp.utils import YoutubeDLError

    try:
        import whisper
    except ImportError as exc:
        raise ExtractionError(
            f"YOUTUBE_WHISPER_MODEL={model_name} is set but openai-whisper is not installed: "
            "pip install 'llmwiki[whisper]' (plus ffmpeg on PATH), or unset it"
        ) from exc
    if shutil.which("ffmpeg") is None:
        raise ExtractionError(
            f"YOUTUBE_WHISPER_MODEL={model_name} is set but ffmpeg is not on PATH; "
            "install it (WITH_WHISPER=1 for the Docker image) or unset the setting"
        )

    workdir = tempfile.mkdtemp(prefix="llmwiki_yt_audio_")
    try:
        with _yt_dlp_opts(
            cookies_path,
            proxy_url,
            format="bestaudio/best",
            outtmpl=os.path.join(workdir, "%(id)s.%(ext)s"),
        ) as opts:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(_watch_url(identifier), download=True)
                    audio_path = ydl.prepare_filename(info)
            except YoutubeDLError as exc:
                raise _yt_dlp_failure(identifier, "download audio for", exc) from exc
        result = _load_whisper_model(whisper, model_name).transcribe(audio_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return segments_from_whisper(result)


@functools.lru_cache(maxsize=1)
def _load_whisper_model(whisper_module, model_name: str):  # type: ignore[no-untyped-def]
    """One model per process: loading ``base`` is ~1 GB of RAM, not per-video work."""
    return whisper_module.load_model(model_name)


def _watch_url(identifier: str) -> str:
    return f"https://www.youtube.com/watch?v={identifier}"


@contextlib.contextmanager
def _yt_dlp_opts(
    cookies_path: str | None, proxy_url: str | None, **extra: object
) -> Iterator[dict]:
    """yt-dlp options for one call, with the cookie file handled.

    yt-dlp writes refreshed cookies back to ``cookiefile`` after a successful
    request, so the configured file (a read-only mount in Docker) is copied to
    a temp file for the call and the copy removed afterwards.
    """
    if cookies_path and not os.path.isfile(cookies_path):
        raise ExtractionError(
            f"YOUTUBE_COOKIES_PATH={cookies_path} is not a file; export a Netscape-format "
            "cookies.txt from a browser logged in to YouTube, or unset it to use "
            "youtube-transcript-api"
        )
    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True, **extra}
    if proxy_url:
        opts["proxy"] = proxy_url
    writable_cookies: str | None = None
    if cookies_path:
        fd, writable_cookies = tempfile.mkstemp(prefix="llmwiki_yt_cookies_", suffix=".txt")
        os.close(fd)
        shutil.copyfile(cookies_path, writable_cookies)
        opts["cookiefile"] = writable_cookies
    try:
        yield opts
    finally:
        if writable_cookies is not None:
            with contextlib.suppress(OSError):
                os.unlink(writable_cookies)


def _yt_dlp_failure(identifier: str, what: str, exc: BaseException) -> ExtractionError:
    reason = _first_line(exc)
    hint = ""
    if "sign in" in reason.lower() or "bot" in reason.lower() or "403" in reason:
        hint = (
            " - YouTube did not accept the session; the cookies in "
            "YOUTUBE_COOKIES_PATH have probably expired, re-export them"
        )
    return ExtractionError(f"yt-dlp could not {what} {identifier}: {reason}{hint}")


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


def segments_from_whisper(result: dict) -> list[dict]:
    """Whisper's ``transcribe()`` result -> the same ``text``/``start``/``duration`` list."""
    segments: list[dict] = []
    for seg in result.get("segments") or []:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        # Millisecond precision, like YouTube's own tracks - not float noise.
        segments.append(
            {"text": text, "start": round(start, 3), "duration": round(max(end - start, 0.0), 3)}
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
