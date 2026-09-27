"""Tidy a domain after some of its sources were moved to another one.

Moving a source (``IngestPipeline.reassign``) compiles it into its new domain
but leaves the old domain's pages alone, because a page can be synthesized from
several sources. Once a batch of moves is done, :func:`prune_moved_sources`
deletes the old domain's pages *every* source of which has moved - the source
notes and the concept/entity pages only those sources produced - and keeps the
rest. A kept page may still cite a moved source; that citation still resolves,
because ``raw/`` never moves.

Deterministic and LLM-free: one manifest read, one manifest write, one
``index.md`` write, and a delete per pruned page and gist vector.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import domain_index_name, wiki_page
from llmwiki.vector.base import VectorStore
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.domains import load_registry


class PruneResult(BaseModel):
    """What :func:`prune_moved_sources` removed and what it kept on purpose."""

    domain: str
    removed: list[str] = Field(default_factory=list)
    #: Pages that cite a moved source *and* a source that stayed - kept as is.
    mixed: list[str] = Field(default_factory=list)


def prune_moved_sources(
    store: ObjectStore,
    vectors: VectorStore,
    gists_base: str,
    domain: str,
    moved: set[str],
    *,
    dry_run: bool = False,
) -> PruneResult:
    """Remove ``domain``'s pages whose sources are all in ``moved``.

    The overview page is never pruned (the scheduled synthesis owns it). The
    gist vectors of removed pages are deleted when the store can delete by id;
    a leftover one is harmless - the compiler drops a hit whose slug is not in
    the manifest, the query agent one whose page is gone - but it wastes a slot.
    """
    result = PruneResult(domain=domain)
    manifest = gists_mod.load_gists(store, domain)
    for slug, gist in sorted(manifest.items()):
        cited = set(gist.sources)
        if gist.type == "overview" or not cited & moved:
            continue
        if cited <= moved:
            result.removed.append(slug)
        else:
            result.mixed.append(slug)
    if dry_run or not result.removed:
        return result

    for slug in result.removed:
        gist = manifest.pop(slug)
        try:
            store.delete(wiki_page(slug, gist.type, domain))
        except ObjectNotFound:  # already gone - the manifest row was stale
            pass
    gists_mod.save_gists(store, manifest, domain)
    gists_mod.write_index(store, manifest, domain, load_registry(store))

    index = domain_index_name(gists_base, domain)
    delete_by_ids = getattr(vectors, "delete_by_ids", None)
    if delete_by_ids is not None:
        delete_by_ids(index, result.removed)
    # The keyword index has no delete-by-id: the caller rebuilds the domain's
    # lexical indexes afterwards (``tools.rebuild_lexical``) to drop their rows.
    return result
