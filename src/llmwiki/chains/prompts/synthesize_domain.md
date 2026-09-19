---
name: synthesize-domain
description: Write or refresh one domain's overview page - the map of its concepts, entities and open tensions - from the domain's gist manifest and its most-sourced pages.
---

You are writing the overview page of one domain of a compiled research wiki. You are
given every page of the domain as a one-line gist, and the full text of its most
important pages. Your page is the entry point a researcher reads first.

Write the page as markdown with these sections:

- `## What this domain covers` - two or three sentences.
- `## Main threads` - the handful of themes that organize the domain, each with the
  pages that belong to it as `[[slug]]` wikilinks and one sentence on how they relate.
- `## Key entities` - the people, organizations, systems or artifacts that recur, as
  `[[slug]]` links with a phrase each.
- `## Tensions and open questions` - where pages disagree, where evidence is thin, and
  what a new source would most usefully settle. Cite the pages involved.
- `## Where to start` - three to five pages, in reading order, for someone new.

Rules:

- Link only to slugs that appear in the gist list. Never invent a page.
- Say only what the pages say. Do not add outside knowledge.
- The `gist` you return is one sentence, under 200 characters, describing the domain.
