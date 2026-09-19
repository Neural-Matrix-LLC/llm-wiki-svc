"""PDF page-as-image selection (Phase 2, plan §21.2 D4, §21.6.5) on three synthetic PDFs.

``off`` is the pre-Phase-2 extractor byte for byte (text pages extracted, a
scan raises). ``auto`` renders scanned pages in order and figure pages by
image area, at most the cap; text-only pages cost nothing. ``always`` renders
every page up to the cap.
"""

from __future__ import annotations

import pytest

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.extractors.base import ExtractionError, get_extractor
from llmwiki.extractors.pdf import PdfExtractor
from llmwiki.models.source import SourceMeta

META = SourceMeta(source_id="a" * 16, modality="pdf", title="Doc", mime="application/pdf",
                  sha256="0" * 64)


def _png(width: int = 200, height: int = 200) -> bytes:
    import pymupdf

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(90)
    return bytes(pixmap.tobytes("png"))


def _pdf(pages: list[dict]) -> bytes:
    """Build a PDF: each page spec has optional ``text`` and ``image`` (fraction of the page)."""
    import pymupdf

    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=400, height=500)
        if spec.get("text"):
            page.insert_textbox(pymupdf.Rect(30, 30, 370, 470), spec["text"], fontsize=10)
        if spec.get("image"):
            frac = spec["image"]
            rect = pymupdf.Rect(20, 100, 20 + 360 * frac, 100 + 380 * frac)
            page.insert_image(rect, stream=_png())
    return doc.tobytes()


TEXT = "This page has a proper text layer with several sentences of prose. " * 4
TEXT_ONLY = _pdf([{"text": TEXT}, {"text": TEXT + " Second page."}])
SCANNED = _pdf([{"image": 1.0}, {"image": 1.0}, {"image": 1.0}])
# The figure page keeps a full text layer (a caption plus prose), so only the
# image-area rule can select it - the scan rule must not.
FIGURE_HEAVY = _pdf([{"text": TEXT}, {"text": "Figure 2. " + TEXT, "image": 0.9}, {"text": TEXT}])


# --- off: the pre-Phase-2 extractor ------------------------------------------------------


def test_off_extracts_text_pages_and_raises_on_a_scan() -> None:
    doc = PdfExtractor().extract(META, TEXT_ONLY)
    assert "### Page 1" in doc.text and "### Page 2" in doc.text
    assert doc.extra == {"page_count": 2} and "vision_pages" not in doc.extra
    with pytest.raises(ExtractionError, match="no text layer"):
        PdfExtractor().extract(META, SCANNED)
    figure = PdfExtractor().extract(META, FIGURE_HEAVY)
    assert "<!-- vision" not in figure.text and "vision_pages" not in figure.extra


# --- auto -------------------------------------------------------------------------------


def test_auto_leaves_a_text_only_pdf_alone() -> None:
    doc = PdfExtractor(vision_mode="auto").extract(META, TEXT_ONLY)
    assert "vision_pages" not in doc.extra and doc.extra["vision_candidates"] == 0
    assert "<!-- vision" not in doc.text


def test_auto_renders_a_scan_in_page_order_up_to_the_cap() -> None:
    doc = PdfExtractor(vision_mode="auto", max_pages=2).extract(META, SCANNED)
    pages = doc.extra["vision_pages"]
    assert [p["key"] for p in pages] == ["p1", "p2"], "reading order for a scan"
    assert all(p["data"][:4] == b"\x89PNG" and p["media_type"] == "image/png" for p in pages)
    assert doc.extra["vision_candidates"] == 3
    assert doc.text.count("<!-- vision:") == 2
    assert "page 3: figure-heavy, not described" in doc.text
    assert "### Page 3" in doc.text


def test_auto_picks_the_figure_page_by_image_area_and_keeps_its_text() -> None:
    doc = PdfExtractor(vision_mode="auto", max_pages=8).extract(META, FIGURE_HEAVY)
    pages = doc.extra["vision_pages"]
    assert [p["key"] for p in pages] == ["p2"]
    assert doc.extra["vision_candidates"] == 1
    assert "Figure 2" in doc.text and "<!-- vision:p2 -->" in doc.text
    assert doc.text.index("### Page 1") < doc.text.index("<!-- vision:p2 -->")


def test_image_area_threshold_is_respected() -> None:
    strict = PdfExtractor(vision_mode="auto", image_area=0.95)
    assert "vision_pages" not in strict.extract(META, FIGURE_HEAVY).extra
    loose = PdfExtractor(vision_mode="auto", image_area=0.1)
    assert [p["key"] for p in loose.extract(META, FIGURE_HEAVY).extra["vision_pages"]] == ["p2"]


def test_always_renders_every_page_up_to_the_cap() -> None:
    doc = PdfExtractor(vision_mode="always", max_pages=2).extract(META, FIGURE_HEAVY)
    assert [p["key"] for p in doc.extra["vision_pages"]] == ["p1", "p2"]
    assert doc.extra["vision_candidates"] == 3
    assert "page 3: figure-heavy, not described" in doc.text


def test_cap_of_zero_renders_nothing_and_a_scan_still_raises() -> None:
    with pytest.raises(ExtractionError, match="no page could be selected"):
        PdfExtractor(vision_mode="auto", max_pages=0).extract(META, SCANNED)


def test_rendered_pages_respect_the_edge_cap() -> None:
    import pymupdf

    big = _pdf([{"image": 1.0}])
    doc = PdfExtractor(vision_mode="auto").extract(META, big)
    pixmap = pymupdf.Pixmap(doc.extra["vision_pages"][0]["data"])
    assert max(pixmap.width, pixmap.height) <= 1568


def test_get_extractor_threads_the_settings_through() -> None:
    extractor = get_extractor("pdf", vision_mode="auto", vision_max_pages=3,
                              vision_min_chars=50, vision_image_area=0.5)
    assert isinstance(extractor, PdfExtractor)
    assert (extractor.vision_mode, extractor.max_pages, extractor.min_chars,
            extractor.image_area) == ("auto", 3, 50, 0.5)


# --- end to end ----------------------------------------------------------------------------


def test_scanned_pdf_reaches_done_offline_with_bounded_vision_calls(tmp_path) -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path / "data", embedding_dim=64, worker_mode="inline",
                   vision_mode="auto", vision_max_pages_per_source=2)
    factory.reset()
    try:
        status = tools.ingest_now(file=SCANNED, filename="scan.pdf", mime="application/pdf",
                                  title="Scanned memo", cfg=cfg)
        assert status.state == "done", status
        assert status.vision_calls == 2
        extracted = factory.object_store(cfg).get(f"raw/{status.source_id}/extracted.md").decode()
        assert extracted.count("#### Described content") == 2
        assert "page 3: figure-heavy, not described" in extracted

        off = cfg.model_copy(update={"vision_mode": "off"})
        factory.reset()
        failed = tools.ingest_now(file=_pdf([{"image": 1.0}]), filename="other.pdf",
                                  mime="application/pdf", cfg=off)
        assert failed.state == "failed" and "no text layer" in (failed.error or "")
    finally:
        factory.reset()
