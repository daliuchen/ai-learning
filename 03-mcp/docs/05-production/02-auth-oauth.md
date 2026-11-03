# MCP Production 02：远程 MCP 的 OAuth 2.1 鉴权

> 远程 MCP 的官方鉴权方案是 OAuth 2.1。MCP Server 在其中扮演资源服务器，只验证 token，不签发 token；它通过 RFC 9728 的 `/.well-known/oauth-protected-resource` 告诉 Client 去哪个授权服务器拿 token。在 Python SDK 2.x 里，你只需要实现一个 `TokenVerifier`，发现文档、401 加 `WWW-Authenticate`、解析 `Authorization` 头这些事 SDK 都会处理。

---

## 1. 整体架构

```
              ┌──────────────────────────┐
              │  Authorization Server    │  ← 签发 access token（Auth0 / Keycloak / Entra / 自建）
              └───────────▲──────────────┘
                          │ OAuth 2.1
   ┌──────────────────────┼─────────────────────┐
┌──┴───┐            ┌─────┴─────┐          ┌────▼─────┐
│Client │ ←───────→ │  Auth flow │          │MCP Server│
│      │ Authorization: Bearer eyJ...        │(Resource)│
│      ├────────────────────────────────────>│          │
└──────┘                                     └──────────┘
                    Server 校验 token → 放行 / 401
```

这里有三个角色：Client（Claude Code、Cursor 或你自己的程序）；Authorization Server，也就是 IdP，负责登录、授权、签发 token，这部分不用你写；Resource Server，也就是你的 MCP Server，它检查每个请求 `Authorization` 头里的 token。

> 授权只存在于 HTTP 传输上。stdio 的安全边界是启动它的那个进程（规范要求 stdio 实现从环境变量里获取凭据，不走这一套）；进程内的 `Client(mcp)` 测试连接会跳过整个 HTTP 层，授权也一起跳过了。

---

## 2. 规范现状（2026-07-28）

2026-07-28 在授权方面相对上一版 2025-11-25 的变化：

| 变化 | 含义 |
|------|------|
| 优先使用 Client ID Metadata Documents（CIMD），动态客户端注册（DCR，RFC 7591）被弃用 | Client 不再向每个授权服务器 POST 注册，而是在一个稳定的 HTTPS URL 上发布一份描述自己的 JSON，这个 URL 就是 `client_id`，由授权服务器去抓取。DCR 只为不支持 CIMD 的授权服务器保留 |
| RFC 9207 的 `iss` 参数 | 授权服务器 SHOULD 在授权响应里带上 `iss`；Client 在兑换授权码之前 MUST 校验它和记录的 issuer 一致（防止服务器混淆攻击） |
| DCR 时必须带 `application_type` | 避免和 OIDC 的 redirect URI 规则冲突 |
| 客户端凭据绑定到颁发它的授权服务器 | 必须按 issuer 分别保存，不能在不同授权服务器之间复用；授权服务器变了就要重新注册 |
| WWW-Authenticate 里的 `scope` | Server SHOULD 在 401 的 `WWW-Authenticate` 里给出需要的 scope，Client 优先使用它（最小权限原则） |

三种 Client 注册方式按这个顺序选择：CIMD，然后是预注册，最后才是 DCR。

---

## 3. RFC 9728：Protected Resource Metadata

Server 在一个固定路径上暴露元数据，Client 据此找到授权服务器：

```
GET https://mcp.example.com/.well-known/oauth-protected-resource/mcp
```

路径是 `.well-known/oauth-protected-resource` 加上资源路径。在 Python SDK 里，这份文档根据你的 `AuthSettings` 自动生成。下面是本篇 demo 的实际响应：

```json
{
  "resource": "http://127.0.0.1:8790/mcp",
  "authorization_servers": ["https://auth.example.com/"],
  "scopes_supported": ["notes:read"],
  "bearer_methods_supported": ["header"]
}
```

