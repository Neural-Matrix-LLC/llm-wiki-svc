"""The only module in the package that reads the environment.

Everything else receives a :class:`Settings` instance.  Secrets are typed as
``SecretStr`` so a stray log line or repr cannot leak a key.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s"
_LOG_DATEFMT = "%H:%M:%S"
_logging_configured = False

# PRE-02 / F-R2-02: ``/dashboard?token=<INGEST_API_TOKEN>`` (and any ``*_token=``) must not reach
# the logs. Parameters are matched by their percent-decoded name (``%74oken`` is ``token`` to
# Starlette, so it must be to us too), case-insensitively, anywhere in the query string. Names are
# decoded repeatedly (``%2574oken`` -> ``%74oken`` -> ``token``) so a proxy or client that decodes
# twice cannot slip a token past us; see _decoded_name for the cap.
_QUERY = re.compile(r"\?([^\s\"'#]*)")  # a query string inside a log line
_PARAM_SPLIT = re.compile(r"([&;])")
REDACTED = "[redacted]"


#: Decoding rounds for a parameter name. Real clients encode at most once or twice.
MAX_NAME_DECODE_ROUNDS = 5


def _decoded_name(raw_name: str) -> tuple[str, bool]:
    """``(name, capped)``: ``raw_name`` run through ``unquote_plus`` until it stops changing,
    at most :data:`MAX_NAME_DECODE_ROUNDS` times. ``capped`` is True when it was still changing
    after the last round (nested deeper than the cap)."""
    name = raw_name
    for _ in range(MAX_NAME_DECODE_ROUNDS):
        decoded = urllib.parse.unquote_plus(name)
        if decoded == name:
            return name, False
        name = decoded
    return name, urllib.parse.unquote_plus(name) != name


def _is_token_name(raw_name: str) -> bool:
    """token / *_token after decoding (case-insensitive; .strip() errs on the side of redacting).

    Fail safe at the cap: a name still encoded after MAX_NAME_DECODE_ROUNDS rounds (still
    contains '%' and would decode further) is treated as a token name and its value redacted.
    """
    name, capped = _decoded_name(raw_name)
    if capped and "%" in name:
        return True
    name = name.strip().lower()
    return name == "token" or name.endswith("_token")


def _redact_query(query: str) -> str:
    parts = _PARAM_SPLIT.split(query)  # separators kept at odd indexes
    for i in range(0, len(parts), 2):
        name, sep, _value = parts[i].partition("=")
        if sep and _is_token_name(name):
            parts[i] = f"{name}={REDACTED}"  # the raw name is kept, only the value goes
    return "".join(parts)


def redact_tokens(text: str) -> str:
    """``...?token=secret&month=x`` -> ``...?token=[redacted]&month=x``; also ``%74oken=``,
    ``Access_Token=``, repeated parameters. Everything else in ``text`` is left byte for byte."""
    if "?" not in text:
        return text
    return _QUERY.sub(lambda m: "?" + _redact_query(m.group(1)), text)


class TokenRedactingFilter(logging.Filter):
    """Redacts token query parameters in a log record (uvicorn's access log puts the full path,
    query string included, in ``record.args``). Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # with args, msg is a format string (uvicorn's AccessFormatter unpacks record.args): leave
        # it alone so placeholders and arg count stay valid, and redact the args instead
        if isinstance(record.msg, str) and not record.args:
            record.msg = redact_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_tokens(a) if isinstance(a, str) else a
                                for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact_tokens(v) if isinstance(v, str) else v
                           for k, v in record.args.items()}
        return True


#: Loggers that print request URLs: uvicorn's access log (and its error log, for safety).
_REDACTED_LOGGERS = ("uvicorn.access", "uvicorn.error")


def install_token_redaction() -> None:
    """Attach :class:`TokenRedactingFilter` to uvicorn's loggers once (idempotent).

    uvicorn configures its own loggers before it imports the app; ``api/app.py`` calls
    :func:`configure_logging` at import, which calls this, so the filter is in place before the
    first request whichever way uvicorn was started (CLI, ``llmwiki serve``, Docker CMD).
    """
    for name in _REDACTED_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, TokenRedactingFilter) for f in target.filters):
            target.addFilter(TokenRedactingFilter())


