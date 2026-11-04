import pytest

pytest.importorskip("fastmcp")
from starlette.testclient import TestClient  # noqa: E402

from unraid_mcp.config import Settings  # noqa: E402
from unraid_mcp.server import build_app, build_server  # noqa: E402

TOKEN = "t" * 40
S = Settings(unraid_url="http://server/graphql", unraid_api_key="k", access_token=TOKEN)


def test_auth():
    with TestClient(build_app(S)) as c:
        assert c.get("/health").text == "ok"
        assert c.post("/mcp", json={}).status_code == 401
        assert c.get("/.well-known/oauth-protected-resource").status_code == 404
        r = c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}",
                                    "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "1"}}})
        assert r.status_code == 200


def test_werkzeuge():
    import asyncio
    mcp = build_server(S)
    tools = asyncio.run(mcp.get_tools()) if hasattr(mcp, "get_tools") else None
    names = set(tools.keys() if isinstance(tools, dict) else [t.name for t in tools or []])
    if names:
        assert {"unraid_status", "docker_list", "docker_action", "files_write", "files_trash"} <= names
        assert not any("delete" in n for n in names)
