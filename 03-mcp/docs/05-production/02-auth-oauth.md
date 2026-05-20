# MCP Production 02：OAuth 2.1 鉴权 —— 远程 MCP 标准方案

> **一句话**：远程 MCP 的官方鉴权是 **OAuth 2.1**，遵循 RFC 9728（OAuth 2.0 Protected Resource Metadata）。Server 暴露 `/.well-known/oauth-protected-resource` 告诉 Client 去哪找 IdP，Client 走标准 OAuth 流程拿 token，调用时 Authorization Bearer。本篇讲流程 + Python 落地骨架。

---

## 1. 整体架构

```
              ┌─────────────────┐
              │  IdP (Auth Server) │  ← 颁发 access token
              │  (Auth0/Okta/Keycloak)│
              └────────▲────────┘
                       │ OAuth 2.1
   ┌───────────────────┼──────────────────┐
   │                   │                  │
┌──▼───┐         ┌─────┴─────┐      ┌────▼────┐
│Client │ ←──────│ Auth flow│       │MCP Server│
│      │ Bearer  └───────────┘      │(Resource)│
│      ├─────────────────────────────>│        │
└──────┘     Authorization: Bearer eyJ...     └──────────┘
                            │
              Server 校验 token → 允许/拒绝
```

三个角色：

- **Client**（Claude Code / Cursor）
- **Authorization Server**（IdP：Auth0、Okta、Keycloak、Google OAuth、自建）
- **Resource Server**（MCP Server）

MCP Server **不**做用户管理 / 不发 token——只**验证** token。

---

## 2. RFC 9728：Protected Resource Metadata

Server 在固定路径暴露元数据，Client 拿来发现 IdP：

```
GET https://mcp.example.com/.well-known/oauth-protected-resource
```

返回：

```json
{
  "resource": "https://mcp.example.com/mcp",
  "authorization_servers": [
    "https://auth.example.com"
  ],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["mcp:read", "mcp:write"],
  "resource_documentation": "https://mcp.example.com/docs"
}
```

Client 收到后再访问 IdP 的 `/.well-known/oauth-authorization-server` 拿到完整 OAuth 配置（token 端点、scope 等）。

---

## 3. 标准 OAuth 2.1 流程（Client 端）

Authorization Code + PKCE 流程：

```
1. Client 调 .well-known/oauth-protected-resource → 知道 IdP
2. Client 调 IdP 的 .well-known/oauth-authorization-server → 知道 endpoints
3. Client 生成 code_verifier + code_challenge（PKCE）
4. Client 浏览器跳到 IdP 的 authorize 端点（带 client_id、redirect_uri、code_challenge）
5. 用户登录授权
6. IdP 回调 redirect_uri 带 authorization code
7. Client POST 到 IdP 的 token 端点（带 code + code_verifier）→ 拿到 access_token + refresh_token
8. Client 调 MCP Server，Authorization: Bearer <access_token>
9. Token 过期时用 refresh_token 续
```

PKCE 让没有 client_secret 的 public client（IDE / CLI）也能安全用 OAuth。

---

## 4. Client 端：Python SDK 支持

Python SDK 1.x 已经内置 OAuth Client 工具：

```python
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
from mcp.shared.auth import OAuthClientMetadata


class MemoryTokenStorage(TokenStorage):
    """简单内存 token store，生产里换成磁盘/Keychain"""
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, info):
        self.client_info = info


async def main():
    storage = MemoryTokenStorage()

    auth = OAuthClientProvider(
        server_url="https://mcp.example.com",
        client_metadata=OAuthClientMetadata(
            client_name="my-app",
            redirect_uris=["http://localhost:3000/callback"],
        ),
        storage=storage,
        redirect_handler=lambda url: print(f"请打开: {url}"),
        callback_handler=lambda: (input("粘贴回调 URL: "), None),
    )

    async with streamablehttp_client(
        "https://mcp.example.com/mcp",
        auth=auth,
    ) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            ...
```

SDK 自动：

1. 第一次连：拉 .well-known、跳浏览器、走 OAuth code flow
2. 拿到 access_token 存到 storage
3. 调 MCP 时自动加 Authorization 头
4. token 过期自动 refresh

---

## 5. Server 端：验证 Token

最简实现：用 `pyjwt` 校验 JWT bearer token：