def configure_logging(level: str = "INFO") -> None:
    """Attach one stream handler to the root logger, at ``level``.

    Lives here rather than in its own module because the transport layers
    (api/, cli.py) may only import ``tools``/``models``/``config``/``wiki``/
    ``factory`` (``test_layering.py::test_transport_layer_only_calls_tools``)
    - both call this once, at process start, with ``Settings.log_level``.

    Idempotent: a second call (a second ``Settings`` built by a test, or
    ``serve --reload`` re-importing the app) only adjusts the level, it never
    stacks a second handler or duplicates log lines.
    """
    global _logging_configured
    resolved = getattr(logging, level.upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"LOG_LEVEL={level!r} is not a recognized logging level")

    install_token_redaction()
    root = logging.getLogger()
    root.setLevel(resolved)
    if not _logging_configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        root.addHandler(handler)
        _logging_configured = True
    else:
        for existing_handler in root.handlers:
            existing_handler.setLevel(resolved)


# Providers the generic LLM contract accepts. "anthropic" has a native adapter
# (prompt caching, measured cost); the rest are reached through LangChain and
# need their extra installed - see llmwiki.llm.providers.REGISTRY, which must
# stay in step with this list. "fake" is the offline double.
# See implement-plan.md Part II §7.5 and 7.6.
Provider = Literal[
    "anthropic",
    "fake",
    "openai",
    "vllm",
    "llamacpp",
    "google",
    "nvidia",
    "deepseek",
    "openrouter",
]


