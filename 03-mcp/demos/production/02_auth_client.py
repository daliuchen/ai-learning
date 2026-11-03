"""配套 Client：演示 401 + 发现文档、带 token 调用、scope 不足、过期 / 错 audience 的 token"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from pathlib import Path

import httpx2
import jwt

from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client

# 文件名以数字开头，不能直接 import，按路径加载服务端模块只为复用 make_token / 常量
spec = importlib.util.spec_from_file_location("auth_server", Path(__file__).parent / "02_auth_server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

BASE = f"http://127.0.0.1:{srv.PORT}"
URL = f"{BASE}/mcp"


def client_with_token(token: str) -> Client:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0, read=300.0))
    return Client(streamable_http_client(URL, http_client=http))


async def main() -> None:
    async with httpx2.AsyncClient() as http:
        r = await http.post(URL, json={}, headers={"accept": "application/json, text/event-stream"})
        print("无 token      :", r.status_code, r.headers.get("www-authenticate", "")[:110], "...")
        meta = (await http.get(f"{BASE}/.well-known/oauth-protected-resource/mcp")).json()
        print("发现文档(RFC9728):", meta)

    alice = srv.make_token("alice", "notes:read")
    async with client_with_token(alice) as c:
        print("whoami        :", (await c.call_tool("whoami", {})).content[0].text)
        print("my_notes      :", (await c.call_tool("my_notes", {})).structured_content)
        r = await c.call_tool("delete_note", {"user": "alice", "index": 0})
        print("scope 不足    :", r.is_error, "|", r.content[0].text)

    async with client_with_token(srv.make_token("alice", "notes:read notes:write")) as c:
        print("有 write scope:", (await c.call_tool("delete_note", {"user": "alice", "index": 0})).content[0].text)

    bad = {
        "过期 token": jwt.encode({"iss": srv.ISSUER, "aud": srv.RESOURCE, "sub": "alice", "scope": "notes:read", "exp": int(time.time()) - 10}, srv.SECRET, algorithm="HS256"),
        "错 audience": jwt.encode({"iss": srv.ISSUER, "aud": "http://other-api/", "sub": "alice", "scope": "notes:read", "exp": int(time.time()) + 300}, srv.SECRET, algorithm="HS256"),
    }
    for label, tok in bad.items():
        try:
            async with client_with_token(tok) as c:
                await c.list_tools()
        except Exception as e:  # noqa: BLE001
            print(f"{label:10}    : 被拒绝 ->", type(e).__name__)


if __name__ == "__main__":
    asyncio.run(main())