发现流程从 401 开始。请求没带 token，或者 token 被你的 verifier 拒绝时，SDK 直接拦下请求：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://127.0.0.1:8790/.well-known/oauth-protected-resource/mcp"
```

`resource_metadata` 就是自动发现的入口：收到 401，读取元数据文档，找到授权服务器，拿到 token，再重试。

---

## 4. 标准的 OAuth 2.1 流程（Client 端）

使用 Authorization Code + PKCE：

```
1. Client 无 token 请求 → Server 401 + WWW-Authenticate（带 resource_metadata）
2. Client 取 Protected Resource Metadata → 知道授权服务器
3. Client 取授权服务器元数据（RFC 8414 或 OIDC Discovery，二者都要支持）
4. 注册：CIMD（client_id 就是 URL）/ 预注册 / DCR（已弃用）
5. Client 生成 PKCE 的 code_verifier + code_challenge，带上 resource 参数（RFC 8707）和选定的 scope
6. 浏览器跳到授权端点；用户登录并授权
7. 授权服务器带着 code 和 iss 回调 redirect_uri；Client 校验 iss（RFC 9207）
8. Client 用 code + code_verifier + resource 到 token 端点换 access_token（+ refresh_token）
9. 带 Authorization: Bearer 调 MCP；过期用 refresh_token 续
```

有了 PKCE，没有 client_secret 的公共客户端（IDE、CLI）也能安全地使用 OAuth。`resource` 参数让授权服务器把 token 绑定到这个 MCP Server，Server 再校验 `aud`，防止为别的资源签发的 token 被拿来冒用。

---

## 5. Client 端：`OAuthClientProvider`（Python SDK 2.x）

`OAuthClientProvider` 是一个 `httpx2.Auth`，本身和 MCP 协议无关。把它挂在 `httpx2.AsyncClient` 上，再把这个 HTTP 客户端交给 `streamable_http_client`，之后 `main()` 里就完全看不到 token 了：

```python
import httpx2
from pydantic import AnyUrl
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata

class FileTokenStorage:                      # TokenStorage 是个 Protocol：写出这 4 个 async 方法即可，不用继承
    async def get_tokens(self): ...          # 生产：落到磁盘 / Keychain / Secret Service
    async def set_tokens(self, tokens): ...
    async def get_client_info(self): ...     # 注意也要存 client_info，否则每次运行都重新注册
    async def set_client_info(self, info): ...

async def open_browser(url: str) -> None:    # 授权 URL 已含 client_id / state / PKCE challenge
    import webbrowser; webbrowser.open(url)

async def wait_for_callback() -> AuthorizationCodeResult:
    # 真实 Client 在 redirect_uri 上起一个本地 HTTP 服务；这里示意：返回回调里的 code / state / iss
    ...
    return AuthorizationCodeResult(code=code, state=state, iss=iss)    # state 和 iss 要原样传回，SDK 会比对

oauth = OAuthClientProvider(
    server_url="https://mcp.example.com/mcp",
    client_metadata=OAuthClientMetadata(
        client_name="my-app", redirect_uris=[AnyUrl("http://127.0.0.1:3030/callback")], scope="notes:read",
    ),
    storage=FileTokenStorage(),
    redirect_handler=open_browser,
    callback_handler=wait_for_callback,
    # client_metadata_url="https://my-app.example.com/client.json",   # CIMD：服务器支持时跳过 /register
)

async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
    async with Client(streamable_http_client("https://mcp.example.com/mcp", http_client=http_client)) as client:
        print([t.name for t in (await client.list_tools()).tools])
