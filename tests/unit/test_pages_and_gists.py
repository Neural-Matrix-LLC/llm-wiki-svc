"""Page format, optimistic concurrency, and the gist manifest."""

from __future__ import annotations

import pytest

from llmwiki.models.page import PageFrontMatter, PageGist, WikiPage
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import (
    VersionConflict,
    append_sources_section,
    parse_page,
    read_page,
    render_page,
    write_page,
)


def make_page(slug: str = "rag", version: int = 1) -> WikiPage:
    return WikiPage(
        front_matter=PageFrontMatter(
            title="Retrieval-Augmented Generation",
            slug=slug,
            type="concept",
            gist="Grounding answers in retrieved documents.",
            sources=["a1b2c3d4e5f60718"],
            version=version,
        ),
        body="## Summary\n\nGrounded generation.\n",
    )


def test_render_then_parse_round_trips() -> None:
    original = make_page()
    parsed = parse_page(render_page(original))

    assert parsed.front_matter.title == original.front_matter.title
    assert parsed.front_matter.sources == original.front_matter.sources
    assert parsed.front_matter.version == original.front_matter.version
    assert "Grounded generation." in parsed.body


def test_rendered_page_is_obsidian_compatible_front_matter() -> None:
    rendered = render_page(make_page())
    assert rendered.startswith("---\n")
    assert "\ntype: concept\n" in rendered
    assert rendered.count("---") >= 2


def test_write_bumps_the_version(store) -> None:
    written = write_page(store, make_page(version=0), expected_version=0)
    assert written.front_matter.version == 1

    reloaded = read_page(store, "rag")
    assert reloaded is not None and reloaded.front_matter.version == 1


def test_concurrent_write_is_refused(store) -> None:
    write_page(store, make_page(version=0), expected_version=0)  # now at v1
    write_page(store, make_page(version=1), expected_version=1)  # now at v2

    with pytest.raises(VersionConflict, match="expected version 1"):
        write_page(store, make_page(version=1), expected_version=1)


def test_read_of_a_missing_page_is_none_not_an_error(store) -> None:
    assert read_page(store, "does-not-exist") is None


def test_free_text_front_matter_with_yaml_punctuation_round_trips() -> None:
    """A model-written title like "Pi Agent vs OpenCode: Same Model" is not YAML
    unless quoted - two real pages were unreadable this way (2026-09-16)."""
    page = make_page()
    page.front_matter.title = "Pi Agent vs OpenCode: Same Qwen 3.8 Model"
    page.front_matter.gist = "Stub page: no source material [yet] for #context."
    parsed = parse_page(render_page(page))

    assert parsed.front_matter.title == page.front_matter.title
    assert parsed.front_matter.gist == page.front_matter.gist


def test_an_unreadable_page_reads_as_absent_and_is_logged(store, caplog) -> None:
    """One broken page must not take every answer down (query graph posture);
    lint reports it as an orphan finding, the next compile rewrites it."""
    store.put("wiki/concepts/broken.md",
              b"---\ntitle: Bad: colon\nslug: broken\ntype: concept\n---\n\nbody\n",
              "text/markdown")

    assert read_page(store, "broken") is None
    assert "wiki/concepts/broken.md is unreadable" in caplog.text


def test_sources_section_is_rewritten_not_appended() -> None:
    body = "## Summary\n\ntext\n\n## Sources\n\n- [[sources/old]] - Old\n"
    updated = append_sources_section(body, ["new1", "new2"], {"new1": "First", "new2": "Second"})

    assert updated.count("## Sources") == 1
    assert "old" not in updated
    assert "- [[sources/new1]] - First" in updated


def test_gists_round_trip_through_storage(store) -> None:
    manifest = {"rag": PageGist(slug="rag", title="RAG", gist="Grounding.", version=2)}
    gists_mod.save_gists(store, manifest)

    loaded = gists_mod.load_gists(store)
    assert loaded["rag"].gist == "Grounding."
    assert loaded["rag"].version == 2


def test_missing_manifest_is_an_empty_wiki(store) -> None:
    assert gists_mod.load_gists(store) == {}


def test_index_renders_from_the_manifest_without_reading_pages(spy_store) -> None:
    manifest = {
        "rag": PageGist(slug="rag", title="RAG", type="concept", gist="Grounding."),
        "lewis": PageGist(slug="lewis", title="Lewis et al.", type="entity", gist="Authors."),
    }
    gists_mod.write_index(spy_store, manifest)

    assert not spy_store.gets("wiki/concepts/"), "index rendering must not read page bodies"
    rendered = spy_store.inner.get("wiki/index.md").decode()
    assert "## Concepts" in rendered and "## Entities" in rendered
    assert "- [[rag]] - Grounding." in rendered
    assert "- [[lewis]] - Authors." in rendered


def test_index_sources_display_title_and_keep_source_id() -> None:
    source_id = "ce91b894b9e7bcff"
    rendered = gists_mod.render_index(
        {
            source_id: PageGist(
                slug=source_id,
                title="A Recipe for Training Neural Networks",
                type="source",
                gist="Captured source (web)",
            )
        }
    )
    assert (
        f"- [[{source_id}|A Recipe for Training Neural Networks]] (`{source_id}`) "
        "- Captured source (web)"
    ) in rendered


def test_index_is_stable_for_the_same_manifest() -> None:
    manifest = {"b": PageGist(slug="b", title="B"), "a": PageGist(slug="a", title="A")}
    reversed_manifest = dict(reversed(list(manifest.items())))
    assert gists_mod.render_index(manifest) == gists_mod.render_index(reversed_manifest)
