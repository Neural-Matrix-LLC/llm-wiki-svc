---
name: create-page
description: Write a new Obsidian-flavored wiki page (and its gist) for a concept or entity with no existing page.
---

You are writing a new page for a research wiki that is read by both humans and
agents. Output is Obsidian-flavored markdown.

Structure:
- `## Summary` - 2-4 sentences a reader can stop at.
- `## Details` - the substance, in prose and lists. Link related concepts with
  `[[slug]]` wikilinks where a link would genuinely help.
- Do not write a `## Sources` section; the compiler appends it.

Also return `gist`: one sentence, under 200 characters, that will represent this
page in the index. The gist is how the compiler decides, later, whether a future
source belongs here - write it to be discriminating, not decorative.

Write only what the provided source material supports. No filler, no "in
conclusion", no restating the title as a first sentence.
