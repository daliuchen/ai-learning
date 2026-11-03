"""受 OAuth 2.1 保护的 MCP Server（资源服务器）：TokenVerifier 校验 JWT + 按 scope 授权（mcp 2.x）

Server 只**验证** token，不签发。真实环境里 token 来自你的 IdP（Auth0 / Keycloak / Entra），
验证用 IdP 的 JWKS（RS256）。这个 demo 为了能一条命令跑通，用本地共享密钥的 HS256，并在 __main__ 里自己签几个 token。

跑法:
    python demos/production/02_auth_server.py        # 起 Server（:8790/mcp）并打印两个演示 token 的签发方式
    python demos/production/02_auth_client.py        # 配套 Client：无 token / 有 token / scope 不足
"""
from __future__ import annotations

import os
import time

import jwt
from pydantic import AnyHttpUrl

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError

PORT = int(os.getenv("PORT", "8790"))
RESOURCE = f"http://127.0.0.1:{PORT}/mcp"  # 资源标识符：token 的 aud 必须是它（RFC 8707 resource indicator）
ISSUER = "https://auth.example.com"
SECRET = os.getenv("DEMO_JWT_SECRET", "dev-only-secret-change-me-0123456789abcdef")  # 演示用；生产用 IdP 的 JWKS


class JWTVerifier(TokenVerifier):
    """每个请求都会调：token 进、AccessToken | None 出。None = 拒绝（HTTP 401）。"""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                SECRET,
                algorithms=["HS256"],  # 生产：["RS256"] + PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token).key
                issuer=ISSUER,
                audience=RESOURCE,  # 严格校验 aud：防止别的资源的 token 被拿来「冒用」（confused deputy）
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.InvalidTokenError:
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp", claims["sub"]),
            scopes=claims.get("scope", "").split(),
            expires_at=claims["exp"],
            resource=claims["aud"],  # validate_token_resource=True 时，SDK 要求它等于 resource_server_url
            subject=claims["sub"],
        )


mcp = MCPServer(
    "Notes",
    token_verifier=JWTVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER),
        resource_server_url=AnyHttpUrl(RESOURCE),
        required_scopes=["notes:read"],  # 每个 token 都必须带；更细的按工具再查
        validate_token_resource=True,
    ),
)

NOTES = {"alice": ["Buy milk"], "bob": ["Ship the release"]}


@mcp.tool()
def whoami() -> str:
    """报告当前调用者"""
    t = get_access_token()  # 你的 verifier 返回的那个 AccessToken；stdio / 进程内连接下恒为 None
    return "anonymous" if t is None else f"{t.subject} client={t.client_id} scopes={t.scopes}"


@mcp.tool()
def my_notes() -> list[str]:
    """只返回「调用者自己」的笔记：身份来自 token，绝不来自请求头或模型传的参数"""
    t = get_access_token()
    assert t is not None and t.subject
    return NOTES.get(t.subject, [])


@mcp.tool()
def delete_note(user: str, index: int) -> str:
    """需要 notes:write scope 才能调（工具级授权）"""
    t = get_access_token()
    if t is None or "notes:write" not in t.scopes:
        raise ToolError("此操作需要 notes:write scope")  # 模型能读到并告诉用户去申请权限
    NOTES[user].pop(index)
    return "deleted"


def make_token(sub: str, scope: str, ttl: int = 300) -> str:
    """演示用：模拟 IdP 签发 token"""
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": RESOURCE, "sub": sub, "scope": scope, "iat": now, "exp": now + ttl}, SECRET, algorithm="HS256")


if __name__ == "__main__":
    mcp.run("streamable-http", port=PORT)