```

SDK 会自动完成这些步骤：

1. 第一次请求收到 401，读取 `WWW-Authenticate`，获取资源元数据和授权服务器元数据（元数据里的 `issuer` 必须和它所在的服务器一致，否则拒绝）
2. 存储里没有 `client_info` 时进行注册：服务器声明支持 CIMD、而你传了 `client_metadata_url` 时，不调用 `/register`；否则悄悄回退到 DCR
3. 生成 PKCE 和 `state`，等你的 `redirect_handler` 把用户带到浏览器，再等 `callback_handler` 拿回授权码
4. 兑换 token，存进 storage，带上 `Authorization: Bearer` 重放原来的请求。之后 token 过期会自动 refresh，refresh 也失败了才重新走完整流程

v2 的变化和注意事项：

- `streamable_http_client` 上没有 `auth=` 参数，认证、请求头、代理、超时都属于你自己创建的 `httpx2.AsyncClient`
- `callback_handler` 返回 `AuthorizationCodeResult`（v1 返回的是 `(code, state)` 元组），其中的 `iss` 要原样带回
- `OAuthClientMetadata` 是 Pydantic 模型，缺字段时在发网络请求之前就会报 `ValidationError`
- 客户端凭据绑定到授权服务器，issuer 变了就不会复用旧凭据
- 会请求 `offline_access`，并在授权服务器支持时加上 `prompt=consent`（SEP-2207）；step-up 授权会把之前请求过的 scope 一起并入
- `client_secret_post` 的 token 请求现在也会带 `client_id`；`timeout` 参数从 `OAuthClientProvider` 上移除了（超时设置在 `httpx2` 上）
- SDK 仓库里有可以运行的完整示例：`examples/servers/simple-auth/` 和 `examples/clients/simple-auth-client/`（要跑通需要一个完整的 IdP，见下文）

### 机器对机器：Client Credentials

没有浏览器、也没有人点「允许」的场景（定时任务、CI、其他服务），用 `ClientCredentialsOAuthProvider`，它同样是一个 `httpx2.Auth`：

```python
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider

oauth = ClientCredentialsOAuthProvider(
    server_url="https://mcp.example.com/mcp",
    storage=storage,
    client_id="reporting-agent",
    client_secret=os.environ["CLIENT_SECRET"],     # 不要进代码库
    scope="notes:read",                             # 空格分隔的字符串（v1 的 scopes= 已改名 scope=）
    issuer="https://auth.example.com",              # 颁发这些凭据的授权服务器；不写已弃用，3.0 起必填
)
```

细节见[企业鉴权（Client Credentials、企业管理授权与网关）](03-enterprise-auth.md)。

---

## 6. Server 端：`TokenVerifier` + `AuthSettings`

v2 SDK 的做法是实现 `TokenVerifier`，不再需要手写 Starlette 中间件。`TokenVerifier` 是一个只有一个 async 方法的协议：`verify_token(token) -> AccessToken | None`。SDK 对每个请求调用它，返回 `None` 就是 401。

```python
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
```

- `token_verifier=` 和 `auth=` 必须同时传，只传一个的话，`MCPServer(...)` 在启动前就会抛 `ValueError`
- 校验 JWT 用 `pyjwt`，必须严格校验 `iss`、`aud`、`exp`。生产环境用 IdP 的 JWKS（`PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token).key`，RS256）；这个 demo 为了一条命令就能跑通，用的是本地密钥的 HS256
- `validate_token_resource=True` 会拒绝 `AccessToken.resource` 不等于 `resource_server_url` 的 token。如果你的授权服务器按 MCP Client 发来的 `resource` 参数绑定 token，就打开它，并让 `resource_server_url` 和 Client 实际连接的 URL 完全一致；如果授权服务器用的是自己的 audience 标识（Auth0 的 API identifier、Entra 的应用 ID），就关掉它，改在 verifier 里自己检查 `aud`。设置了 `resource_server_url` 却没设这个选项，会触发 `MCPDeprecationWarning`，从 3.0 起默认值是 True
- `required_scopes`：每个 token 都必须带上的 scope；更细的权限按工具再检查
- 想要最简单的实现，可以参考 SDK 仓库 `examples/servers/simple-auth/` 里的 `IntrospectionTokenVerifier`，它调用授权服务器的 RFC 7662 introspection 端点，大多数生产环境的 verifier 都是这个形状
- `MCPServer` 还有一个 `auth_server_provider=` 参数，可以在 MCP Server 里内嵌一个完整的授权服务器。它出现在规范把 AS 和 RS 分开之前，新项目不要用

---

## 7. 在工具里获取调用者身份：`get_access_token()`

在任何 handler（工具、资源、Prompt）里，`get_access_token()` 返回你的 verifier 为当前请求构造的那个 `AccessToken`（包含 `client_id`、`scopes`、`subject`、`expires_at`、`claims` 等）。SDK 的鉴权中间件把它放在每个请求的 contextvar 里，不需要到处传递：

```python
from mcp.server.auth.middleware.auth_context import get_access_token

