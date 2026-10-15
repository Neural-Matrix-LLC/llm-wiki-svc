"""ASGI app for test_v12b_qa_fixes D-19: importing llmwiki.api.app runs the proxy guard exactly as
a real ``uvicorn llmwiki.api.app:app`` start does; this app echoes the peer it was given."""
import json

import llmwiki.api.app  # noqa: F401  (side effect under test: neutralise uvicorn proxy headers)


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = json.dumps({"client": scope["client"][0] if scope.get("client") else None}).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
