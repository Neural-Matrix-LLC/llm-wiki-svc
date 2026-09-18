"""The golden set: a JSONL file in the repo, a pushed copy in LangSmith.

One example per line::

    {"question": "...", "expected_sources": ["<source_id>", ...],
     "must_mention": ["term", ...], "notes": "why this example exists"}

The repo file is the source of truth (decision D10): it is diffed, reviewed
and shipped in the image; ``push_dataset`` mirrors it to LangSmith so
experiments can run against it. ``tests/fixtures/eval/answer_quality.jsonl``
is the shipped sample, written over the documents ``scripts/smoke_flow.py
--offline`` ingests, so the whole loop runs with no keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

FIXTURE_DATASET = Path("tests/fixtures/eval/answer_quality.jsonl")


class Example(BaseModel):
    """One golden example. ``inputs``/``outputs`` are the LangSmith halves."""

    question: str
    expected_sources: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def inputs(self) -> dict:
        return {"question": self.question}

    @property
    def outputs(self) -> dict:
        return {"expected_sources": self.expected_sources, "must_mention": self.must_mention}


class DatasetError(ValueError):
    """A malformed golden-set line, reported with its line number."""


def load_examples(path: Path) -> list[Example]:
    """Parse a JSONL golden set. Blank lines and ``#`` comment lines are skipped."""
    examples: list[Example] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            examples.append(Example.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(f"{path}:{lineno}: {exc}") from exc
    return examples


def append_examples(path: Path, examples: list[Example]) -> int:
    """Append examples as JSONL lines; returns how many were written."""
    with path.open("a", encoding="utf-8") as fh:
        for example in examples:
            fh.write(example.model_dump_json() + "\n")
    return len(examples)


def push_dataset(examples: list[Example], name: str, *, api_key: str, endpoint: str = "") -> str:
    """Mirror the golden set to a LangSmith dataset (created if absent).

    Existing examples in the dataset are replaced wholesale so the pushed
    copy always equals the file. Returns the dataset id.
    """
    from langsmith import Client

    client = Client(api_key=api_key, api_url=endpoint or None)
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        existing = [ex.id for ex in client.list_examples(dataset_id=dataset.id)]
        if existing:
            client.delete_examples(example_ids=existing)
    else:
        dataset = client.create_dataset(
            dataset_name=name,
            description="llmwiki answer-quality golden set (pushed by scripts/eval_answer.py)",
        )
    client.create_examples(
        dataset_id=dataset.id,
        examples=[{"inputs": ex.inputs, "outputs": ex.outputs, "metadata": {"notes": ex.notes}}
                  for ex in examples],
    )
    return str(dataset.id)