@mcp.tool()
def my_notes() -> list[str]:
    """只返回「调用者自己」的笔记：身份来自 token，绝不来自请求头或模型传的参数"""
    t = get_access_token()
    return NOTES.get(t.subject, [])
```

> 从 v1 迁移：不要再用 `ctx.request_context.request.state.user` 这种从 Starlette request 里取 claims 的写法。`ctx.headers` 是客户端可以控制的输入，地区、功能开关之类可以读，身份信息绝对不能从这里读。在 stdio 或进程内连接下，`get_access_token()` 永远是 `None`。

---

## 8. 动态客户端注册（已弃用）与 CIMD

- DCR（RFC 7591）：Client 第一次连接时向授权服务器的 `/register` 发 POST，自动拿到 `client_id`。2026-07-28 起弃用，只为不支持 CIMD 的授权服务器保留
- CIMD：Client 在一个稳定的 HTTPS URL（不能是根路径）上发布描述自己的 JSON，这个 URL 就是 `client_id`，授权服务器去抓取它并校验 `redirect_uris`。在 SDK 里传 `client_metadata_url=`；授权服务器元数据里有 `client_id_metadata_document_supported: true` 时会跳过 `/register`，没有就悄悄回退到 DCR。URL 不是 HTTPS 或者没有路径时，构造阶段就会抛 `ValueError`
- 预注册：授权服务器给你一个固定的 `client_id`（Cursor 等客户端的「静态 OAuth」就属于这种）

---

## 9. Scope 设计与工具级授权

建议至少设置这几个 scope：

| Scope | 含义 |
|-------|------|
| `notes:read` / `mcp:read` | 调用只读工具，读取 resource |
| `notes:write` / `mcp:write` | 调用写工具 |
| `mcp:admin` | 调用管理类工具 |

`required_scopes` 是全局门槛，更细的检查在工具里读 `get_access_token().scopes`：

```python
@mcp.tool()
def delete_note(user: str, index: int) -> str:
    t = get_access_token()
    if t is None or "notes:write" not in t.scopes:
        raise ToolError("此操作需要 notes:write scope")   # 模型能读到，并告诉用户去申请权限
    ...
