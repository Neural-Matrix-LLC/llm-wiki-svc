"""Turn pending vision pages into markdown (Phase 2, design §4.10.4, plan §21.2 D2/D3).

Extractors stay free of model calls: a PDF page that has no text layer (or,
opted in, a figure-heavy one) and an uploaded image arrive here as rendered
PNG bytes under ``ExtractedDoc.extra["vision_pages"]`` with a
``<!-- vision:{key} -->`` placeholder in the text. This step describes each
one with the ``describe_image`` op - at most ``VISION_MAX_PAGES_PER_SOURCE``
per source - splices the markdown in, caches every description at
``raw/{id}/vision.json`` (so a recompile or a rebuild never pays twice) and
strips the image bytes. What leaves here is plain text: chunking, the lexical
index, the compiler and backfill never see an image.
"""

from __future__ import annotations

import json
import logging
import re

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.config import Settings
from llmwiki.extractors.base import ExtractionError
from llmwiki.llm.base import LLMClient, VisionLLMClient
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import ExtractedDoc, ImageInput
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import raw_vision

logger = logging.getLogger(__name__)

PLACEHOLDER = "<!-- vision:{key} -->"
_PLACEHOLDER_RE = re.compile(r"<!-- vision:([A-Za-z0-9_-]+) -->")
SKIPPED_NOTE = "> [{hint}: not described - VISION_MAX_PAGES_PER_SOURCE reached]"


def _has_prose(text: str) -> bool:
    """Anything besides headings, blank lines and vision placeholders."""
    stripped = _PLACEHOLDER_RE.sub("", text)
    return any(line.strip() and not line.lstrip().startswith("#")
               for line in stripped.splitlines())


def load_cache(store: ObjectStore, source_id: str) -> dict[str, str]:
    try:
        return dict(json.loads(store.get(raw_vision(source_id)).decode("utf-8")))
    except ObjectNotFound:
        return {}
    except Exception:
        logger.warning("vision cache for %s is unreadable; ignoring it", source_id)
        return {}


def save_cache(store: ObjectStore, source_id: str, cache: dict[str, str]) -> None:
    store.put(raw_vision(source_id), json.dumps(cache, indent=2).encode("utf-8"),
              "application/json")


def describe_pending_pages(
    doc: ExtractedDoc,
    llm: LLMClient,
    settings: Settings,
    store: ObjectStore,
) -> tuple[ExtractedDoc, list[CostRecord]]:
    """Fill the doc's vision placeholders. Returns the finished doc and the usage records."""
    pages: list[dict] = list(doc.extra.get("vision_pages") or [])
    if not pages:
        return doc, []
    if not isinstance(llm, VisionLLMClient):
        raise ExtractionError(
            f"{doc.source_id} needs image description but the configured LLM client cannot "
            "take images; route describe_image to a vision-capable provider or set VISION_MODE=off"
        )

    cache = load_cache(store, doc.source_id)
    cap = max(0, settings.vision_max_pages_per_source)
    system = load_prompt("describe_image")
    usage: list[CostRecord] = []
    calls = 0
    skipped = 0
    described: dict[str, str] = {}

    for page in pages:
        key, hint = str(page["key"]), str(page.get("hint") or page["key"])
        if key in cache:
            described[key] = cache[key]
            continue
        if calls >= cap:
            skipped += 1
            continue
        image = ImageInput(media_type=str(page.get("media_type") or "image/png"),
                           data=bytes(page["data"]))
        response = llm.describe(op="describe_image", system=system,
                                prompt=f"Source: {doc.title or doc.source_id}. Image: {hint}.",
                                images=[image])
        calls += 1
        if response.usage is not None:
            usage.append(response.usage)
        text = (response.text or "").strip()
        if text:
            described[key] = text
            cache[key] = text

    if calls:
        save_cache(store, doc.source_id, cache)

    def splice(match: re.Match[str]) -> str:
        key = match.group(1)
        hint = next((str(p.get("hint") or key) for p in pages if str(p["key"]) == key), key)
        if key in described:
            return f"#### Described content ({hint})\n\n{described[key]}"
        return SKIPPED_NOTE.format(hint=hint)

    text = _PLACEHOLDER_RE.sub(splice, doc.text).strip()
    extra = {k: v for k, v in doc.extra.items() if k != "vision_pages"}
    extra.update({"vision_described": len(described), "vision_calls": calls,
                  "vision_skipped": skipped})
    if not described and not _has_prose(doc.text):
        # Nothing was described and the extractor left only headings and
        # placeholders: storing that would be an empty source with a title.
        raise ExtractionError(f"{doc.source_id}: no text and no describable pages")
    logger.info("describe: source_id=%s described=%d calls=%d skipped=%d",
                doc.source_id, len(described), calls, skipped)
    return doc.model_copy(update={"text": text, "extra": extra}), usage
