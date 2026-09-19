---
name: describe-image
description: Turn one image - a scanned page, a figure-heavy page or an uploaded picture - into faithful markdown - transcribed text, tables as tables, figures described with their numbers - so it can be indexed and compiled like any text source.
---

You are converting one image from a research source into markdown that will be stored,
searched and compiled in place of the image. Be complete and faithful; never speculate.

Produce, in this order and only the parts that apply:

1. **Transcribed text.** Every piece of readable text, verbatim, in reading order, keeping
   headings as markdown headings and lists as lists. Do not summarize or correct it.
2. **Tables.** Each table as a markdown table with its header row. If a cell is
   unreadable write `?`.
3. **Figures, charts and diagrams.** One `### Figure` block each: what it shows, the axes
   and units, the series or components, the trend or relationship, and every number,
   label or annotation that is legible. Quote numbers exactly as printed.
4. **Captions and footnotes**, verbatim.

If the image contains no readable content at all, write one line saying so. Do not add
an introduction, a conclusion, or any content that is not in the image.
