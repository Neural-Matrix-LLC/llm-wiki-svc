"""HTTP routes. No business logic lives here - every handler calls ``tools.py``.

Auth (docs/sso-integration-design.md B3): ``AUTH_MODE=static`` (default) is the Phase 0 static
bearer token, unchanged. ``sso`` accepts SSO access tokens and the browser session from ``/login``;
``both`` accepts either. ``require_auth`` is the dependency; ``require_token`` is kept as its alias.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator

from llmwiki import tools
from llmwiki.config import settings
from llmwiki.models.chunk import SearchHit
from llmwiki.models.page import Domain, LintReport, PageGist
from llmwiki.models.plan import Answer, CompileResult, CostSummary, SynthesisResult, WorkerStatus
from llmwiki.models.source import SourceRef, SourceStatus

logger = logging.getLogger(__name__)

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

#: Below this length, first-5 + last-5 would give away most of the secret (at
#: 10 characters it would be the whole thing), so short tokens show length only.
MIN_MASKABLE_TOKEN_CHARS = 16


def mask(secret: str) -> str:
    """Render a secret for a log: enough to tell two apart, not enough to use one.

    A debug line that prints the token in full defeats the ``SecretStr`` the
    setting is stored in, and DEBUG is exactly the level a deployment turns on
    when authentication is misbehaving - the moment the log is most likely to
    be pasted into a chat or a ticket.
    """
    if not secret:
        return "<empty>"
    if len(secret) < MIN_MASKABLE_TOKEN_CHARS:
        return f"<{len(secret)} chars, too short to show safely>"
    return f"{secret[:5]}...{secret[-5:]} ({len(secret)} chars)"


#: 401 challenge for AUTH_MODE=sso|both (design B3). static mode keeps its Phase 0 401 unchanged.
WWW_AUTHENTICATE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


class Forbidden(Exception):
    """Authenticated but without SSO_REQUIRED_PERMISSION -> 403 (app.py renders the body)."""

    BODY = {"detail": "forbidden", "error": "permission_denied"}


#: Cookie-authenticated requests with these methods need X-CSRF-Token (v1.1 item 3, v1.2 C-13).
CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CSRFFailed(Exception):
    """Cookie-authenticated write without a matching X-CSRF-Token -> 403 (v1.2 Q-30)."""

    BODY = {"error": "csrf_failed"}


def _sso_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Login service unavailable")


def _sso_misconfigured() -> HTTPException:
    return HTTPException(status_code=503, detail="Login service misconfigured")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="invalid or missing credentials",
                         headers=WWW_AUTHENTICATE)


def _static_check(credentials: HTTPAuthorizationCredentials | None) -> None:
    """Constant-time check of the static Phase 0 bearer token (AUTH_MODE=static, as before)."""
    expected = settings.ingest_api_token.get_secret_value()
    supplied = credentials.credentials if credentials else ""
    logger.debug("require_token: expected=%s supplied=%s", mask(expected), mask(supplied))
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> tools.Principal | None:
    """The protected-route dependency (design B3).

    * static: exactly the Phase 0 check (returns None).
    * sso/both: ``Authorization: Bearer`` first - a JWT is verified as an SSO access token plus
      SSO_REQUIRED_PERMISSION (sso/both), anything else is compared with INGEST_API_TOKEN (both
      only). Without a header, the browser session (api/session.py). 401 + WWW-Authenticate when
      missing/invalid, 403 when the permission is missing.
    """
    if settings.auth_mode == "static":
        _static_check(credentials)
        return None
    service = tools.auth_service(settings)
    supplied = credentials.credentials if credentials else ""
    logger.debug("require_auth: mode=%s supplied=%s", settings.auth_mode, mask(supplied))
    if supplied:
        try:
            return service.authenticate_bearer(supplied)
        except tools.PermissionDenied as exc:
            raise Forbidden() from exc
        except tools.AuthError as exc:
            logger.info("bearer rejected: %s", exc)
            raise _unauthorized() from exc
    principal = getattr(request.state, "auth_principal", None)
    if principal is not None:
        if request.method in CSRF_METHODS:
            session = request.state.auth_session
            if not service.csrf_ok(session.csrf, request.headers.get(service.csrf_header)):
                raise CSRFFailed()
        return principal
    problem = getattr(request.state, "auth_problem", None)
    if problem == "forbidden":
        raise Forbidden()
    if problem == "unavailable":
        raise _sso_unavailable()
    if problem == "misconfigured":
        raise _sso_misconfigured()
    raise _unauthorized()


#: Kept so routes and callers that name the Phase 0 dependency do not churn.
require_token = require_auth


def require_read_auth(request: Request) -> tools.Principal | None:
    """Read routes: open unless SSO_PROTECT_READS=true, then the same as :func:`require_auth`.

    Reads the Authorization header itself instead of depending on ``HTTPBearer``, so the
    read routes' OpenAPI schema stays exactly as before (design v1.1 item 5).
    """
    if not settings.sso_protect_reads:
        return None
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    credentials = (HTTPAuthorizationCredentials(scheme=scheme, credentials=value.strip())
                   if scheme.lower() == "bearer" and value.strip() else None)
    return require_auth(request, credentials)


#: Dependencies of the read routes: a no-op unless SSO_PROTECT_READS=true.
READ_AUTH = [Depends(require_read_auth)]


class IngestRequest(BaseModel):
    """Body of ``POST /ingest``: either a URL to fetch or text to store verbatim."""

    url: str | None = None
    text: str | None = None
    title: str = ""
    # Phase 2: file the source under this registered domain (else routed).
    domain: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> IngestRequest:
        """Reject a body naming both or neither, so the 422 comes from validation."""
        if (self.url is None) == (self.text is None):
            raise ValueError("provide exactly one of url or text")
        return self


@router.get("/healthz")
def healthz() -> dict:
    """Liveness plus the configured backends. Unauthenticated, no network calls."""
    return tools.health()


@router.post("/ingest", response_model=SourceRef, dependencies=[Depends(require_token)])
def ingest(request: IngestRequest, background: BackgroundTasks) -> SourceRef:
    """Capture a URL or a block of text and queue it. Returns before compilation runs.

    ``{"url": ...}`` covers a blog post, a YouTube video and a direct link to a
    PDF; ``{"text": ...}`` stores the string itself as the immutable source.
    Files go to ``POST /upload`` instead.
    """
    logger.debug(" /ingest request: %s", request)
    try:
        ref = tools.ingest_source(url=request.url, text=request.text, title=request.title,
                                  domain=request.domain)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc
    except tools.ExtractionError as exc:
        # The URL could not be fetched (YouTube refusing a cloud IP, a dead
        # link): the request is well-formed, the source is not obtainable.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ref.duplicate:
        background.add_task(tools.enqueue_source, ref.source_id)
    return ref


@router.post("/upload", response_model=SourceRef, dependencies=[Depends(require_token)])
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    domain: str | None = Form(None),
) -> SourceRef:
    """Capture an uploaded file: a PDF, a ``.txt``/``.md`` text file, HTML or an image.

    The modality comes from the content type and filename, not from the caller.
    ``domain`` (a form field, Phase 2) files it under a registered domain.
    """
    logger.debug(" /upload request: filename=%s, content_type=%s", file.filename, file.content_type)
    data = await file.read()
    try:
        ref = tools.ingest_source(
            file=data,
            filename=file.filename,
            mime=file.content_type or "application/octet-stream",
            title=title,
            domain=domain or None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc
    if not ref.duplicate:
        background.add_task(tools.enqueue_source, ref.source_id)
    return ref


@router.get("/usage", response_model=CostSummary, dependencies=[Depends(require_token)])
def usage(month: str | None = None, domain: str | None = None) -> CostSummary:
    """Spend by model, op, domain, kind, day and source (Phase 2). Default: month to date."""
    try:
        return tools.usage_summary(month=month, domain=domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"month must be YYYY-MM: {exc}") from exc


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    month: str | None = None,
    token: str | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Response:
    """The usage dashboard (Phase 2). Bearer header, or ``?token=`` for a browser.

    The same static token as every other protected route; the query-parameter
    form exists only because a browser cannot send the header. Keep it behind
    TLS, as the deployment already is.
    """
    # (Docstring unchanged on purpose: it is the OpenAPI description, which stays
    # byte-for-byte the same in AUTH_MODE=static - design v1.1 item 5.)
    # sso/both (design B4): the browser session from /login (unauthenticated
    # browsers get a 303 to /login?next=/dashboard), or a Bearer header. The
    # ?token= form works in "both" only (static token); "sso" ignores it so
    # tokens stay out of URLs.
    from llmwiki.api.dashboard import render

    signed_in_as: str | None = None
    if settings.auth_mode == "static":
        expected = settings.ingest_api_token.get_secret_value()
        supplied = token or (credentials.credentials if credentials else "")
        if not secrets.compare_digest(supplied or "", expected):
            raise HTTPException(status_code=401, detail="invalid or missing token")
    elif token and settings.auth_mode == "both":
        if not tools.auth_service(settings).static_matches(token):
            raise HTTPException(status_code=401, detail="invalid or missing token")
    elif credentials:
        require_auth(request, credentials)  # an invalid explicit Bearer is a 401 (v1.1 Q-07)
    else:
        principal = getattr(request.state, "auth_principal", None)
        if principal is None:
            from llmwiki.api import login as login_pages

            problem = getattr(request.state, "auth_problem", None)
            if problem == "forbidden":
                return login_pages.no_access_page(request)
            if problem in ("unavailable", "misconfigured"):
                return login_pages.unavailable_page(misconfigured=problem == "misconfigured")
            target = "/dashboard" + (f"?month={month}" if month else "")
            return RedirectResponse(f"/login?{urlencode({'next': target})}", status_code=303)
        signed_in_as = principal.username or principal.sub

    try:
        if signed_in_as is None:
            return HTMLResponse(render(month=month))
        csrf = request.state.auth_session.csrf
        response = HTMLResponse(render(month=month, signed_in_as=signed_in_as, csrf=csrf))
        response.headers["Cache-Control"] = "no-store"
        return response
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"month must be YYYY-MM: {exc}") from exc


@router.get("/worker", response_model=WorkerStatus, dependencies=READ_AUTH)
def worker() -> WorkerStatus:
    """What the ingest worker is doing (Phase 2): queued, in flight, parked and why."""
    return tools.worker_status()


@router.post("/worker/resume", dependencies=[Depends(require_token)])
def worker_resume() -> dict:
    """Retry sources parked under the monthly cost cap now (after raising it)."""
    resumed = tools.resume_processing()
    return {"ok": True, "resumed": resumed}


@router.get("/sources/{source_id}", response_model=SourceStatus, dependencies=READ_AUTH)
def source_status(source_id: str) -> SourceStatus:
    """Poll one source through the pipeline."""
    try:
        return tools.get_source_status(source_id)
    except tools.InvalidSourceId as exc:  # malformed id: 404, not a 500 (QA PRE-01)
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/search", response_model=list[SearchHit], dependencies=READ_AUTH)
def search(q: str, k: int = 5, domain: str | None = None) -> list[SearchHit]:
    """Wiki-first search. ``domain`` (Phase 2) scopes it to one domain."""
    try:
        return tools.search_wiki(q, k=k, domain=domain)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc


@router.get("/answer", response_model=Answer, dependencies=READ_AUTH)
def answer(q: str, k: int = 5, domain: str | None = None) -> Answer:
    """Answer a question with verified citations.

    Since Phase 1-D the response also carries ``steps`` (the tool calls the
    query graph made), ``context``, ``external_refs`` (web results, never
    citations) and ``run_id`` (the LangSmith run, when tracing is on) - the
    handle ``POST /feedback`` takes. Since Phase 2 also ``cost_usd`` and
    ``domains`` (the domains searched); ``domain`` scopes the search.
    """
    try:
        return tools.answer(q, k=k, domain=domain)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc


class FeedbackRequest(BaseModel):
    """Body of ``POST /feedback``: score an answer and, ideally, say what was right."""

    run_id: str
    score: float = Field(ge=0.0, le=1.0)
    correction: str = ""


@router.post("/feedback", dependencies=[Depends(require_token)])
def feedback(request: FeedbackRequest) -> dict:
    """Attach a human correction to the run that produced an answer (design §4.9).

    409 when tracing is off: there is no run to attach to, and silently
    accepting the correction would be worse than refusing it.
    """
    try:
        feedback_id = tools.record_feedback(request.run_id, request.score, request.correction)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "feedback_id": feedback_id, "run_id": request.run_id}


@router.get("/concepts", response_model=list[PageGist], dependencies=READ_AUTH)
def concepts(prefix: str | None = None, domain: str | None = None) -> list[PageGist]:
    """List one domain's page gists - one object read regardless of wiki size."""
    try:
        return tools.list_concepts(prefix, domain=domain)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc


