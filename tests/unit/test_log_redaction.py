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


# ---------------------------------------------------------------- repeated decoding of names
def _nest(name: str, levels: int) -> str:
    """Percent-encode the first character of ``name``, then re-encode the '%' ``levels``-1 times:
    _nest("token", 2) == "%2574oken"."""
    encoded = f"%{ord(name[0]):02X}{name[1:]}"
    for _ in range(levels - 1):
        encoded = encoded.replace("%", "%25", 1)
    return encoded


@pytest.mark.parametrize("raw_name", [
    "%2574oken",                 # double
    "%252574oken",               # triple
    "%25%37%34oken",             # double, the inner '74' itself encoded
    "%2574%256F%256B%2565%256E",  # double, every letter
    "%2554OKEN",                 # mixed case: upper-case name
    "%2574OkEn", "%2574oken".upper(),  # mixed-case letters and hex digits (%2574 / %2574)
    "access%255Ftoken",          # *_token, double-encoded underscore
    _nest("token", 4), _nest("token", 5),  # within the cap (5 rounds)
])
def test_multiply_encoded_token_names_are_redacted(raw_name) -> None:
    assert redact_tokens(f"/x?m=1&{raw_name}={SECRET}&n=2") == f"/x?m=1&{raw_name}=[redacted]&n=2"


@pytest.mark.parametrize("levels", [6, 7, 12, 40])
def test_names_nested_beyond_the_cap_are_redacted_fail_safe(levels) -> None:
    """Still encoded after MAX_NAME_DECODE_ROUNDS: treated as a token name (documented choice)."""
    from llmwiki.config import MAX_NAME_DECODE_ROUNDS

    assert MAX_NAME_DECODE_ROUNDS == 5
    raw = _nest("token", levels)
    assert redact_tokens(f"/x?{raw}={SECRET}") == f"/x?{raw}=[redacted]"
    other = _nest("month", levels)  # same depth, any name: also redacted at the cap
    assert redact_tokens(f"/x?{other}=2026") == f"/x?{other}=[redacted]"


def test_deep_nesting_does_not_crash_or_hang() -> None:
    raw = _nest("token", 300)  # 300 levels: capped after 5 rounds, redacted
    assert redact_tokens(f"/x?{raw}=v") == f"/x?{raw}=[redacted]"
    # not real nesting: one round turns 5000 '%25' into '%%%...%74oken' -> '%%%...%token', which
    # is stable (no longer decodable) and is not 'token': untouched, and fast
    junk = "%25" * 5000 + "74oken"
    assert redact_tokens(f"/x?{junk}=v") == f"/x?{junk}=v"


@pytest.mark.parametrize("raw_name", [
    "%256Donth",          # month, double-encoded
    "%2574okens",         # tokens
    "%2574okenizer",
    "100%",               # a stable '%' (not decodable): not capped, untouched
    "a%2Bb", "q%25",      # decode to 'a+b' / 'q%': stable after 1-2 rounds
    "%2574%256F%256B%2565",  # 'toke'
    "my%2520token",       # 'my token' with the space nested: not '_token'
])
def test_non_token_names_stay_untouched(raw_name) -> None:
    line = f"/x?{raw_name}=keep&b=2"
    assert redact_tokens(line) == line
