"""Loads the application-specific multi-provider/per-op routing config.

design v1.4 §4.8.1, implement-plan-v1.4.md §19.2. Two plain, committed Python
files at the repo root - ``config/providers.py`` and ``config/ops.py`` -
outside ``src/llmwiki`` entirely, not part of the installed package. Neither
holds a secret: ``providers.py`` names which environment variable carries each
provider's credentials, and this module resolves those variables at load time.

**Absence of ``config/providers.py`` is the fallback switch.** When it is not
present, :func:`load_routing_config` returns ``None`` and ``factory.py`` builds
today's single-provider client from ``Settings``, unchanged. This module is
otherwise inert - importing it does nothing until something calls the loader.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)

# The same file config.py's Settings reads (model_config's env_file=".env").
# Duplicated here, deliberately, rather than shared: Settings only knows its
# own fixed field names, but a provider row's api_key_env/base_url_env names
# an arbitrary variable not known until config/providers.py itself is read -
# see implement-plan-v1.4.md §19.9 item on os.environ access in this slice.
DOTENV_PATH = Path(".env")

# Every op name the codebase actually calls llm.complete(op=...) with.
# tests/unit/test_routing_config.py scans the real call sites (agent/query.py,
# agent/graph.py, agent/judge.py, wiki/compiler.py) and asserts they match
# this set exactly - the guard against a new call site adding an op with no
# config/ops.py row.
KNOWN_OPS = frozenset({
    "summarize_source",
    "plan_compile",
    "create_page",
    "patch_page",
    "answer_query",
    # Phase 1-D (design §4.9): the query graph's per-iteration tool decision -
    # route it to the cheapest model - and the eval-only groundedness judge.
    "agent_step",
    "judge_answer",
})

DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 2048


@dataclass(frozen=True)
class ProviderCreds:
    """One active provider's resolved credentials."""

    provider: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class OpRoute:
    """One operation's resolved provider/model/temperature/max_tokens."""

    op: str
    provider: str
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class RoutingConfig:
    """The fully validated routing table - safe to build adapters from."""

    providers: dict[str, ProviderCreds]
    ops: dict[str, OpRoute]

    def providers_in_use(self) -> set[str]:
        """Providers actually named by an op row - the only ones worth building."""
        return {route.provider for route in self.ops.values()}


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load config module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dotenv_fallback() -> dict[str, str]:
    """The .env file, as a plain dict - never mutates ``os.environ``.

    ``Settings`` (config.py) reads ``.env`` through pydantic-settings, which
    populates only its own model fields and never exports them into the real
    process environment - so a bare ``os.environ.get("OPENROUTER_API_KEY")``
    finds nothing for a key that exists only in ``.env``. ``dotenv_values()``
    (python-dotenv, already a hard dependency) reads the same file without
    that side effect, and returns ``{}`` for a missing file rather than
    raising.
    """
    from dotenv import dotenv_values

    return {key: value for key, value in dotenv_values(DOTENV_PATH).items() if value is not None}


def _env(name: str, dotenv: dict[str, str]) -> str:
    """Real process env wins over ``.env``, matching pydantic-settings' own precedence."""
    return os.environ.get(name) or dotenv.get(name, "")


def _require_list(module: ModuleType, attr: str, path: Path) -> list:
    value = getattr(module, attr, None)
    logger.debug(
        "loaded %s from %s: %d row(s)", attr, path, len(value) if isinstance(value, list) else 0
    )
    if not isinstance(value, list):
        raise RuntimeError(
            f"{path} must define a top-level {attr!r} list; found "
            f"{type(value).__name__ if value is not None else 'nothing'}"
        )
    return value


