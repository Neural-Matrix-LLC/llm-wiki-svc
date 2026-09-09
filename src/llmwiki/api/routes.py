"""HTTP routes. No business logic lives here - every handler calls ``tools.py``.

Phase 0 auth is a single static bearer token (design doc 5); real auth is Phase 1.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, model_validator

from llmwiki import tools
from llmwiki.config import settings
from llmwiki.models.chunk import SearchHit
from llmwiki.models.page import LintReport, PageGist
from llmwiki.models.plan import Answer, CompileResult
from llmwiki.models.source import SourceRef, SourceStatus

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Constant-time check of the static Phase 0 bearer token."""
    expected = settings.ingest_api_token.get_secret_value()
    supplied = credentials.credentials if credentials else ""
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
    ref = tools.ingest_source(url=request.url, text=request.text, title=request.title)
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
    """Answer a question with verified citations."""
    return tools.answer(q, k=k)


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
