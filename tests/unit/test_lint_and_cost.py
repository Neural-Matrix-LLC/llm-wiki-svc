"""Global lint findings and cost-ledger aggregation."""

from __future__ import annotations

from llmwiki.models.page import PageFrontMatter, PageGist, WikiPage
from llmwiki.models.plan import CostRecord
from llmwiki.storage.layout import COST_KEY, wiki_page
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.lint import lint_wiki
from llmwiki.wiki.pages import render_page


def put_page(store, slug: str, body: str = "## Summary\n\ntext\n") -> None:
    page = WikiPage(
        front_matter=PageFrontMatter(title=slug.title(), slug=slug, gist=f"About {slug}."),
        body=body,
    )
    store.put(wiki_page(slug, "concept"), render_page(page).encode(), "text/markdown")


def test_clean_wiki_has_no_findings(store) -> None:
    put_page(store, "rag")
    gists_mod.save_gists(store, {"rag": PageGist(slug="rag", title="RAG", gist="Grounding.")})

    report = lint_wiki(store)
    assert report.findings == []
    assert report.page_count == 1


def test_page_absent_from_the_manifest_is_an_orphan(store) -> None:
    put_page(store, "rag")
    gists_mod.save_gists(store, {})

    report = lint_wiki(store)
    assert [f.kind for f in report.findings] == ["orphan"]


def test_manifest_row_without_a_page_is_stale(store) -> None:
    gists_mod.save_gists(store, {"ghost": PageGist(slug="ghost", title="Ghost", gist="g")})

    report = lint_wiki(store)
    assert any(f.kind == "stale_source" and f.slug == "ghost" for f in report.findings)


def test_dangling_wikilink_is_reported(store) -> None:
    put_page(store, "rag", "## Summary\n\nSee [[nowhere]] for more.\n")
    gists_mod.save_gists(store, {"rag": PageGist(slug="rag", title="RAG", gist="g")})

    report = lint_wiki(store)
    assert any(f.kind == "dangling_link" for f in report.findings)


def test_missing_gist_is_reported(store) -> None:
    put_page(store, "rag")
    gists_mod.save_gists(store, {"rag": PageGist(slug="rag", title="RAG", gist="")})

    report = lint_wiki(store)
    assert any(f.kind == "missing_gist" for f in report.findings)


def test_fix_removes_stale_rows_but_never_invents_pages(store) -> None:
    put_page(store, "rag", "## Summary\n\nSee [[nowhere]].\n")
    gists_mod.save_gists(store, {
        "rag": PageGist(slug="rag", title="RAG", gist="g"),
        "ghost": PageGist(slug="ghost", title="Ghost", gist="g"),
    })

    report = lint_wiki(store, dry_run=False)

    assert "ghost" not in gists_mod.load_gists(store)
    assert not store.exists(wiki_page("nowhere", "concept")), (
        "lint must not create a stub page to satisfy a dangling link"
    )
    assert any("index.md" in line for line in report.repaired)


def test_cost_summary_aggregates_by_model(store) -> None:
    from llmwiki import tools
    from llmwiki.config import Settings

    records = [
        CostRecord(op="summarize_source", model="claude-haiku-4-5", input_tokens=1000,
                   output_tokens=100, cost_usd=0.0015),
        CostRecord(op="patch_page", model="claude-sonnet-5", input_tokens=2000,
                   output_tokens=200, cost_usd=0.006, cache_read_tokens=500),
    ]
    store.put(COST_KEY, ("\n".join(r.model_dump_json() for r in records) + "\n").encode())

    cfg = Settings(_env_file=None, storage_backend="local",
                   local_storage_path=store.root, vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake")
    from llmwiki import factory

    factory.reset()
    summary = tools.cost_summary(cfg=cfg)
    factory.reset()

    assert summary.call_count == 2
    assert round(summary.total_usd, 5) == 0.0075
    assert set(summary.by_model) == {"claude-haiku-4-5", "claude-sonnet-5"}
    assert summary.cache_read_tokens == 500


def test_malformed_ledger_lines_are_skipped_not_fatal(store) -> None:
    from llmwiki.wiki.compiler import read_cost_ledger

    store.put(COST_KEY, b'{"op":"a","model":"m"}\nnot json at all\n\n')
    assert len(read_cost_ledger(store)) == 1
