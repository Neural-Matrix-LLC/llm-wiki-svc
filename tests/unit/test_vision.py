"""Selective multimodal, the text-first way (Phase 2, plan §21.2 D1-D3).

The vision protocol on every adapter (message shapes), the router's refusal
of a text-only adapter, the description step with its cache and cap, the
image extractor, and an uploaded image reaching ``done`` end to end offline.
"""

from __future__ import annotations

import base64
import json

import pytest
from langchain_core.messages import AIMessage
from tests.doubles import ScriptedLLM

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.extractors.base import ExtractionError, get_extractor
from llmwiki.extractors.image import ImageExtractor, render_png
from llmwiki.llm.base import LLMClient, VisionLLMClient
from llmwiki.llm.fake import FakeLLM
from llmwiki.llm.metering import MeteredLLM, collect_usage
from llmwiki.models.source import ExtractedDoc, ImageInput, SourceMeta
from llmwiki.pipeline.describe import PLACEHOLDER, describe_pending_pages, load_cache
from llmwiki.storage.layout import raw_vision


def _png(width: int = 40, height: int = 30) -> bytes:
    import pymupdf

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(200)
    return bytes(pixmap.tobytes("png"))


IMAGE = ImageInput(media_type="image/png", data=b"\x89PNGfake")


# --- protocol and adapters ---------------------------------------------------------------


def test_fake_and_metered_clients_implement_the_vision_protocol() -> None:
    fake = FakeLLM()
    assert isinstance(fake, VisionLLMClient) and isinstance(fake, LLMClient)
    metered = MeteredLLM(fake)
    with collect_usage() as records:
        response = metered.describe(op="describe_image", system="s", prompt="page 1",
                                    images=[IMAGE])
    assert "Offline description of 1 image(s)" in response.text
    assert [r.op for r in records] == ["describe_image"]
    assert fake.calls[-1]["images"] == 1


def test_complete_contract_is_untouched_by_the_vision_protocol() -> None:
    """P5: a text-only client is still a full LLMClient; it just is not a VisionLLMClient."""
    scripted = ScriptedLLM({})
    assert isinstance(scripted, LLMClient) and not isinstance(scripted, VisionLLMClient)
    with pytest.raises(AttributeError):
        MeteredLLM(scripted).describe(op="describe_image", system="s", prompt="p", images=[IMAGE])