```python
import os
import jwt
from jwt import PyJWKClient
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


ISSUER = os.environ["OAUTH_ISSUER"]              # https://auth.example.com
AUDIENCE = os.environ["MCP_AUDIENCE"]            # https://mcp.example.com/mcp
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
jwks = PyJWKClient(JWKS_URL)


class OAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/.well-known"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth_header[7:]

        try:
            signing_key = jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, signing_key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=AUDIENCE,
            )
        except jwt.InvalidTokenError as e:
            return JSONResponse({"error": f"token invalid: {e}"}, status_code=401)

        # 把 claims 放进 request state 供后续 handler 用
        request.state.user = claims
        return await call_next(request)
```

挂到 FastAPI app：

```python
app.add_middleware(OAuthMiddleware)
```

---

## 6. 暴露 .well-known 元数据

```python
@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata():
    return {
        "resource": "https://mcp.example.com/mcp",
        "authorization_servers": ["https://auth.example.com"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp:read", "mcp:write"],
    }
```

注意：这个端点**必须**不要求鉴权（Client 还没拿到 token 时就要访问它）。

---

## 7. 在 MCP 工具里使用用户身份

```python
from mcp.server.fastmcp import Context, FastMCP


mcp = FastMCP("auth-aware")


@mcp.tool()
async def get_my_data(ctx: Context) -> dict:
    """需要鉴权的工具"""
    # 从底层 starlette request 拿 claims
    request = ctx.request_context.request
    user = request.state.user

    user_id = user["sub"]
    return await fetch_data_for_user(user_id)
```

要点：把 token 验证后的 claims 通过 middleware 注入 request state，工具里读取。

---

## 8. Dynamic Client Registration

OAuth 2.1 + RFC 7591 让 Client 第一次连时自动注册——无需手动 client_id：

```
Client 调 IdP 的 /register
   ↓
{
  "client_name": "my-mcp-app",
  "redirect_uris": ["http://localhost:3000/callback"]
}
   ↓
IdP 返回 {"client_id": "xxx", "client_secret": "..."}
```

SDK 端 `OAuthClientProvider` 自动处理。

---

## 9. Scopes 设计

建议至少这几个 scope：

| Scope | 含义 |
|-------|------|
| `mcp:read` | 调只读工具 + 读 resource |
| `mcp:write` | 调写工具 |
| `mcp:admin` | 调管理类工具 |

Server 端拦截：

```python
@mcp.tool()
async def delete_thing(id: str, ctx: Context) -> dict:
    user = ctx.request_context.request.state.user
    if "mcp:admin" not in user.get("scope", "").split():
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError("此操作需要 mcp:admin scope")
    ...
```

---

## 10. Token 缓存与轮换

SDK 默认缓存到内存。生产 Client 要：

- 缓存到磁盘（mac Keychain / Windows DPAPI / Linux Secret Service）
- 实现 refresh_token 流程
- token 即将过期时主动刷新（不要等 401）

---

## 11. 错误处理

Client 调用 MCP 时如果 401，按规范要：

1. 看响应 `WWW-Authenticate` 头里的 `resource_metadata` URL（可选）
2. 重新拉 .well-known
3. 必要时重新走 OAuth flow

SDK 自动处理；自己写要小心循环。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **`.well-known` 也被 OAuth 拦了** | 中间件必须放行这些路径 |
| **audience claim 不匹配** | Server 校验 `aud` 必须严格匹配，IdP 端要正确配 |
| **PKCE code_verifier 没存** | Client 跳浏览器前要保存到 state，回调时取回 |
| **Token 没自动刷新** | refresh_token 流程要写完整 |
| **JWKS 拉取太频繁** | PyJWKClient 默认带 cache，看 ttl 设置 |
| **本地 redirect_uri 用 http** | OAuth 2.1 不允许 http（localhost 除外），用 `http://localhost:xxx` |

---

## 13. 下一步

- 📖 企业鉴权（Client Credentials、SAML 网关、专属 IdP） → [03-enterprise-auth.md](./03-enterprise-auth.md)
- 📖 安全防御 → [04-security.md](./04-security.md)
- 📖 远程部署 → [01-remote-mcp.md](./01-remote-mcp.md)

## 参考资料

- MCP Authorization spec：https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- RFC 9728 (Protected Resource Metadata)：https://datatracker.ietf.org/doc/html/rfc9728
- OAuth 2.1 draft：https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1
- RFC 7591 (Dynamic Client Registration)：https://datatracker.ietf.org/doc/html/rfc7591