#: Minimum INGEST_API_TOKEN length in AUTH_MODE=sso|both (design v1.1 item 6); static only warns.
MIN_STATIC_TOKEN_CHARS = 24


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM (provider-generic: the agentkit-llm env contract, plan II §7.6) ---
    llm_provider: Provider = "anthropic"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-haiku-4-5"
    llm_base_url: str = ""
    # Applied uniformly by every adapter (native Anthropic and every
    # LangChain-routed provider) and passed explicitly at every call site -
    # same idiom as llm_model, never a hidden per-adapter default.
    llm_max_tokens: int = 2048
    llm_temperature: float = 1.0

    # --- Application-specific LLM routing (design v1.4 §4.8, plan §19) --------
    # Deliberately outside the shared agentkit-llm contract above: multi-
    # provider credentials and per-op model routing are this application's
    # concern, not the reusable LLM layer's. Absence of the *file* at
    # llm_providers_config (not of this setting) is what selects the
    # single-provider fallback path built from the fields above - see
    # factory._build_llm_client. Replaces the old, single-purpose
    # COMPILE_EXECUTOR_MODEL: a stronger model for create_page/patch_page is
    # now just those ops' own rows in config/ops.py.
    #
    # ``validation_alias`` is required here: pydantic-settings would otherwise
    # auto-derive the env var name from the field name alone
    # (LLM_PROVIDERS_CONFIG, no "WIKI") - the deliberately-prefixed
    # LLMWIKI_PROVIDERS_CONFIG/LLMWIKI_OPS_CONFIG documented in .env.example
    # and implement-plan.md Part II §19.8 would otherwise be silently ignored.
    # The plain field name is kept as a second alias so direct construction
    # (``Settings(llm_providers_config=...)``, used throughout the test suite)
    # is unaffected.
    llm_providers_config: Path = Field(
        default=Path("./config/providers.py"),
        validation_alias=AliasChoices("LLMWIKI_PROVIDERS_CONFIG", "llm_providers_config"),
    )
    llm_ops_config: Path = Field(
        default=Path("./config/ops.py"),
        validation_alias=AliasChoices("LLMWIKI_OPS_CONFIG", "llm_ops_config"),
    )

    # Query-agent skill discovery directory (design v1.4 §4.8.2, plan §19.5),
    # outside src/llmwiki like the two paths above. Not yet read by any code
    # path - lands with R5; declared now so the location is locked in .env
    # rather than improvised later.
    agent_skills_dir: Path = Field(default=Path("./skills"))

    # --- Query graph bounds (Phase 1-D, design §4.9) -------------------------
    # The query agent is a bounded ReAct loop (agent/graph.py). Every bound is
    # enforced in code, never by the prompt: 0 disables the tool loop entirely
    # and reproduces the pre-graph single-call sequence exactly.
    agent_max_tool_calls: int = 4
    # Whether the model is ever *offered* the search_web tool: "off" (never;
    # no key needed), "weak" (only when the wiki had no strong hit and the
    # chunk fallback ran), "always". Gate is applied when the action schema is
    # built, so the model cannot pick a tool the policy withholds.
    agent_web_search_policy: Literal["off", "weak", "always"] = "off"
    agent_max_web_searches: int = 1

    # --- Web search backend (Phase 1-D) -----------------------------------
    # "none" builds no searcher at all (the tool is not even constructed);
    # "tavily" needs TAVILY_API_KEY; "fake" is the offline double.
    web_search_backend: Literal["none", "tavily", "fake"] = "none"
    tavily_api_key: SecretStr = SecretStr("")

    # --- Cloudflare ---
    cf_account_id: str = ""
    cf_api_token: SecretStr = SecretStr("")
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "llmwiki"
    r2_endpoint_url: str = ""
    vectorize_chunks_index: str = "llmwiki-chunks"
    vectorize_gists_index: str = "llmwiki-gists"
    embedding_model: str = "@cf/baai/bge-base-en-v1.5"
    embedding_dim: int = 768

    # --- Service ---
    ingest_api_token: SecretStr = SecretStr("changeme")
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Authentication (docs/sso-integration-design.md B1) ---
    # "static": only INGEST_API_TOKEN (the Phase 0 behaviour, the default).
    # "sso": SSO access tokens (Bearer) and the browser session from /login.
    # "both": SSO or the static token - what keeps MCP clients and scripts
    # working while browsers move to /login. create_app() refuses to start
    # in sso/both without SSO_BASE_URL, SSO_ISSUER, SSO_CLIENT_ID/SECRET, or
    # with a weak INGEST_API_TOKEN (see require_auth_config). /mcp is
    # protected in sso/both (v1.1 item 1); in static it stays open.
    auth_mode: Literal["static", "sso", "both"] = "static"
    sso_base_url: str = ""  # e.g. https://sso.example.com (server-to-server)
    sso_issuer: str = ""  # must equal the SSO's "iss" exactly
    sso_jwks_url: str = ""  # blank = {SSO_BASE_URL}/.well-known/jwks.json
    sso_audience: str = "myfin-services"
    sso_required_permission: str = "application.app1"
    # This app's ServiceClient at the SSO (HTTP Basic on login/refresh, so the SSO
    # throttles per end-user IP: design v1.1 item 4). Required in sso/both.
    sso_client_id: str = ""
    sso_client_secret: SecretStr = SecretStr("")
    # Server-side sessions (design v1.1 item 2): SQLite file holding the SSO tokens;
    # blank = {LOCAL_STORAGE_PATH}/sessions.sqlite3. Absolute lifetime in hours.
    session_db_path: Path | None = None
    session_max_age_hours: float = 168.0
    # Idle timeout (design v1.2 Q-27): a session unused this long ends.
    session_idle_hours: float = 12.0
    # Browser IP passed to the SSO (design v1.2 C-11/Q-25): blank = the TCP peer
    # (uvicorn proxy headers are ignored, D-19). e.g. "cf-connecting-ip" or
    # "x-forwarded-for"; honoured ONLY when the peer is in TRUSTED_PROXY_IPS (CIDRs).
    client_ip_header: str = ""
    trusted_proxy_ips: str = ""  # comma-separated IPs/CIDRs, e.g. "127.0.0.1/32,::1/128"
    session_cookie_secure: bool = True  # false only for local http dev
    # true: read routes (/search, /answer, /concepts, /page, GET /domains,
    # /worker, /sources) need auth too. /healthz and /login stay public.
    sso_protect_reads: bool = False
    # D-17: the per-request SSO introspect bound. A hung SSO costs at most
    # timeout x (1 + retries) seconds (+ ~0.1 s backoff) before the 503.
    sso_introspect_timeout_s: float = 2.5
    sso_introspect_retries: int = 0  # 0 or 1

    # --- Capture channels (Phase 1, KB design §5): each is optional and
    # independently disabled by leaving its secret unset - api/app.py mounts
    # a channel's router only when llmwiki.channels.<name>.build_router(cfg)
    # returns non-None, the same degrade-gracefully posture as the MCP mount. ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_webhook_secret: SecretStr = SecretStr("")
    mailgun_signing_key: SecretStr = SecretStr("")
    # The service's public HTTPS origin (e.g. the Cloudflare Tunnel hostname).
    # Read only by scripts/telegram_webhook.py, which registers
    # <public_base_url>/channels/telegram/webhook with Telegram on every
    # `docker compose up`. Blank = never register (the dev default).
    public_base_url: str = ""
    # YouTube refuses transcript requests from most cloud-provider egress IPs,
    # so a deployed service needs one of these two to capture videos at all
    # (extractors/youtube.py). Both blank = a direct youtube-transcript-api
    # request, which works from a laptop.
    #   youtube_proxy_url    - HTTP(S) proxy for youtube-transcript-api
    #   youtube_cookies_path - Netscape cookies.txt of a logged-in session;
    #                          switches the fetch to yt-dlp. Wins when both set.
    #   youtube_whisper_model - "base"/"small"/...: a video with NO captions is
    #                          transcribed locally from its audio (llmwiki[whisper]
    #                          extra + ffmpeg). Blank = such a video fails cleanly.
    youtube_proxy_url: str = ""
    youtube_cookies_path: str = ""
    youtube_whisper_model: str = ""

    # --- Observability: stdlib logging ---
    # Consumed by llmwiki.logging_config.configure_logging, called once by each
    # entry point (cli.main, api/app.py). Independent of LangSmith tracing
    # below - this is process-local log output, not a shipped trace.
    log_level: str = "INFO"

    # --- Observability: LangSmith tracing (optional, off by default) ---
    # Independent of LLM_PROVIDER: factory.py wraps the native Anthropic client
    # directly and exports these for LangChain's own auto-instrumentation to
    # pick up on every other provider. Complements, not replaces, the measured
    # cost ledger (models/plan.py CostRecord -> wiki/_meta/cost.jsonl).
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "llmwiki"
    langsmith_endpoint: str = ""
    # The LangSmith dataset scripts/eval_answer.py pushes to and evaluates
    # against (Phase 1-D). Tracing is not required for local (--offline) eval.
    langsmith_eval_dataset: str = "llmwiki-answer-quality"

    # --- Domains (Phase 2, design §4.10.1, plan §21.2 A4/A6) -------------------
    # "auto": a source with no explicit domain is routed by one route_domain
    # call - but only when at least one domain is registered; a general-only
    # registry makes no call at all. "off": never call the router; everything
    # not explicitly filed goes to general.
    domain_routing: Literal["auto", "off"] = "auto"
    # Below this confidence the router's pick is demoted to general and kept
    # as suggested_domain.
    domain_route_min_confidence: float = 0.6

    # Query scope when no domain= is given (plan §21.2 A6): "all" fans out over
    # every registered domain with no model call; "routed" spends one
    # route_domain call to pick at most QUERY_MAX_DOMAINS; "general" searches
    # only the general wiki. With nothing registered all three are identical.
    query_domain_policy: Literal["all", "routed", "general"] = "all"
    query_max_domains: int = 2
    # The scheduled per-domain overview page reads at most this many page
    # bodies (plan §21.2 A8) - one strong-model call per domain per run.
    synthesis_max_pages: int = 10

    # --- Hybrid retrieval (Phase 2, design §4.10.2, plan §21.2 B1-B4) -----------
    # A lexical (keyword) index beside the dense one, fused by reciprocal rank.
    # "sqlite": FTS5, one file per index under LEXICAL_DB_PATH, rebuildable from
    # raw/ (`llmwiki lexical rebuild`); "memory": tests/offline; "none": dense
    # only - byte for byte the pre-Phase-2 retrieval.
    lexical_backend: Literal["sqlite", "memory", "none"] = "sqlite"
    lexical_db_path: Path | None = None  # default: {LOCAL_STORAGE_PATH}/lexical
    # Candidates taken from each list (dense, lexical) per scope before fusion.
    hybrid_pool_k: int = 20
    # A cross-encoder reorders the fused pool (at most RERANK_MAX_CANDIDATES)
    # inside each layer; the wiki-confidence gate is unaffected. "workers_ai"
    # reuses the CF_* credentials; "fake" is the offline double; "none" skips.
    reranker_backend: Literal["workers_ai", "fake", "none"] = "workers_ai"
    reranker_model: str = "@cf/baai/bge-reranker-base"
    rerank_max_candidates: int = 40

    # --- Selective multimodal (Phase 2, design §4.10.4, plan §21.2 D1-D4) --------
    # "off": today's behaviour - images fail at extraction, scanned PDFs raise.
    # "auto": PDF pages with fewer than VISION_MIN_CHARS_PER_PAGE chars of text
    # layer, or (figure-heavy) pages whose images cover VISION_IMAGE_AREA_RATIO
    # of the page, plus uploaded images, are described by the describe_image
    # op - at most VISION_MAX_PAGES_PER_SOURCE per source. "always": every
    # PDF page, up to the cap. The op must route to a vision-capable model.
    vision_mode: Literal["off", "auto", "always"] = "off"
    vision_max_pages_per_source: int = 8
    vision_min_chars_per_page: int = 200
    vision_image_area_ratio: float = 0.25

    # --- Ingest worker (Phase 2, plan §21.2 C3/C5) ------------------------------
    # "threads": a bounded pool of WORKER_THREADS drains what REST and the
    # capture channels submit, one domain compiling at a time (a lock per
    # domain). "inline": run in the caller - the CLI, the MCP tool and the test
    # suite. WORKER_THREADS also bounds concurrent LLM calls from ingest;
    # providers with tight rate limits want 2.
    worker_mode: Literal["threads", "inline"] = "threads"
    worker_threads: int = 4
    # How often a source parked under the monthly hard cap is retried.
    worker_resume_interval_s: float = 60.0

    # --- Cost alerts and the hard cap (Phase 2, plan §21.2 C4/C5) -----------------
    # USD thresholds over the measured ledger; 0 = off. The two alerts log a
    # WARNING and notify once per day/month. The hard cap also pauses
    # post-capture processing (extract/route/embed/compile) until the month
    # rolls over or the cap is raised - capture, search and answers keep working.
    cost_alert_daily_usd: float = 0.0
    cost_alert_monthly_usd: float = 0.0
    cost_hard_cap_monthly_usd: float = 0.0
    # Where alerts go besides the log: "telegram" reuses TELEGRAM_BOT_TOKEN and
    # needs ALERT_TELEGRAM_CHAT_ID; "fake" is the offline double.
    notify_backend: Literal["log", "telegram", "fake"] = "log"
    alert_telegram_chat_id: str = ""

    # --- Cost guardrails ---
    # Phase 2 (plan §21.2 C1): which process is writing the cost ledger. Each
    # writer gets its own daily key under wiki/_meta/cost/, so the API, the
    # CLI and the cron jobs never read-modify-write the same object. The CLI
    # and the scripts set this themselves (COST_WRITER=cli, backfill, eval...);
    # "api" is what the service process is.
    cost_writer: str = "api"
    compile_max_pages: int = 5
    compile_candidate_pages: int = 8
    ingest_token_budget: int = 60_000
    chunk_size_chars: int = 3200
    chunk_overlap_chars: int = 400

    # --- Backend selection ---
    storage_backend: Literal["r2", "local"] = "r2"
    vector_backend: Literal["vectorize", "memory"] = "vectorize"
    embedding_backend: Literal["workers_ai", "fake"] = "workers_ai"
    local_storage_path: Path = Field(default=Path("./.data"))

    # --- Deprecated LLM aliases, removed at N4 (plan II §7.6) ---
    # Real fields, not properties, so that both a pre-rename ``.env`` and a
    # pre-rename constructor kwarg keep working.  ``extra="ignore"`` would
    # silently swallow an unknown kwarg, which is exactly how a test fixture
    # asking for the fake backend would end up building a real client.
    llm_backend: Provider | None = None
    anthropic_api_key: SecretStr | None = None
    llm_default_model: str | None = None

    @model_validator(mode="after")
    def _resolve_deprecated_aliases(self) -> Settings:
        """Map the old LLM variable names onto the generic ones, then mirror back.

        An explicitly-set canonical name always wins over its deprecated alias;
        anything a settings source supplied counts as explicitly set.  After
        resolution both spellings hold the same value, so call sites and tests
        written against either one keep reading the truth.
        """
        chosen = self.model_fields_set
        if self.llm_backend is not None and "llm_provider" not in chosen:
            self.llm_provider = self.llm_backend
        if self.anthropic_api_key is not None and "llm_api_key" not in chosen:
            self.llm_api_key = self.anthropic_api_key
        if self.llm_default_model is not None and "llm_model" not in chosen:
            self.llm_model = self.llm_default_model

        self.llm_backend = self.llm_provider
        self.anthropic_api_key = self.llm_api_key
        self.llm_default_model = self.llm_model
        return self

    @property
    def lexical_root(self) -> Path:
        """Where the FTS5 files live: ``LEXICAL_DB_PATH`` or ``{LOCAL_STORAGE_PATH}/lexical``."""
        return self.lexical_db_path or (self.local_storage_path / "lexical")

    @property
    def effective_sso_jwks_url(self) -> str:
        """``SSO_JWKS_URL``, or ``{SSO_BASE_URL}/.well-known/jwks.json`` when that is blank."""
        return self.sso_jwks_url or f"{self.sso_base_url.rstrip('/')}/.well-known/jwks.json"

    @property
    def effective_session_db_path(self) -> Path:
        """``SESSION_DB_PATH``, or ``{LOCAL_STORAGE_PATH}/sessions.sqlite3`` when unset."""
        return self.session_db_path or (self.local_storage_path / "sessions.sqlite3")

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """TRUSTED_PROXY_IPS parsed (ValueError on a bad entry)."""
        return [ipaddress.ip_network(x.strip(), strict=False)
                for x in self.trusted_proxy_ips.split(",") if x.strip()]

    def static_token_weakness(self) -> str | None:
        """Why INGEST_API_TOKEN is weak, or None. Weak = ``changeme`` or shorter than
        ``MIN_STATIC_TOKEN_CHARS`` (24) characters (design v1.1 item 6)."""
        token = self.ingest_api_token.get_secret_value()
        if token.strip() == "changeme":
            return "is the placeholder 'changeme'"
        if len(token) < MIN_STATIC_TOKEN_CHARS:
            return f"is shorter than {MIN_STATIC_TOKEN_CHARS} characters ({len(token)})"
        return None

    def require_auth_config(self) -> None:
        """Fail fast (design B1, v1.1): AUTH_MODE=sso|both needs the SSO settings, this app's
        ServiceClient credentials and a strong INGEST_API_TOKEN. static: nothing is required."""
        if self.auth_mode == "static":
            return
        missing = [name for name, value in (
            ("SSO_BASE_URL", self.sso_base_url),
            ("SSO_ISSUER", self.sso_issuer),
            ("SSO_CLIENT_ID", self.sso_client_id),
            ("SSO_CLIENT_SECRET", self.sso_client_secret.get_secret_value()),
            ("SSO_REQUIRED_PERMISSION", self.sso_required_permission),
        ) if not value.strip()]
        if missing:
            raise RuntimeError(
                f"AUTH_MODE={self.auth_mode} needs {', '.join(missing)} "
                "(see .env.example, 'Authentication')."
            )
        weak = self.static_token_weakness()
        if weak:
            raise RuntimeError(
                f"AUTH_MODE={self.auth_mode} refuses a weak INGEST_API_TOKEN: it {weak}. Use e.g. "
                "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        if not 0 < self.sso_introspect_timeout_s <= 30:
            raise RuntimeError("SSO_INTROSPECT_TIMEOUT_S must be > 0 and <= 30")
        if self.sso_introspect_retries not in (0, 1):
            raise RuntimeError("SSO_INTROSPECT_RETRIES must be 0 or 1")
        if self.session_max_age_hours <= 0 or self.session_idle_hours <= 0:
            raise RuntimeError("SESSION_MAX_AGE_HOURS and SESSION_IDLE_HOURS must be positive")
        try:
            networks = self.trusted_proxy_networks
        except ValueError as exc:
            raise RuntimeError(f"TRUSTED_PROXY_IPS: {exc}") from exc
        if self.client_ip_header.strip() and not networks:
            raise RuntimeError("CLIENT_IP_HEADER is set but TRUSTED_PROXY_IPS is empty: the "
                               "header would never be honoured")

    def require(self, *fields: str) -> None:
        """Fail loudly, and by name, when a backend's credentials are absent.

        Adapters call this in their constructor so a misconfigured deployment
        reports ``R2_ACCESS_KEY_ID is not set`` rather than a 403 from boto3.
        """
        missing = []
        for field in fields:
            value = getattr(self, field)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value or value == "changeme":
                missing.append(field.upper())
        if missing:
            raise RuntimeError(
                f"missing required configuration: {', '.join(missing)}. "
                "Copy .env.example to .env and fill these in "
                "(see implement-plan.md Part I section 6)."
            )


def load_settings() -> Settings:
    """Build a fresh Settings from the current environment.

    Used by tests and by the CLI after it has adjusted ``os.environ``; ordinary
    code should use the module-level ``settings``.
    """
    return Settings()


settings = load_settings()
