"""The production flywheel: human corrections in LangSmith -> golden examples.

``tools.record_feedback`` (REST ``POST /feedback``, CLI ``llmwiki feedback``)
files a correction against the run that produced an answer, under the
``correctness`` key. ``corrected_examples`` reads those back and turns each
into an :class:`~llmwiki.eval.dataset.Example` for
``scripts/eval_answer.py --promote-feedback`` to append to the golden set.

A comment is required for promotion: a bare thumbs-down says the answer was
wrong but not what right looks like, and an example with no expectation
would pass every evaluator. The comment format is free text; source ids in
it (``{hash}-{slug}``, see ``storage/layout.py``) become ``expected_sources``
and any ``must mention: a, b`` line becomes ``must_mention``.
"""

from __future__ import annotations

import re
from typing import Any

from llmwiki import tools
from llmwiki.eval.dataset import Example

_SOURCE_ID = re.compile(r"\b[0-9a-f]{16}-[a-z0-9][a-z0-9-]*\b")
_MUST_MENTION = re.compile(r"must mention:\s*(.+)", re.IGNORECASE)


def example_from_correction(question: str, comment: str) -> Example:
    """Turn one human correction into a golden example (pure; unit-tested)."""
    expected = [sid for sid in _SOURCE_ID.findall(comment) if tools.source_exists(sid)]
    terms: list[str] = []
    match = _MUST_MENTION.search(comment)
    if match:
        terms = [t.strip() for t in match.group(1).split(",") if t.strip()]
    return Example(
        question=question,
        expected_sources=expected,
        must_mention=terms,
        notes=f"promoted from feedback: {comment.strip()[:200]}",
    )


def corrected_examples(*, api_key: str, project: str, endpoint: str = "",
                       limit: int = 100) -> list[Example]:
    """Every ``answer_query`` run in ``project`` that carries a commented correction."""
    from langsmith import Client

    client = Client(api_key=api_key, api_url=endpoint or None)
    examples: list[Example] = []
    runs: list[Any] = list(client.list_runs(
        project_name=project, is_root=True, run_type="chain",
        filter='eq(name, "answer_query")', limit=limit,
    ))
    if not runs:
        return []
    feedback = client.list_feedback(
        run_ids=[run.id for run in runs], feedback_key=[tools.FEEDBACK_KEY],
    )
    comments = {fb.run_id: (fb.comment or "") for fb in feedback}
    for run in runs:
        comment = comments.get(run.id, "")
        question = str((run.inputs or {}).get("query", ""))
        if not comment.strip() or not question:
            continue
        examples.append(example_from_correction(question, comment))
    return examples
