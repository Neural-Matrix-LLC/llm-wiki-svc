---
name: route-domain-query
description: Choose which registered knowledge-base domains a research question should be searched in, fewest first.
---

You are deciding where in a domain-partitioned research knowledge base a question
should be searched. You are given the registered domains with their descriptions and
the question.

Return the domains to search, most relevant first. Rules:

- Include a domain only when the question plainly concerns its subject.
- Include `general` when the question could be answered by material that was never
  filed under a specific domain, or when you are unsure.
- Prefer one domain; return two only when the question genuinely spans both. Never
  return more than the schema allows.
- Never name a domain that is not in the list.
