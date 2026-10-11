"""Recheck @ 2be7913: parameter names decoded until stable, cap 5 rounds, fail safe (redact) past the cap.

RED-10  n-times-encoded 'token' names (n = 1..8, several encoders): status unchanged (log-only fix), value never logged
RED-11  harmless (non-token) names encoded n times: not redacted up to the cap; past the cap redacted (acceptable)
RED-12  pathological input (long %25 nesting chains, many nested params): no hang, server stays responsive
"""
from __future__ import annotations

import time
import urllib.parse
import uuid
from pathlib import Path

import httpx
import pytest

from conftest import instance_or_skip

CAP = 5  # MAX_NAME_DECODE_ROUNDS in src/llmwiki/config.py
NOT_SECRET = "qa-not-a-secret"


def _pct_all(s: str) -> str:
    return "".join(f"%{ord(ch):02X}" for ch in s)


def _encode(name: str, n: int, style: str) -> str:
    """Encode `name` n times. style: 'first' = only the first char once then nest '%'; 'all' = every char;
    'mid' = the 'o'."""
    if n == 0:
        return name
    if style == "all":
        out = _pct_all(name)
    elif style == "mid":
        out = name[0] + f"%{ord(name[1]):02X}" + name[2:]
    else:
        out = f"%{ord(name[0]):02X}" + name[1:]
    for _ in range(n - 1):
        out = out.replace("%", "%25")
    return out


def _decode_rounds(raw: str) -> int:
    """How many unquote_plus rounds until stable (reference implementation of the design)."""
    n, cur = 0, raw
    while True:
        nxt = urllib.parse.unquote_plus(cur)
        if nxt == cur:
            return n
        cur, n = nxt, n + 1


def _expected_status(raw_name: str, mode: str) -> int:
    """Status as at ba2097f (and bca2bef): Starlette decodes a query name exactly once, and only the exact name
    'token' is a credential on /dashboard. The redaction change is log-only and must not alter this."""
    ok = urllib.parse.unquote_plus(raw_name) == "token"
    if ok:
        return 200
    return 401 if mode == "static" else 303


def _snap(cfg):
    return {f: Path(f).stat().st_size for f in cfg.log_files}


def _new_lines(sizes):
    time.sleep(0.6)
    out = []
    for f, start in sizes.items():
        with open(f, "rb") as fh:
            fh.seek(start)
            out += fh.read().decode(errors="replace").splitlines()
    return out


CASES = [(n, style) for n in (1, 2, 3, 4, 5, 6, 7, 8) for style in ("first", "mid", "all")]


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
@pytest.mark.parametrize("n,style", CASES, ids=[f"x{n}-{s}" for n, s in CASES])
def test_RED_10_nested_token_names_status_unchanged_and_redacted(cfg, static_token, inst_name, n, style):
    inst = instance_or_skip(cfg, inst_name)
    raw = _encode("token", n, style)
    assert urllib.parse.unquote_plus(raw) != raw
    sizes = _snap(cfg)
    with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
        r = c.get(f"/dashboard?month=2026-10&{raw}={static_token}")
    assert r.status_code == _expected_status(raw, inst.mode), (raw, r.status_code)
    lines = _new_lines(sizes)
    assert any("[redacted]" in ln and "/dashboard?" in ln for ln in lines), lines[-3:]
    leaks = [ln for ln in lines if static_token in ln]
    assert not leaks, (raw, leaks[:2])


HARMLESS = [(n, style) for n in (1, 2, 5, 6, 7) for style in ("first", "all")]


