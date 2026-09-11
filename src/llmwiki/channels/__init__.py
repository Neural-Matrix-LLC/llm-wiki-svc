"""Webhook-based capture channels (Phase 1, KB design §5): Telegram, email.

Each module here is a self-contained FastAPI transport, peer to ``api/`` and
``mcp/``: it exposes ``build_router(cfg) -> APIRouter | None`` and reaches the
domain only through ``tools.py``, never around it
(``test_layering.py::test_transport_layer_only_calls_tools``). A channel whose
secret/token is unset returns ``None`` and is not mounted - the same
degrade-gracefully posture ``api/app.py`` already uses for the optional MCP
mount.
"""

from __future__ import annotations