def test_anthropic_describe_sends_image_blocks_before_the_text() -> None:
    from llmwiki.llm.anthropic_client import AnthropicLLM

    llm = AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5")
    seen: dict = {}

    class _Usage:
        input_tokens, output_tokens = 10, 5
        cache_read_input_tokens = cache_creation_input_tokens = 0

    class _Block:
        type, text = "text", "a caption"

    class _Message:
        usage, content = _Usage(), [_Block()]

    class _Messages:
        def create(self, **request):
            seen.update(request)
            return _Message()

    class _Client:
        messages = _Messages()

    llm._client = _Client()
    response = llm.describe(op="describe_image", system="sys", prompt="page 2", images=[IMAGE])

    content = seen["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(content[0]["source"]["data"]) == IMAGE.data
    assert content[-1] == {"type": "text", "text": "page 2"}
    assert response.text == "a caption" and response.usage.op == "describe_image"


def test_langchain_describe_sends_data_uri_image_parts() -> None:
    from tests.unit.test_langchain_client import make_client

    client, built = make_client(AIMessage(content="described"))
    response = client.describe(op="describe_image", system="sys", prompt="page 3",
                               images=[IMAGE], model="google/gemini-2.5-flash-lite")
    human = built[0].messages[1]
    parts = human.content
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[-1] == {"type": "text", "text": "page 3"}
    assert built[0].model == "google/gemini-2.5-flash-lite"
    assert response.text == "described"


def test_router_dispatches_describe_and_refuses_a_text_only_adapter() -> None:
    from llmwiki.llm.router import RoutingLLMClient
    from llmwiki.llm.routing_config import OpRoute, RoutingConfig

    routing = RoutingConfig(providers={}, ops={
        "describe_image": OpRoute("describe_image", "vision", "v-model", 0.2, 2048),
        "summarize_source": OpRoute("summarize_source", "text", "t-model", 1.0, 2048),
    })
    vision, text = FakeLLM(), ScriptedLLM({})
    router = RoutingLLMClient(routing, {"vision": vision, "text": text})
    assert router.supports_vision("describe_image")
    assert not router.supports_vision("summarize_source")
    router.describe(op="describe_image", system="s", prompt="p", images=[IMAGE])
    assert vision.calls[-1]["model"] == "v-model" and vision.calls[-1]["max_tokens"] == 2048

    bad = RoutingConfig(providers={}, ops={
        "describe_image": OpRoute("describe_image", "text", "t-model", 0.2, 2048)})
    with pytest.raises(RuntimeError, match="cannot take images"):
        RoutingLLMClient(bad, {"text": text}).describe(op="describe_image", system="s",
                                                        prompt="p", images=[IMAGE])


def test_factory_refuses_at_startup_when_describe_image_routes_to_a_text_only_adapter(
    tmp_path, monkeypatch,
) -> None:
    from llmwiki import factory

    class NoVision:
        def complete(self, **kw):  # pragma: no cover - never called
            raise AssertionError

    monkeypatch.setattr(factory, "_construct_provider_client", lambda *a, **k: NoVision())
    providers = tmp_path / "providers.py"
    ops = tmp_path / "ops.py"
    providers.write_text('PROVIDERS = [{"provider": "fake"}]\n')
    rows = ", ".join(f'{{"op": "{op}", "provider": "fake", "model": "m"}}'
                     for op in sorted(__import__("llmwiki.llm.routing_config",
                                                 fromlist=["KNOWN_OPS"]).KNOWN_OPS))
    ops.write_text(f"OPS = [{rows}]\n")
    cfg = Settings(_env_file=None, llm_providers_config=providers, llm_ops_config=ops)
    factory.reset()
    try:
        with pytest.raises(RuntimeError, match="describe_image"):
            factory.llm_client(cfg)
    finally:
        factory.reset()


# --- the description step --------------------------------------------------------------


def _doc_with_pages(n: int) -> ExtractedDoc:
    text = "\n\n".join(f"### Page {i + 1}\n\n{PLACEHOLDER.format(key=f'p{i + 1}')}"
                       for i in range(n))
    pages = [{"key": f"p{i + 1}", "media_type": "image/png", "data": _png(),
              "hint": f"page {i + 1}"} for i in range(n)]
    return ExtractedDoc(source_id="a" * 16, title="Scan", text=text, modality="pdf",
                        extra={"vision_pages": pages, "page_count": n})


def test_describe_fills_placeholders_caches_and_strips_the_bytes(store, settings) -> None:
    llm = FakeLLM()
    doc, usage = describe_pending_pages(_doc_with_pages(2), llm, settings, store)

    assert "vision_pages" not in doc.extra
    assert doc.extra["vision_calls"] == 2 and doc.extra["vision_described"] == 2
    assert "<!-- vision:" not in doc.text
    assert "#### Described content (page 1)" in doc.text
    assert [r.op for r in usage] == ["describe_image", "describe_image"]
    cache = load_cache(store, "a" * 16)
    assert set(cache) == {"p1", "p2"}
    assert store.exists(raw_vision("a" * 16))

    llm.calls.clear()
    again, usage = describe_pending_pages(_doc_with_pages(2), llm, settings, store)
    assert llm.calls == [] and usage == [], "the cache means a recompile never pays twice"
    assert again.extra["vision_calls"] == 0 and again.extra["vision_described"] == 2


def test_vision_calls_per_source_are_bounded_by_setting(store, settings) -> None:
    """Load-bearing scale guard (plan §21.10): a 40-page scan costs at most the cap."""
    llm = FakeLLM()
    cfg = settings.model_copy(update={"vision_max_pages_per_source": 3})
    doc, usage = describe_pending_pages(_doc_with_pages(40), llm, cfg, store)
    assert len(llm.calls) == 3 and len(usage) == 3
    assert doc.extra == {"page_count": 40, "vision_described": 3, "vision_calls": 3,
                         "vision_skipped": 37}
    assert doc.text.count("not described - VISION_MAX_PAGES_PER_SOURCE reached") == 37


def test_describe_without_a_vision_client_or_content_is_a_clear_extraction_error(
    store, settings,
) -> None:
    with pytest.raises(ExtractionError, match="cannot take images"):
        describe_pending_pages(_doc_with_pages(1), ScriptedLLM({}), settings, store)
    llm = FakeLLM({"describe_image": {"text": ""}})
    with pytest.raises(ExtractionError, match="no text and no describable pages"):
        describe_pending_pages(_doc_with_pages(1), llm, settings, store)


def test_unreadable_cache_is_ignored(store, settings) -> None:
    store.put(raw_vision("a" * 16), b"not json")
    doc, usage = describe_pending_pages(_doc_with_pages(1), FakeLLM(), settings, store)
    assert doc.extra["vision_calls"] == 1
    assert json.loads(store.get(raw_vision("a" * 16)))["p1"]


# --- the image extractor ---------------------------------------------------------------


def test_render_png_normalises_and_downscales() -> None:
    import pymupdf

    small = render_png(_png(40, 30))
    assert pymupdf.Pixmap(small).width == 40
    big = render_png(_png(4000, 1000), max_edge=1568)
    pixmap = pymupdf.Pixmap(big)
    assert max(pixmap.width, pixmap.height) <= 1568
    with pytest.raises(ExtractionError):
        render_png(b"not an image")


def test_image_extractor_emits_one_vision_page_or_refuses_when_off() -> None:
    meta = SourceMeta(source_id="a" * 16, modality="image", title="Whiteboard", mime="image/png",
                      sha256="0" * 64)
    with pytest.raises(ExtractionError, match="VISION_MODE"):
        ImageExtractor(vision_mode="off").extract(meta, _png())
    doc = ImageExtractor(vision_mode="auto").extract(meta, _png())
    assert doc.modality == "image" and doc.title == "Whiteboard"
    assert doc.text == "# Whiteboard\n\n<!-- vision:p1 -->\n"
    (page,) = doc.extra["vision_pages"]
    assert page["key"] == "p1" and page["media_type"] == "image/png"
    assert page["data"][:4] == b"\x89PNG"
    assert isinstance(get_extractor("image", vision_mode="auto"), ImageExtractor)


def test_get_extractor_default_is_the_pre_phase2_behaviour() -> None:
    """VISION_MODE=off: an image still fails at extraction, as before (a clearer message)."""
    meta = SourceMeta(source_id="a" * 16, modality="image", mime="image/png", sha256="0" * 64)
    with pytest.raises(ExtractionError):
        get_extractor("image").extract(meta, _png())


# --- end to end: an uploaded image reaches done, its cost is ledgered and budgeted ---------


def test_uploaded_image_reaches_done_offline_when_vision_is_on(tmp_path) -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path / "data", embedding_dim=64, worker_mode="inline",
                   vision_mode="auto")
    factory.reset()
    try:
        status = tools.ingest_now(file=_png(), filename="board.png", mime="image/png",
                                  title="Whiteboard photo", cfg=cfg)
        assert status.state == "done", status
        assert status.vision_calls == 1
        source_id = status.source_id
        extracted = factory.object_store(cfg).get(f"raw/{source_id}/extracted.md").decode()
        assert "Described content (uploaded image)" in extracted and "<!-- vision" not in extracted
        assert factory.object_store(cfg).exists(raw_vision(source_id))
        kinds = {r.op: r.kind for r in factory.ledger(cfg).read()}
        assert kinds["describe_image"] == "ingest"
        assert tools.get_source_status(source_id, cfg=cfg).state == "done"

        off = cfg.model_copy(update={"vision_mode": "off"})
        factory.reset()
        failed = tools.ingest_now(file=_png(10, 10), filename="other.png", mime="image/png",
                                  cfg=off)
        assert failed.state == "failed" and "VISION_MODE" in (failed.error or "")
    finally:
        factory.reset()


def test_vision_spend_counts_against_the_ingest_token_budget(tmp_path) -> None:
    """The compiler's budget check sees the description's tokens (plan §21.2 D4)."""
    from llmwiki import factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path / "data", embedding_dim=64, worker_mode="inline",
                   vision_mode="auto", ingest_token_budget=900)
    factory.reset()
    try:
        # The fake describe() bills ~800 input tokens; the compile's own calls
        # push past 900, so the source is marked failed by the budget guard.
        status = tools.ingest_now(file=_png(), filename="board.png", mime="image/png", cfg=cfg)
        assert status.state == "failed" and "INGEST_TOKEN_BUDGET" in (status.error or "")
    finally:
        factory.reset()