```

Step-up 授权：对 scope 不够的请求，Server 返回 `403` 加 `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`，Client 用合并之后的 scope 重新授权。

---

## 10. 完整 demo

Server 校验 JWT 并按 scope 授权；Client 依次演示 401 加发现文档、带合法 token、scope 不足、token 过期、audience 不对这几种情况：

```python
# demos/production/02_auth_server.py
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
```

```python
# demos/production/02_auth_client.py
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
```

```bash
PORT=8790 python demos/production/02_auth_server.py      # 终端 1
PORT=8790 python demos/production/02_auth_client.py      # 终端 2
```

实际输出：

```
无 token      : 401 Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://127.0.0.1 ...
发现文档(RFC9728): {'resource': 'http://127.0.0.1:8790/mcp', 'authorization_servers': ['https://auth.example.com/'], 'scopes_supported': ['notes:read'], 'bearer_methods_supported': ['header']}
whoami        : alice client=alice scopes=['notes:read']
my_notes      : {'result': ['Buy milk']}
scope 不足    : True | Error executing tool delete_note: 此操作需要 notes:write scope
有 write scope: deleted
过期 token      : 被拒绝 -> ExceptionGroup
错 audience    : 被拒绝 -> ExceptionGroup
```

> 这个 demo 不覆盖「浏览器加真实授权服务器」的完整流程，那需要 SDK 仓库 `examples/servers/simple-auth/` 里提供的授权服务器。资源服务器这一半（验证、发现文档、401、按 scope 授权）用这个 demo 就能完整跑通。

---

## 11. Token 缓存与轮换

- token 要缓存到磁盘（macOS Keychain、Windows DPAPI、Linux Secret Service），不要只放在内存里，否则进程一退出就得重新走完整流程
- 实现 refresh_token 流程（SDK 的 provider 已经做了）
- 快过期时主动刷新；Server 端把 `exp` 设短一些（分钟级）

---

## 12. 错误处理

Client 的请求收到 401 时，先看响应 `WWW-Authenticate` 里的 `resource_metadata` URL，重新拉取 .well-known 文档，必要时重新走一遍 OAuth 流程。`OAuthClientProvider` 会全自动处理；如果自己实现，要小心别陷入循环。

---

## 13. 常见坑

| 坑 | 排查 |
|----|------|
| `.well-known` 被你自己的鉴权拦住了 | 用 SDK 的 `token_verifier=` + `auth=` 时，发现文档由 SDK 提供，不要求鉴权；只有自己写中间件时才需要手动放行 |
| 只传了 `token_verifier`，没传 `auth` | 会抛 `ValueError`，两者必须一起传 |
| audience 或 `resource` 不匹配 | `validate_token_resource=True` 要求 `AccessToken.resource == resource_server_url`；IdP 用自己的 audience 时，关掉它并在 verifier 里检查 `aud` |
| `resource_server_url` 和 Client 实际连接的 URL 不一致（尾斜杠、端口、协议） | 两者要完全一致 |
| `callback_handler` 没有原样传回 `state` / `iss` | SDK 比对失败会直接拒绝（这是 CSRF 和服务器混淆的防御） |
| 没保存 `client_info` | 每次运行都会注册一个新的 client |
| 用 `ctx.headers` 判断身份 | 请求头是客户端可控的输入，身份只能来自 `get_access_token()` |
| 在 stdio 或进程内测试里 `get_access_token()` 是 None | 授权只在 HTTP 上生效，要测授权就得走实际的 HTTP 连接（见 demo） |
| 没保存 PKCE 的 code_verifier | 自己实现 Client 时，跳转浏览器之前要先保存 |
| 拉取 JWKS 太频繁 | `PyJWKClient` 默认带缓存，注意 ttl 设置 |
| redirect_uri 用了 http | OAuth 2.1 要求 redirect URI 走 HTTPS，原生应用（IDE、CLI）例外：可以用回环地址的 http 重定向（RFC 8252 §7.3），推荐写 IP 字面量 `http://127.0.0.1:<port>/…` 或 `http://[::1]:<port>/…`，不推荐写 `localhost`（可能被解析到非回环接口）；授权服务器对回环地址要允许任意端口。原生应用也可以用私有 URI scheme |
| `ModuleNotFoundError: mcp.client.streamable_http.streamablehttp_client` | v2 改名为 `streamable_http_client`，也没有 `auth=` 参数了 |

---

## 14. 下一步

- 企业鉴权（Client Credentials、Enterprise-Managed Authorization、身份断言）：[企业鉴权（Client Credentials、企业管理授权与网关）](./03-enterprise-auth.md)
- 安全防御：[防范 Prompt 注入、Tool 投毒与路径越权](./04-security.md)
- 远程部署：[用 Streamable HTTP 部署远程 MCP](./01-remote-mcp.md)

## 参考资料

- MCP Authorization spec：https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- Client Registration（CIMD / DCR）：https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration
- Python SDK Authorization（服务端）：https://py.sdk.modelcontextprotocol.io/run/authorization/
- Python SDK OAuth clients：https://py.sdk.modelcontextprotocol.io/client/oauth-clients/
- RFC 9728 (Protected Resource Metadata)：https://datatracker.ietf.org/doc/html/rfc9728
- RFC 8707 (Resource Indicators)：https://www.rfc-editor.org/rfc/rfc8707.html
- RFC 9207 (Issuer Identification)：https://datatracker.ietf.org/doc/html/rfc9207
- OAuth 2.1 draft：https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1
