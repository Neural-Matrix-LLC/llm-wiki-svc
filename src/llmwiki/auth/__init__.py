"""Authentication for the HTTP surface: SSO access tokens, the static token, the browser session.

``llmwiki.auth`` is an L1 primitive (``tests/unit/test_layering.py``): it imports only ``config``
and
the standard/third-party libraries. The transport layer (``api/``) reaches it through ``tools.py``
(``tools.auth_service`` and the re-exported exceptions), like every other capability.

Design: ``docs/sso-integration-design.md`` Part B.
"""

from llmwiki.auth.principal import (
    AuthError,
    PermissionDenied,
    Principal,
    SSOLoginError,
    SSOUnavailable,
)

__all__ = ["AuthError", "PermissionDenied", "Principal", "SSOLoginError", "SSOUnavailable"]