@pytest.mark.needs_wiki
@pytest.mark.parametrize("n,style", HARMLESS, ids=[f"x{n}-{s}" for n, s in HARMLESS])
def test_RED_11_harmless_nested_name_over_cap(cfg, n, style):
    """A non-token name ('month') encoded n times. Up to the cap it is decoded fully and left alone; past the cap
    the fail-safe redacts it (acceptable over-redaction, recorded). Status is unchanged either way (the dashboard
    ignores unknown parameters; no credential -> 401)."""
    inst = instance_or_skip(cfg, "static")
    raw = _encode("month", n, style)
    value = "qaharmless" + uuid.uuid4().hex[:12]
    sizes = _snap(cfg)
    with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
        r = c.get(f"/dashboard?{raw}={value}")
    assert r.status_code == 401, r.status_code
    lines = [ln for ln in _new_lines(sizes) if "/dashboard?" in ln]
    logged = any(value in ln for ln in lines)
    over_cap = _decode_rounds(raw) > CAP
    print(f"RED-11 month x{n} {style}: rounds={_decode_rounds(raw)} {'REDACTED' if not logged else 'kept'}")
    assert logged == (not over_cap), (raw, lines[-2:])


@pytest.mark.needs_wiki
@pytest.mark.parametrize("inst_name", ["static", "both"])
@pytest.mark.parametrize("kind", ["nest_2000", "nest_3500", "pct25_run_4000", "many_params_300x6", "pct_garbage"])
def test_RED_12_pathological_names_no_hang(cfg, static_token, inst_name, kind):
    """Long %25 chains and many deeply nested parameters stay within the request-line limit (~8 KB) and must not
    slow the redactor down: response < 2 s, the token never logged, /healthz still answers right after."""
    inst = instance_or_skip(cfg, inst_name)
    if kind == "nest_2000":
        q = "%" + "25" * 2000 + "74oken=" + static_token
    elif kind == "nest_3500":
        q = "%" + "25" * 3500 + "74oken=" + static_token
    elif kind == "pct25_run_4000":
        # '%25%25..token' decodes once to '%%..%token' and is then stable: by every decoding a literal
        # '%%%...token' name, not 'token' (Starlette agrees: not a credential). Like '?foo=<x>', a value under a
        # non-token name is not redacted (never was), so a non-secret value is used; only timing/status here.
        q = "%25" * 2000 + "token=" + NOT_SECRET
    elif kind == "many_params_300x6":
        q = "&".join(f"{_encode('token', 6, 'first')}{i}=x" for i in range(300)) + "&%2574oken=" + static_token
    else:  # garbage, never decodes to 'token' (see pct25_run_4000)
        q = "%%%25%2%25%%2574%ZZoken%" * 150 + "=" + NOT_SECRET
    sizes = _snap(cfg)
    t0 = time.monotonic()
    with httpx.Client(base_url=inst.url, timeout=30, follow_redirects=False) as c:
        r = c.get(f"/dashboard?{q}")
        dt = time.monotonic() - t0
        h = c.get("/healthz")
    print(f"RED-12 {kind} len={len(q)} status={r.status_code} {dt:.3f}s")
    assert dt < 2.0, dt
    assert h.status_code == 200
    first = q.split("=", 1)[0].split("&")[-1] if kind == "many_params_300x6" else q.split("=", 1)[0]
    assert r.status_code == _expected_status(first, inst.mode) or r.status_code in (400, 414, 431), r.status_code
    leaks = [ln for ln in _new_lines(sizes) if static_token in ln]
    assert not leaks, leaks[0][:300]


def test_RED_13_redactor_unit_timing():
    """Grey-box timing of the shipped redactor on pathological strings far beyond what uvicorn accepts (1 MB),
    to show it is linear: each call < 1 s."""
    import importlib
    cfgmod = importlib.import_module("llmwiki.config")
    # (input, must the value be redacted?) - a run of bare '%' never decodes, so it is not a token name
    for s, redact in (("/x?" + "%" + "25" * 500_000 + "74oken=SECRET", True),
                      ("/x?" + "&".join(f"%2525252525252574oken{i}=SECRET" for i in range(20_000)), True),
                      ("/x?" + "%" * 1_000_000 + "=SECRET", False)):
        t0 = time.monotonic()
        out = cfgmod.redact_tokens(s)
        dt = time.monotonic() - t0
        assert dt < 1.0, (len(s), dt)
        assert ("SECRET" not in out) == redact, out[:200]