def _resolve_providers(rows: list, path: Path) -> dict[str, ProviderCreds]:
    from llmwiki.llm import providers as langchain_providers

    known_kinds = {"anthropic", "fake"} | set(langchain_providers.REGISTRY)
    dotenv = _dotenv_fallback()
    active: dict[str, ProviderCreds] = {}
    for row in rows:
        name = row.get("provider")
        if not name:
            raise RuntimeError(f"{path}: every PROVIDERS row needs a 'provider' name")
        if name in active:
            raise RuntimeError(f"{path}: duplicate provider entry {name!r}")
        if name not in known_kinds:
            raise RuntimeError(
                f"{path}: provider {name!r} has no adapter in this package. "
                f"Available: {', '.join(sorted(known_kinds))}."
            )
        api_key_env = row.get("api_key_env")
        api_key = _env(api_key_env, dotenv).strip() if api_key_env else ""
        # Never log the resolved value itself (config.py's SecretStr exists
        # for exactly this reason) - presence/absence only.
        if api_key_env:
            state = "set" if api_key else "unset - inactive"
            logger.debug("provider %s: credentials from %s (%s)", name, api_key_env, state)
        else:
            logger.debug("provider %s: no credentials needed", name)
        if api_key_env and not api_key:
            # Declared but unset/empty - not an error, just inactive.
            continue
        base_url_env = row.get("base_url_env")
        base_url = _env(base_url_env, dotenv).strip() if base_url_env else ""
        active[name] = ProviderCreds(provider=name, api_key=api_key, base_url=base_url)
    return active


def _resolve_ops(
    rows: list, active_providers: dict[str, ProviderCreds], path: Path
) -> dict[str, OpRoute]:
    resolved: dict[str, OpRoute] = {}
    logger.debug("resolving ops from %s, active providers=%s", path, sorted(active_providers))
    for row in rows:
        op = row.get("op")
        if not op:
            raise RuntimeError(f"{path}: every OPS row needs an 'op' name")
        if op in resolved:
            raise RuntimeError(f"{path}: duplicate op entry {op!r}")
        provider = row.get("provider")
        model = row.get("model")
        if not provider or not model:
            raise RuntimeError(f"{path}: op {op!r} needs both 'provider' and 'model'")
        if provider not in active_providers:
            raise RuntimeError(
                f"{path}: op {op!r} routes to provider {provider!r}, which is not active "
                "(its credential env var is unset, or it is missing from config/providers.py)"
            )
        resolved[op] = OpRoute(
            op=op,
            provider=provider,
            model=model,
            temperature=float(row.get("temperature", DEFAULT_TEMPERATURE)),
            max_tokens=int(row.get("max_tokens", DEFAULT_MAX_TOKENS)),
        )

    missing = KNOWN_OPS - resolved.keys()
    if missing:
        raise RuntimeError(
            f"{path}: missing a row for op(s) {sorted(missing)} - every op the codebase "
            "calls llm.complete(op=...) with needs exactly one row"
        )
    return resolved


def load_routing_config(providers_path: Path, ops_path: Path) -> RoutingConfig | None:
    """Load and validate the routing config, or ``None`` in fallback mode.

    Fails loudly, by name, on any config error - same posture as
    ``Settings.require`` - rather than deferring to a confusing failure once a
    call is actually attempted.
    """
    providers_exists = providers_path.exists()
    ops_exists = ops_path.exists()

    if not providers_exists and not ops_exists:
        return None
    if providers_exists != ops_exists:
        present, absent = (
            (providers_path, ops_path) if providers_exists else (ops_path, providers_path)
        )
        raise RuntimeError(
            f"{present} exists but {absent} does not - both files are required together, "
            "or neither (to use the single-provider fallback)"
        )
    logger.debug("loading routing config from %s and %s", providers_path, ops_path)

    providers_rows = _require_list(_load_module(providers_path), "PROVIDERS", providers_path)
    ops_rows = _require_list(_load_module(ops_path), "OPS", ops_path)

    active_providers = _resolve_providers(providers_rows, providers_path)
    ops = _resolve_ops(ops_rows, active_providers, ops_path)
    used = {route.provider for route in ops.values()}
    return RoutingConfig(providers={name: active_providers[name] for name in used}, ops=ops)
