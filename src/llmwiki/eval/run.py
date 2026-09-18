"""Run the golden set: locally (no LangSmith) or as a LangSmith experiment.

``run_local`` is the offline loop - the same target and evaluators, a table
of rows and a pass/fail summary, no network. ``run_experiment`` is a thin
wrapper over ``langsmith.evaluate`` that adds the metadata which makes two
experiments comparable (git sha, the routes in force, the graph bounds).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.config import settings as default_settings
from llmwiki.eval.dataset import Example
from llmwiki.eval.evaluators import GATED, Evaluator, Score


def answer_target(inputs: dict, cfg: Settings | None = None) -> dict:
    """The function under evaluation: ``tools.answer`` flattened to plain data."""
    result = tools.answer(str(inputs["question"]), cfg=cfg)
    return {
        "text": result.text,
        "citations": [c.source_id for c in result.citations],
        "used_rag_fallback": result.used_rag_fallback,
        "context": result.context,
        "steps": [s.model_dump() for s in result.steps],
        "external_refs": [r.model_dump() for r in result.external_refs],
        "run_id": result.run_id,
    }


@dataclass
class Row:
    """One example's outcome in a local run."""

    example: Example
    outputs: dict
    scores: list[Score] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        """Names of gated evaluators this example scored below threshold on."""
        return [
            s["key"] for s in self.scores
            if s["key"] in GATED and float(s["score"]) < GATED[s["key"]]
        ]


@dataclass
class LocalResult:
    rows: list[Row]

    @property
    def failures(self) -> list[Row]:
        return [row for row in self.rows if row.failed]

    def mean(self, key: str) -> float | None:
        values = [float(s["score"]) for row in self.rows for s in row.scores if s["key"] == key]
        return sum(values) / len(values) if values else None


def run_local(
    examples: list[Example], evaluators: list[Evaluator], cfg: Settings | None = None
) -> LocalResult:
    """Answer every example and score it, in-process."""
    rows: list[Row] = []
    for example in examples:
        outputs = answer_target(example.inputs, cfg=cfg)
        scores = [ev(example.inputs, outputs, example.outputs) for ev in evaluators]
        rows.append(Row(example=example, outputs=outputs, scores=scores))
    return LocalResult(rows=rows)


def experiment_metadata(cfg: Settings | None = None) -> dict[str, Any]:
    """What makes two experiments comparable: code version, routes, bounds."""
    cfg = cfg or default_settings
    from llmwiki import __version__

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    meta: dict[str, Any] = {
        "llmwiki_version": __version__,
        "git_sha": sha,
        "agent_max_tool_calls": cfg.agent_max_tool_calls,
        "agent_web_search_policy": cfg.agent_web_search_policy,
        "llm_provider": cfg.llm_provider,
        "llm_model": cfg.llm_model,
    }
    # The per-op routes in force, as /healthz reports them (tools._config_state).
    routes = tools.health(cfg)["config"].get("routes", {})
    for op in ("answer_query", "agent_step", "judge_answer"):
        if op in routes:
            meta[f"route_{op}"] = routes[op]
    return meta


def run_experiment(
    dataset_name: str,
    evaluators: list[Evaluator],
    *,
    experiment_prefix: str = "llmwiki",
    cfg: Settings | None = None,
) -> Any:
    """Run the golden set as a LangSmith experiment; returns the ``ExperimentResults``."""
    cfg = cfg or default_settings
    cfg.require("langsmith_api_key")
    from langsmith import Client, evaluate

    client = Client(
        api_key=cfg.langsmith_api_key.get_secret_value(),
        api_url=cfg.langsmith_endpoint or None,
    )
    return evaluate(
        lambda inputs: answer_target(inputs, cfg=cfg),
        data=dataset_name,
        evaluators=list(evaluators),
        experiment_prefix=experiment_prefix,
        metadata=experiment_metadata(cfg),
        client=client,
        max_concurrency=1,
    )
