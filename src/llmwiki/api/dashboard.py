"""``GET /dashboard`` - one server-rendered page of spend, budget, worker and domains.

Phase 2 (design v1.4 §4.10.3, plan §21.2 C6). Plain HTML with inline CSS and
inline SVG bars: no JavaScript, no template engine, no new dependency. Reads
``tools.usage_summary``, ``tools.budget_status``, ``tools.worker_status`` and
``tools.list_domains`` - the same functions the CLI and the JSON routes use -
and only formats them.
"""

from __future__ import annotations

from html import escape

from llmwiki import tools
from llmwiki.models.plan import CostSummary


def _usd(value: float) -> str:
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def _table(title: str, rows: list[tuple[str, float]], total: float) -> str:
    if not rows:
        return f"<h2>{escape(title)}</h2><p class='muted'>nothing yet</p>"
    body = "".join(
        f"<tr><td>{escape(str(key))}</td><td class='num'>{_usd(value)}</td>"
        f"<td class='num muted'>{(value / total * 100 if total else 0):.0f}%</td></tr>"
        for key, value in sorted(rows, key=lambda kv: -kv[1])
    )
    return (f"<h2>{escape(title)}</h2><table><thead><tr><th></th><th class='num'>USD</th>"
            f"<th class='num'>share</th></tr></thead><tbody>{body}</tbody></table>")


def _bars(by_day: dict[str, float]) -> str:
    """Spend per day as an inline SVG bar chart (last 31 days present in the window)."""
    days = sorted(by_day)[-31:]
    if not days:
        return "<p class='muted'>no spend in this window</p>"
    peak = max(by_day[day] for day in days) or 1.0
    width, height, gap = 18, 120, 4
    total_w = len(days) * (width + gap)
    bars = []
    for i, day in enumerate(days):
        value = by_day[day]
        h = max(1, round(value / peak * (height - 20)))
        x = i * (width + gap)
        bars.append(
            f"<rect x='{x}' y='{height - h}' width='{width}' height='{h}' rx='2'>"
            f"<title>{escape(day)}: {_usd(value)}</title></rect>"
        )
    labels = "".join(
        f"<text x='{i * (width + gap) + width / 2}' y='{height + 12}' text-anchor='middle'>"
        f"{escape(day[-2:])}</text>"
        for i, day in enumerate(days)
    )
    return (f"<svg viewBox='0 0 {total_w} {height + 16}' width='{min(total_w, 900)}' "
            f"height='{height + 16}' class='bars' role='img' aria-label='spend per day'>"
            f"{''.join(bars)}{labels}</svg>")


def render(month: str | None = None) -> str:
    summary: CostSummary = tools.usage_summary(month=month)
    budget = tools.budget_status()
    worker = tools.worker_status()
    domains = tools.list_domains()
    health = tools.health()
    window = month or budget["month"]

    def line(label: str, value: object) -> str:
        return f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>"

    thresholds = "".join(
        line(name, _usd(float(value)) if float(value) > 0 else "off")
        for name, value in (
            ("daily alert", budget["daily_alert_usd"]),
            ("monthly alert", budget["monthly_alert_usd"]),
            ("monthly hard cap", budget["hard_cap_usd"]),
        )
    )
    cap_state = ("<strong class='bad'>PAUSED - hard cap reached</strong>" if budget["capped"]
                 else "<span class='ok'>within budget</span>")
    worker_rows = (
        line("mode", worker.mode) + line("queued", sum(worker.queued.values()))
        + line("in flight", ", ".join(worker.in_flight) or "-")
        + line("parked", ", ".join(worker.parked) or "-")
        + (line("reason", worker.reason) if worker.reason else "")
    )
    domain_rows = "".join(
        f"<tr><td>{escape(d.name)}</td><td>{escape(d.description)}</td>"
        f"<td class='num'>{_usd(summary.by_domain.get(d.name, 0.0))}</td></tr>"
        for d in domains
    )
    top_sources = "".join(
        f"<tr><td><code>{escape(sid)}</code></td><td class='num'>{_usd(usd)}</td></tr>"
        for sid, usd in summary.top_sources
    ) or "<tr><td class='muted' colspan='2'>none</td></tr>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>llmwiki usage - {escape(window)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --fg:#1b1b1b; --muted:#6b6b6b; --bg:#fff; --line:#e4e4e4; --bar:#3b6ea5; --ok:#1e7d3a; --bad:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#eaeaea; --muted:#9a9a9a; --bg:#151515; --line:#2e2e2e; --bar:#7aa7d8; --ok:#5fd07f; --bad:#ff6b5e; }} }}
body {{ font: 15px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); margin: 0; padding: 24px 16px; max-width: 980px; margin-inline: auto; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 28px 0 8px; }}
.muted {{ color: var(--muted); }} .ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 16px; }}
.tile {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }}
.tile .big {{ font-size: 26px; font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font-weight: 600; color: var(--muted); }} td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.bars rect {{ fill: var(--bar); }} .bars text {{ font-size: 9px; fill: var(--muted); }}
code {{ font-size: 13px; }} nav a {{ margin-right: 12px; }}
</style></head><body>
<h1>llmwiki usage <span class="muted">{escape(window)}</span></h1>
<p class="muted">llmwiki {escape(str(health['version']))} · llm {escape(str(health['backends']['llm']))}
 · lexical {escape(str(health['backends']['lexical']))} · reranker {escape(str(health['backends']['reranker']))}
 · {cap_state}</p>
<div class="grid">
 <div class="tile"><div class="muted">month to date</div><div class="big">{_usd(float(budget['month_usd']))}</div></div>
 <div class="tile"><div class="muted">today</div><div class="big">{_usd(float(budget['day_usd']))}</div></div>
 <div class="tile"><div class="muted">calls in window</div><div class="big">{summary.call_count}</div></div>
 <div class="tile"><div class="muted">tokens in / out</div><div class="big">{summary.input_tokens:,} / {summary.output_tokens:,}</div></div>
</div>
<h2>Spend per day</h2>{_bars(summary.by_day)}
<h2>Budget</h2><table><tbody>{thresholds}</tbody></table>
<h2>Ingest worker</h2><table><tbody>{worker_rows}</tbody></table>
{_table("By domain", list(summary.by_domain.items()), summary.total_usd)}
{_table("By kind", list(summary.by_kind.items()), summary.total_usd)}
{_table("By operation", list(summary.by_op.items()), summary.total_usd)}
{_table("By model", list(summary.by_model.items()), summary.total_usd)}
<h2>Top sources</h2><table><thead><tr><th>source</th><th class="num">USD</th></tr></thead><tbody>{top_sources}</tbody></table>
<h2>Domains</h2><table><thead><tr><th>name</th><th>description</th><th class="num">spend</th></tr></thead><tbody>{domain_rows}</tbody></table>
<p class="muted">JSON: <code>GET /usage?month={escape(window)}</code> · <code>GET /worker</code> · <code>GET /domains</code></p>
</body></html>"""
