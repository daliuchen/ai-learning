"""服务到服务鉴权（Client Credentials）端到端：迷你授权服务器 + 受保护 MCP Server + ClientCredentialsOAuthProvider

三个角色都在这一个文件里，一条命令跑通（真实环境里：授权服务器是 Okta / Keycloak / Entra，你只写 MCP Server 和 Client）:
    python demos/production/03_enterprise_auth.py
"""
from __future__ import annotations

import asyncio
import time

import httpx2
import jwt
import uvicorn
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp import Client
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

AS_PORT, MCP_PORT = 9100, 9101
ISSUER = f"http://127.0.0.1:{AS_PORT}"
RESOURCE = f"http://127.0.0.1:{MCP_PORT}/mcp"
SECRET = "demo-signing-key-0123456789abcdef0123456789"
CLIENTS = {"reporting-agent": "s3cret"}  # client_id -> client_secret（生产：授权服务器里登记，密钥放 secrets manager）


# ============ 1) 迷你授权服务器：元数据 + /token（仅 client_credentials）============
async def as_metadata(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "issuer": ISSUER + "/",  # 要与 MCP Server 的 PRM 里 authorization_servers 的那一项（AnyHttpUrl 会补尾斜杠）完全一致
            "authorization_endpoint": f"{ISSUER}/authorize",  # RFC 8414 模型里它是必填字段，即使 client_credentials 用不到（不补会导致元数据被当成无效）
            "token_endpoint": f"{ISSUER}/token",
            "grant_types_supported": ["client_credentials"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "response_types_supported": ["token"],
            "scopes_supported": ["reports:read"],
        }
    )


async def token_endpoint(request: Request) -> JSONResponse:
    import base64

    form = await request.form()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):  # 默认 client_secret_basic：secret 放 HTTP Basic
        cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":")
    else:
        cid, sec = str(form.get("client_id")), str(form.get("client_secret"))
    if form.get("grant_type") != "client_credentials" or CLIENTS.get(cid) != sec:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    now = int(time.time())
    access = jwt.encode(
        {"iss": ISSUER + "/", "aud": form.get("resource") or RESOURCE, "sub": cid, "scope": form.get("scope", ""), "iat": now, "exp": now + 3600},
        SECRET,
        algorithm="HS256",
    )
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": 3600, "scope": form.get("scope", "")})


as_app = Starlette(routes=[Route("/.well-known/oauth-authorization-server", as_metadata), Route("/token", token_endpoint, methods=["POST"])])


# ============ 2) 受保护的 MCP Server（资源服务器）============
class JWTVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            c = jwt.decode(token, SECRET, algorithms=["HS256"], issuer=ISSUER + "/", audience=RESOURCE, options={"require": ["exp", "sub"]})
        except jwt.InvalidTokenError:
            return None
        return AccessToken(token=token, client_id=c["sub"], scopes=c.get("scope", "").split(), expires_at=c["exp"], resource=c["aud"], subject=c["sub"])


mcp = MCPServer(
    "Reports",
    token_verifier=JWTVerifier(),
    auth=AuthSettings(issuer_url=AnyHttpUrl(ISSUER), resource_server_url=AnyHttpUrl(RESOURCE), required_scopes=["reports:read"], validate_token_resource=True),
)


@mcp.tool()
def nightly_report() -> str:
    """生成夜间报表（审计：记录是哪个服务身份调的）"""
    t = get_access_token()
    return f"report for caller={t.client_id if t else 'anonymous'}"


# ============ 3) Client：ClientCredentialsOAuthProvider（httpx2.Auth），没有浏览器、没有人 ============
class MemoryStorage:  # TokenStorage 协议：4 个 async 方法
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


async def run_client() -> None:
    storage = MemoryStorage()
    oauth = ClientCredentialsOAuthProvider(
        server_url=RESOURCE,
        storage=storage,
        client_id="reporting-agent",
        client_secret="s3cret",  # 真实代码：os.environ / secrets manager，绝不进仓库
        scope="reports:read",  # 空格分隔字符串
        issuer=ISSUER + "/",  # 用授权服务器元数据里 issuer 的原值；不写已弃用，3.0 起必填
    )
    async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
        async with Client(streamable_http_client(RESOURCE, http_client=http_client)) as client:
            print("tools :", [t.name for t in (await client.list_tools()).tools])
            print("call  :", (await client.call_tool("nightly_report", {})).content[0].text)
    print("token 已缓存在 storage:", storage.tokens is not None)


async def main() -> None:
    servers = [
        uvicorn.Server(uvicorn.Config(as_app, port=AS_PORT, log_level="error")),
        uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), port=MCP_PORT, log_level="error")),
    ]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    while not all(s.started for s in servers):
        await asyncio.sleep(0.05)
    try:
        await run_client()
    finally:
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
