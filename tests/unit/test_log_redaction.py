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


# ---------------------------------------------------------------- F-R2-02: decoded names
@pytest.mark.parametrize("path,expected", [
    # percent-encoded names (Starlette decodes them, so they authenticate)
    (f"/dashboard?%74oken={SECRET}", "/dashboard?%74oken=[redacted]"),
    (f"/dashboard?%74%6F%6B%65%6E={SECRET}&m=1", "/dashboard?%74%6F%6B%65%6E=[redacted]&m=1"),
    (f"/x?access%5Ftoken={SECRET}", "/x?access%5Ftoken=[redacted]"),
    (f"/x?%54OKEN={SECRET}", "/x?%54OKEN=[redacted]"),
    # encoded values: the whole raw value goes
    ("/x?token=s%33cr%65t%26more&m=1", "/x?token=[redacted]&m=1"),
    # repeated parameters: every occurrence
    (f"/x?token={SECRET}&m=1&token=second&TOKEN=third",
     "/x?token=[redacted]&m=1&token=[redacted]&TOKEN=[redacted]"),
    # mixed case, *_token
    (f"/x?Refresh_Token={SECRET}&ToKeN=a", "/x?Refresh_Token=[redacted]&ToKeN=[redacted]"),
    # plus signs: in values (space), and in names (decoded as space: not a token name)
    ("/x?token=abc+def+ghi&q=a+b", "/x?token=[redacted]&q=a+b"),
    ("/x?q=token+%3D+x&my+token=y", "/x?q=token+%3D+x&my+token=y"),
    ("/x?+token=abc", "/x?+token=[redacted]"),  # ' token': over-redacted on purpose (harmless)
    # look-alikes untouched; no '=' means no value to redact
    ("/x?tokens=1&tok%65nizer=2&token", "/x?tokens=1&tok%65nizer=2&token"),
    # double-encoded: Starlette decodes once -> '%74oken', not 'token'
    ("/x?%2574oken=1", "/x?%2574oken=1"),
])
def test_redact_tokens_by_decoded_name(path, expected) -> None:
    assert redact_tokens(path) == expected


def test_the_rest_of_the_access_line_is_intact() -> None:
    record = _access_record(f"/dashboard?month=2026-10&%74oken={SECRET}&a=%20b")
    TokenRedactingFilter().filter(record)
    assert record.getMessage() == ('127.0.0.1:5000 - "GET /dashboard?month=2026-10'
                                   '&%74oken=[redacted]&a=%20b HTTP/1.1" 200')
    assert record.args[0] == "127.0.0.1:5000" and record.args[4] == 200


def test_format_string_messages_keep_their_placeholders() -> None:
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "GET /x?token=%s",
                            (SECRET,), None)
    TokenRedactingFilter().filter(rec)
    assert rec.msg == "GET /x?token=%s"  # no format/arg mismatch
