"""The provider registry: lazy imports, honest errors, no drift.

These run with no provider SDK installed and never touch the network.
"""

from __future__ import annotations

import subprocess
import sys
from typing import get_args

import pytest

from llmwiki.config import Provider, Settings
from llmwiki.llm import providers


def test_importing_the_registry_imports_no_provider_sdk() -> None:
    """The whole point of the extras: importing llmwiki must stay cheap.

    Run in a fresh interpreter, because another test in this session may
    already have imported one of these for its own reasons.
    """
    modules = sorted({spec.module for spec in providers.REGISTRY.values()})
    script = (
        "import sys, llmwiki.llm.providers, llmwiki.factory\n"
        f"leaked = [m for m in {modules!r} if m in sys.modules]\n"
        "print(','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


def test_the_langchain_providers_are_all_registered() -> None:
    assert set(providers.REGISTRY) == {
        "openai",
        "google",
        "nvidia",
        "deepseek",
        "openrouter",
    }


def test_anthropic_is_not_in_the_registry() -> None:
    """It keeps its native adapter: prompt caching and measured cost (7.5)."""
    assert "anthropic" not in providers.REGISTRY


def test_every_registered_provider_is_an_accepted_config_value() -> None:
    assert set(providers.REGISTRY) <= set(get_args(Provider))


def test_a_missing_extra_names_the_pip_command() -> None:
    """A missing integration must not surface as a bare ModuleNotFoundError."""
    absent = [
        name
        for name, spec in providers.REGISTRY.items()
        if spec.module not in sys.modules
    ]
    checked = 0
    for name in absent:
        try:
            providers.load_class(name)
        except RuntimeError as exc:
            message = str(exc)
            assert providers.REGISTRY[name].distribution in message
            assert f'pip install "llmwiki[{providers.REGISTRY[name].extra}]"' in message
            checked += 1
        except Exception:  # pragma: no cover - the integration is installed
            pass
    if checked == 0:  # pragma: no cover - every extra installed
        pytest.skip("all provider extras are installed in this environment")


def test_the_factory_reports_a_missing_extra_rather_than_failing_at_the_socket() -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, llm_provider="openai", llm_api_key="sk-openai")
    factory.reset()
    try:
        client = factory.llm_client(cfg)
    except RuntimeError as exc:
        assert "langchain-openai" in str(exc)
    else:  # pragma: no cover - langchain-openai is installed here
        assert client is not None
    finally:
        factory.reset()


@pytest.mark.parametrize("name", sorted(providers.REGISTRY))
def test_registry_keywords_match_the_installed_class(name: str) -> None:
    """Guards the assumption that one set of keywords fits every integration.

    Skips whichever extras are absent, so it costs nothing here and becomes a
    real check in an environment that installs them.
    """
    spec = providers.REGISTRY[name]
    pytest.importorskip(spec.module)
    cls = providers.load_class(name)
    fields = cls.model_fields
    accepted = set(fields) | {
        field.alias for field in fields.values() if getattr(field, "alias", None)
    }
    import inspect

    accepted |= set(inspect.signature(cls.__init__).parameters)
    for keyword in (
        providers.MODEL_ARG,
        providers.API_KEY_ARG,
        providers.BASE_URL_ARG,
        providers.TEMPERATURE_ARG,
        spec.max_tokens_arg,
    ):
        assert keyword in accepted, f"{cls.__name__} does not accept {keyword!r}"


def test_build_forwards_temperature_to_the_constructor(monkeypatch) -> None:
    """Unlike max_tokens, temperature needs no per-provider spelling override."""
    calls = []

    class FakeChatModel:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(providers, "load_class", lambda provider: FakeChatModel)

    providers.build("openai", model="gpt-5", api_key="sk-openai", temperature=0.2, max_tokens=99)

    assert calls[-1][providers.TEMPERATURE_ARG] == 0.2
    assert calls[-1][providers.REGISTRY["openai"].max_tokens_arg] == 99
