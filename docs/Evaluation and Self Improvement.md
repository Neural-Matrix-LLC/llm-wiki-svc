# Evaluation and Self-Improvement for LLM Agents

**Document purpose:** Overview of how evaluations enable agent self-improvement, common approaches, and a practical comparison of open-source eval layers versus the recommended LangSmith + Phoenix setup for multi-agent systems (such as FUND).

**Last updated:** September 2026

---

## 1. Purpose Overview: How Evals Enable Agent Self-Improvement

Evals turn unpredictable LLM/agent behavior into measurable signals that drive targeted iteration. Without them, “self-improvement” is mostly guesswork. With them, every failure becomes fuel for the next version of the agent.

### Core Idea
- Component-level scores (tool selection, schema compliance, test pass rate)
- Trajectory-level scores (path efficiency, loops, convergence)
- Domain-outcome scores (strategy quality, realistic backtest metrics, end-to-end success)

These scores create a closed feedback loop: measure → diagnose → improve → re-measure.

### Common Approaches

#### 1. Prompt & Skill Refinement (Lowest effort, highest frequency)
- Identify recurring failure patterns from eval results.
- Update system prompts, few-shot examples, or skill files (`SKILL.md`).
- Re-run the same eval suite to quantify improvement.
- Especially effective for Analytic extraction quality and Coding fix-loop behavior.

#### 2. Golden Dataset Expansion + Regression Testing
- Convert successful runs and carefully labeled failures into permanent eval examples.
- Every prompt, model, or skill change is tested against this growing dataset before deployment.
- Failed cases that get fixed become new positive examples; persistent failures remain hard negatives.
- Creates a living test suite that prevents regressions while continuously raising the quality bar.

#### 3. LLM-as-Judge Feedback Loops (Self-Critique)
- After an agent produces an output, a separate judge scores it against the eval rubric.
- The critique is fed back to the original agent for revision.
- Can be single-pass or multi-turn until the score exceeds a threshold.
- Particularly useful for Coding and Analytic agents where intermediate quality can be improved before moving downstream.

#### 4. Preference Data for Fine-Tuning or Alignment
- Create preference pairs: (good vs. mediocre) or (human-corrected vs. original).
- Use for preference optimization (DPO, IPO, etc.), supervised fine-tuning on high-scoring trajectories, or reward model training.
- Even without full fine-tuning, the pairs serve as strong few-shot examples or ranking signals.

#### 5. Automated Prompt Optimization
- Treat the prompt as a searchable object.
- Use frameworks (DSPy-style, evolutionary search, or simple optimization loops) that propose variants, score them on the eval suite, and keep the winners.
- Most effective when a stable, high-quality eval set exists for a specific stage.

#### 6. Production Feedback Flywheel (Online / Continuous)
- Sample real production runs.
- Score them with the same evaluators used in development.
- Surface low-scoring trajectories for review or automatic inclusion in the next training/eval cycle.
- Human-in-the-loop corrections become high-value training signals.

### Practical Flow for Multi-Agent Systems
1. Run offline evals on golden sets after every change.
2. Capture production failures + human HITL corrections.
3. Feed both into prompt/skill updates or preference datasets.
4. Re-evaluate → measure lift → deploy only if scores improve.
5. Periodically expand the golden set with newly solved hard cases.

**Key principle:** Evals close the loop. They make self-improvement systematic rather than hopeful.

---

## 2. Recommended Stack (LangSmith + Phoenix)

**Primary:** LangSmith  
- Already in use.  
- Excellent for experiments, datasets, LangGraph-native trajectory scoring, and regression testing.

**Complement:** Arize Phoenix  
- OpenTelemetry-native tracing.  
- Strong trajectory / path / convergence evals.  
- Production drift detection and span-level scoring.  
- Free self-hosting with no event caps under Elastic License 2.0 (source-available).

This combination provides:
- Fast offline experiments and dataset management (LangSmith)
- Deep multi-step debugging for unpredictable agent behavior (Phoenix)
- Clear path from evaluation results into self-improvement loops

---

## 3. Open-Source Eval Layers – Detailed Comparison

### Top Open-Source / Source-Available Options (2026)

