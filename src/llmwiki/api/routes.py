"""HTTP routes. No business logic lives here - every handler calls ``tools.py``.

Phase 0 auth is a single static bearer token (design doc 5); real auth is Phase 1.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator

from llmwiki import tools
from llmwiki.config import settings
from llmwiki.models.chunk import SearchHit
from llmwiki.models.page import LintReport, PageGist
from llmwiki.models.plan import Answer, CompileResult
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


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Constant-time check of the static Phase 0 bearer token."""
    expected = settings.ingest_api_token.get_secret_value()
    supplied = credentials.credentials if credentials else ""
    logger.debug("require_token: expected=%s supplied=%s", mask(expected), mask(supplied))
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


class IngestRequest(BaseModel):
    """Body of ``POST /ingest``: either a URL to fetch or text to store verbatim."""

    url: str | None = None
    text: str | None = None
    title: str = ""

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
        ref = tools.ingest_source(url=request.url, text=request.text, title=request.title)
    except tools.ExtractionError as exc:
        # The URL could not be fetched (YouTube refusing a cloud IP, a dead
        # link): the request is well-formed, the source is not obtainable.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ref.duplicate:
        background.add_task(tools.process_source, ref.source_id)
    return ref


@router.post("/upload", response_model=SourceRef, dependencies=[Depends(require_token)])
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
) -> SourceRef:
    """Capture an uploaded file: a PDF, a ``.txt``/``.md`` text file, HTML or an image.

    The modality comes from the content type and filename, not from the caller.
    """
    logger.debug(" /upload request: filename=%s, content_type=%s", file.filename, file.content_type)
    data = await file.read()
    ref = tools.ingest_source(
        file=data,
        filename=file.filename,
        mime=file.content_type or "application/octet-stream",
        title=title,
    )
    if not ref.duplicate:
        background.add_task(tools.process_source, ref.source_id)
    return ref


@router.get("/sources/{source_id}", response_model=SourceStatus)
def source_status(source_id: str) -> SourceStatus:
    """Poll one source through the pipeline."""
    return tools.get_source_status(source_id)


@router.get("/search", response_model=list[SearchHit])
def search(q: str, k: int = 5) -> list[SearchHit]:
    """Wiki-first search."""
    return tools.search_wiki(q, k=k)


@router.get("/answer", response_model=Answer)
def answer(q: str, k: int = 5) -> Answer:
    """Answer a question with verified citations.

    Since Phase 1-D the response also carries ``steps`` (the tool calls the
    query graph made), ``context``, ``external_refs`` (web results, never
    citations) and ``run_id`` (the LangSmith run, when tracing is on) - the
    handle ``POST /feedback`` takes.
    """
    return tools.answer(q, k=k)


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


@router.get("/concepts", response_model=list[PageGist])
def concepts(prefix: str | None = None) -> list[PageGist]:
    """List page gists - one object read regardless of wiki size."""
    return tools.list_concepts(prefix)


@router.get("/page/{slug}", response_class=PlainTextResponse)
def page(slug: str) -> str:
    """Return a page as raw markdown, so a browser or Obsidian can read it directly."""
    from llmwiki.wiki.pages import render_page

    try:
        return render_page(tools.get_page(slug))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no page {slug!r}") from exc


@router.post("/compile/{source_id}", response_model=CompileResult,
             dependencies=[Depends(require_token)])
def compile_source(source_id: str, force: bool = False) -> CompileResult:
    """Re-run the incremental compiler for one source."""
    return tools.compile_update(source_id, force=force)


@router.post("/lint", response_model=LintReport, dependencies=[Depends(require_token)])
def lint(dry_run: bool = True) -> LintReport:
    """Run the global lint on demand. Normally a scheduled job."""
    return tools.lint_wiki(dry_run=dry_run)
