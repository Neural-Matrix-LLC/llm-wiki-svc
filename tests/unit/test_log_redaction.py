"""PRE-02: ``token=`` query parameters are redacted in uvicorn's access log."""

from __future__ import annotations

import logging

import pytest

from llmwiki.config import (
    TokenRedactingFilter,
    configure_logging,
    install_token_redaction,
    redact_tokens,
)

SECRET = "s3cret-static-token-value-123456"


@pytest.mark.parametrize("path,expected", [
    (f"/dashboard?token={SECRET}", "/dashboard?token=[redacted]"),
    (f"/dashboard?token={SECRET}&month=2026-10", "/dashboard?token=[redacted]&month=2026-10"),
    (f"/dashboard?month=2026-10&token={SECRET}", "/dashboard?month=2026-10&token=[redacted]"),
    (f"/x?a=1&TOKEN={SECRET}&b=2", "/x?a=1&TOKEN=[redacted]&b=2"),
    (f"/x?access_token={SECRET};b=2", "/x?access_token=[redacted];b=2"),
    ("/x?token=", "/x?token=[redacted]"),
    ("/x?tokenizer=bpe&mytoken=1", "/x?tokenizer=bpe&mytoken=1"),  # other names untouched
    ("/dashboard?month=2026-10", "/dashboard?month=2026-10"),
])
def test_redact_tokens(path, expected) -> None:
    assert redact_tokens(path) == expected


def _access_record(path: str) -> logging.LogRecord:
    # exactly how uvicorn's httptools/h11 protocols log an access line
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d',
                             ("127.0.0.1:5000", "GET", path, "1.1", 200), None)


def test_filter_redacts_the_access_line_and_keeps_it() -> None:
    record = _access_record(f"/dashboard?token={SECRET}&month=2026-10")
    assert TokenRedactingFilter().filter(record) is True
    line = record.getMessage()
    assert SECRET not in line
    assert line == '127.0.0.1:5000 - "GET /dashboard?token=[redacted]&month=2026-10 HTTP/1.1" 200'


def test_filter_handles_msg_and_dict_args() -> None:
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                            f"GET /d?token={SECRET}", None, None)
    TokenRedactingFilter().filter(rec)
    assert SECRET not in rec.getMessage()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%(p)s", ({"p": f"?token={SECRET}"},),
                            None)
    TokenRedactingFilter().filter(rec)
    assert SECRET not in rec.getMessage()


def test_configure_logging_wires_the_uvicorn_access_logger(caplog) -> None:
    configure_logging("INFO")
    install_token_redaction()  # idempotent
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, TokenRedactingFilter) for f in access.filters) == 1
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET",
                f"/dashboard?month=2026-10&token={SECRET}", "1.1", 200)
    assert SECRET not in caplog.text and "token=[redacted]" in caplog.text


def test_importing_the_app_installs_the_filter() -> None:
    import llmwiki.api.app  # noqa: F401 - import side effect under test

    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(f, TokenRedactingFilter) for f in logging.getLogger(name).filters)