| Framework          | License              | Best For                          | Trajectory / Agent Support                  | Offline / Online          | Key Strengths                                      | Weaknesses for Complex Multi-Agent Systems |
|--------------------|----------------------|-----------------------------------|---------------------------------------------|---------------------------|----------------------------------------------------|--------------------------------------------|
| **DeepEval**       | Apache 2.0           | Pytest-style CI + agent metrics   | Strong (task completion, tool correctness, step efficiency, plan adherence) | Both                     | 50+ metrics, G-Eval, DAG, easy unit-test style    | Less native multi-agent orchestration visibility |
| **Arize Phoenix**  | Elastic 2.0 (source-available) | Observability + trajectory evals | Excellent (path, convergence, tool use)    | Both (self-host free)    | OTel-native, strong debugging of multi-step runs  | License not fully OSI open-source          |
| **Langfuse**       | MIT (core)           | Self-hosted tracing + evals       | Good (multi-turn, custom evaluators)        | Both                     | Full self-host, prompt management, datasets       | Weaker out-of-the-box agent-specific metrics |
| **RAGAS**          | Apache 2.0           | RAG-heavy pipelines               | Partial (tool + goal metrics)               | Mostly offline           | Best-in-class reference-free RAG metrics          | Not designed for complex multi-agent trajectories |
| **Promptfoo**      | MIT                  | Prompt testing + red-teaming      | Limited                                     | Offline + CI             | YAML/CLI declarative, strong security testing     | Less trajectory depth                      |
| **OpenAI Evals**   | MIT                  | Offline benchmark-style grading   | Limited                                     | Offline                  | Large registry of pre-built evals                 | Hosted product retiring; limited modern agent support |
| **Opik (Comet)**   | Apache 2.0           | Tracing + production evals        | Good                                        | Both                     | Full platform self-host, high-volume friendly     | Less specialized agent metrics than DeepEval |

Other notable options: `agentevals` / `openevals` (LangChain MIT helpers), TruLens, AgentCompass, MASEval (more research-oriented).

### Direct Comparison vs. Recommended Stack (LangSmith + Phoenix)

| Aspect                              | LangSmith + Phoenix (Recommended) | DeepEval                  | Langfuse                 | RAGAS                    | Promptfoo / OpenAI Evals |
|-------------------------------------|-----------------------------------|---------------------------|--------------------------|--------------------------|--------------------------|
| Fit with LangGraph / multi-agent    | Excellent (native)                | Very good                 | Good                     | Weak                     | Limited                  |
| Trajectory / process evals          | Strong (both tools)               | Strong                    | Moderate                 | Weak                     | Weak                     |
| Domain outcome metrics              | Custom + LLM-judge easy           | Excellent (built-in)      | Custom needed            | Limited                  | Limited                  |
| CI / regression testing             | Good                              | Best (pytest-native)      | Good                     | Moderate                 | Excellent for prompts    |
| Production / online evals           | Strong (Phoenix)                  | Available via cloud       | Strong (self-host)       | Weak                     | Weak                     |
| Self-host / data control            | Phoenix yes; LangSmith limited    | Fully local possible      | Excellent                | Fully local              | Fully local              |
| Learning curve                      | Low (already using LangSmith)     | Low–medium                | Medium                   | Low (if RAG-focused)     | Low for simple cases     |
| Self-improvement loop support       | Strong                            | Strong                    | Good                     | Moderate                 | Moderate                 |

---

## 4. Practical Recommendation for Multi-Agent Systems (e.g., FUND)

1. **Keep LangSmith** as the primary tool for experiments, datasets, and LangGraph-native scoring (lowest friction).
2. **Add Phoenix** for deeper trajectory analysis, path convergence, and production drift detection — especially valuable for unpredictable multi-step behavior.
3. **Add DeepEval** as the CI gating layer:
   - Pytest-style tests for schema compliance, test-pass rate, and end-to-end quality.
   - Its agent metrics map cleanly onto sub-agents (tool correctness, step efficiency, plan adherence).
4. Use **RAGAS** only if retrieval-heavy components expand significantly.

### Resulting Hybrid Benefits
- Fast offline experiments and dataset management
- Strong trajectory visibility for unpredictable agent behavior
- Pytest-based regression that protects quality when prompts, skills, or models change
- Clear, measurable path from evaluation results into self-improvement (failed cases → golden set → prompt/skill updates → re-evaluation)

---

## 5. Suggested Next Steps

- Create initial golden datasets for key stages (Analytic, Coding, End-to-End).
- Instrument critical agents with Phoenix/OpenTelemetry.
- Implement 4–6 high-signal DeepEval metrics as pytest tests.
- Establish a regular cadence: evaluate → diagnose → update prompts/skills → re-evaluate.
- Capture human-in-the-loop corrections as high-value preference data.

This approach makes self-improvement systematic, measurable, and continuous rather than ad-hoc.

---

*Compiled from evaluation strategy discussions for multi-agent financial research platforms. Adapt metrics and evaluators to your specific domain outcomes (e.g., strategy quality, backtest realism, test pass rates).*