---
name: route-domain-source
description: File one newly captured source under exactly one registered knowledge-base domain, or under general when nothing registered fits - never invent a domain.
---

You are filing a newly captured source into a research knowledge base that is
partitioned into domains. You are given the registered domains, each with a one-line
description, and the source's title and the opening of its extracted text.

Choose the single domain the source belongs to. Rules:

- Pick a registered domain only when the source is clearly about that domain's subject.
  Weak or partial overlap is not enough - choose `general`.
- `general` is always available and is the right answer whenever you are unsure.
- Never invent a domain name as the answer. If the source clearly deserves a domain that
  does not exist yet, answer `general` and put the name you would have used in
  `suggested_domain` (short, lowercase, hyphenated). Leave `suggested_domain` empty
  otherwise.
- `confidence` is your probability, from 0 to 1, that the chosen domain is right.
- `reason` is one short sentence.
