You are revising an existing page of a research wiki, given the page's current
markdown and a summary of one new source.

Return the **complete revised body** plus an updated `gist`.

Rules:
- Integrate; do not append. A patched page must read as though it were written
  once, not accreted.
- Preserve existing `[[wikilinks]]`, structure, and any claims the new source
  does not contradict.
- If the new source contradicts the page, keep both and mark it:

  > [!warning] Contradiction
  > `[[source-a]]` claims X; `[[source-b]]` claims not-X. Unresolved.

- Do not write a `## Sources` section; the compiler maintains it.
- If the source genuinely adds nothing, return the body unchanged. That is a
  legitimate outcome and costs less than a gratuitous rewrite.