@router.get("/page/{slug}", response_class=PlainTextResponse, dependencies=READ_AUTH)
def page(slug: str, domain: str | None = None) -> str:
    """Return a page as raw markdown, so a browser or Obsidian can read it directly."""
    from llmwiki.wiki.pages import render_page

    try:
        return render_page(tools.get_page(slug, domain=domain))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no page {slug!r}") from exc


class DomainRequest(BaseModel):
    """Body of ``PUT /domains/{name}``."""

    description: str = ""


@router.get("/domains", response_model=list[Domain], dependencies=READ_AUTH)
def domains() -> list[Domain]:
    """The domain registry, ``general`` first (Phase 2). Unauthenticated, like ``/concepts``."""
    return tools.list_domains()


@router.put("/domains/{name}", response_model=Domain, dependencies=[Depends(require_token)])
def put_domain(name: str, request: DomainRequest) -> Domain:
    """Register a domain or update its description; creates its indexes (Phase 2)."""
    try:
        return tools.upsert_domain(name, request.description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/domains/{name}", dependencies=[Depends(require_token)])
def delete_domain(name: str, force: bool = False) -> dict:
    """Unregister a domain. 409 while it still holds pages, unless ``?force=true``."""
    try:
        removed = tools.remove_domain(name, force=force)
    except ValueError as exc:
        status = 409 if "still has pages" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"no such domain: {name!r}")
    return {"ok": True, "removed": name}


@router.post("/compile/{source_id}", response_model=CompileResult,
             dependencies=[Depends(require_token)])
def compile_source(source_id: str, force: bool = False) -> CompileResult:
    """Re-run the incremental compiler for one source."""
    return tools.compile_update(source_id, force=force)


@router.post("/synthesize/{domain}", response_model=SynthesisResult,
             dependencies=[Depends(require_token)])
def synthesize(domain: str) -> SynthesisResult:
    """Write or refresh one domain's overview page (Phase 2). Scheduled or manual only."""
    try:
        return tools.synthesize(domain)[0]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc


@router.post("/lint", response_model=LintReport, dependencies=[Depends(require_token)])
def lint(dry_run: bool = True, domain: str | None = None) -> LintReport:
    """Run the global lint on demand - every domain, or one. Normally a scheduled job."""
    try:
        return tools.lint_wiki(dry_run=dry_run, domain=domain)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no such domain: {exc.args[0]!r}") from exc
