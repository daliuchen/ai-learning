# MCP Production 03：企业鉴权 —— Client Credentials、企业 IdP、网关

> **一句话**：企业场景下的 MCP 鉴权不只是用户走 OAuth——还要支持服务到服务（Client Credentials）、把企业现有 IdP（Okta / Azure AD / Google Workspace）作为 issuer、用 API Gateway 把 MCP Server 包起来统一鉴权。本篇讲三种企业落地模式。

---

## 1. 三种典型企业模式

### 1.1 模式 A：用户身份（标准 OAuth 2.1）
个人用户用 Claude Code / Cursor 接公司 MCP Server，按个人身份限权。**和 02-auth-oauth 一样**。

### 1.2 模式 B：服务到服务（Client Credentials）
后端 Agent / 自动化脚本调 MCP Server，没有"用户"。用 OAuth 2.1 的 Client Credentials grant。

### 1.3 模式 C：网关托管（最常见的企业部署）
内网 MCP Server 没鉴权；外部访问全走 API Gateway（Cloudflare Access / Pomerium / 自建），网关做 SSO + 把"X-User-Id" 等 header 注入下游。

---

## 2. 模式 B：Client Credentials

Spec 2025-11-25 SEP-1046 明确支持。

### 2.1 Token 获取

```http
POST /oauth/token HTTP/1.1
Host: auth.example.com
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=my-service
&client_secret=xxx
&scope=mcp:read mcp:write
&audience=https://mcp.example.com/mcp
```

返回：

```json
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "mcp:read mcp:write"
}
```

### 2.2 Python Client 端

```python
import httpx
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


async def get_service_token():
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://auth.example.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "my-service",
                "client_secret": "xxx",
                "scope": "mcp:read",
                "audience": "https://mcp.example.com/mcp",
            },
        )
        return r.json()["access_token"]


async def main():
    token = await get_service_token()

    async with streamablehttp_client(
        "https://mcp.example.com/mcp",
        headers={"Authorization": f"Bearer {token}"},
    ) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            ...
```

### 2.3 Token 缓存

不要每次请求都换新 token——按 `expires_in` 缓存到本地，过期前刷新：

```python
import time
from anyio import Lock

_token_cache = {"token": None, "expires_at": 0}
_lock = Lock()


async def get_token_cached():
    async with _lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]

        # 重新拿
        data = await fetch_token()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data["expires_in"]
        return _token_cache["token"]
```

---

## 3. 模式 C：API Gateway 托管

最常见的企业部署：内部 MCP Server 跑在 K8s 内、对外不开放；外部访问全走 Gateway。

### 3.1 架构

```
   [外部用户/Claude Code]
            │
            ▼
   ┌──────────────────┐
   │   API Gateway    │  ← 做 SSO / OAuth / mTLS / SAML
   │  (Cloudflare /   │  ← 注入 X-User-Id, X-User-Email, X-Groups
   │   Pomerium / ...)│
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │   MCP Server     │  ← 信任 Gateway 注入的 header
   │   (内网)         │  ← 按 X-User-Id 限权
   └──────────────────┘
```

### 3.2 Server 端代码

```python
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError


mcp = FastMCP("gw-protected")


def get_current_user(ctx: Context) -> str:
    """从 Gateway header 拿用户身份"""
    request = ctx.request_context.request
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        # 没经过 Gateway → 拒绝
        raise ToolError("未鉴权请求")
    return user_id


@mcp.tool()
async def get_my_orders(ctx: Context) -> list[dict]:
    user_id = get_current_user(ctx)
    return await fetch_orders(user_id)
```

### 3.3 Gateway 端配置

**Cloudflare Access** 配置示例：

```
Policy: "Engineering team only"
   - Include: Group "engineering@example.com"
   - Action: Allow

Header transform:
   - X-User-Id ← {{ user.email }}
   - X-User-Groups ← {{ user.groups | join: "," }}
```

**Pomerium**（自建）：

```yaml
routes:
  - from: https://mcp.example.com
    to: http://mcp-internal:8000
    pass_identity_headers: true
    policy:
      - allow:
          and:
            - email:
                is: "*@example.com"
            - mcp_authorized:
                is: true
```

### 3.4 模式 C 的好处

- Server 代码超简单（不写 OAuth）
- 鉴权策略集中管理
- 复用公司 SSO（Google / Okta / Azure AD）
- 审计日志统一

**劣势**：MCP Server 必须信任 Gateway——别人能直连内网就绕了。务必网络层隔离。

---

## 4. 企业 IdP 集成

### 4.1 Okta / Auth0
OAuth 2.1 + JWT，按 02-auth-oauth 配。在 IdP 里：

1. 注册 Application（Native / Web）
2. 配 audience = MCP Server URL
3. 配 scopes
4. 拿 issuer URL、jwks_uri

### 4.2 Azure AD / Entra ID
类似，但需要明确配置：

- App registration → API permissions → 申请 scope
- Token version v2.0
- Issuer：`https://login.microsoftonline.com/{tenant_id}/v2.0`

### 4.3 Google Workspace
Google Identity Platform 作为 OIDC issuer：

```
issuer: https://accounts.google.com
audience: <your-google-client-id>
```

### 4.4 自建 Keycloak / Authelia
Realm 配 client + scope + groups → 提供标准 OIDC endpoints。

---

## 5. 企业管理授权（Spec 内置）

2025-11-25 spec 引入了**企业管理授权**模式（SEP-990）——让 IT 管理员能在 MCP OAuth 流里集中控制：

- 强制使用特定 IdP（不允许用户自选）
- 禁用 Dynamic Client Registration
- 强制 step-up auth（高危操作要重新登录）
- 设备绑定

实现细节随 spec 演进。**企业部署时建议关注最新 spec 的 Authorization 章节**。

---

## 6. 服务身份 + 用户身份混合

复杂场景：Agent 用 Client Credentials 拿 service token，但要代表某个 user 调用 → **OAuth On-Behalf-Of (OBO)** 流程：

```
1. Agent 用自己的 token 调 IdP 的 token 端点，grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
2. 附 user 的 ID token
3. 拿到带 user 身份的 token
4. 用这个 token 调 MCP
```

Azure AD 直接支持；自建 IdP 要看是否实现 OBO。

---

## 7. 审计与合规

企业鉴权下必备的审计字段：

| 字段 | 来源 |
|------|------|
| `user_id` / `email` | Token claims |
| `client_id` | OAuth client |
| `tool_name` + `arguments_hash` | MCP 调用 |
| `timestamp` | 服务端时间 |
| `ip` | Request |
| `auth_method` | "oauth_user" / "client_credentials" / "gateway" |
| `result` | success / failed / error |

```python
import logging
import structlog
log = structlog.get_logger()


@mcp.tool()
async def sensitive_op(ctx: Context, target: str):
    request = ctx.request_context.request
    user = request.state.user
    log.info(
        "mcp.tool_call",
        tool="sensitive_op",
        user_id=user["sub"],
        target=target,
        ip=request.client.host,
    )
    return await do_sensitive(target)
```

---

## 8. 完整 demo：Client Credentials + JWT 校验

```python
# demos/production/03_enterprise_auth.py
"""服务到服务鉴权（Client Credentials）骨架"""
import os, time
import httpx

from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


_cache = {"token": None, "expires_at": 0}


async def get_token() -> str:
    if _cache["token"] and time.time() < _cache["expires_at"] - 60:
        return _cache["token"]
    async with httpx.AsyncClient() as c:
        r = await c.post(
            os.environ["OAUTH_TOKEN_URL"],
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["MCP_OAUTH_CLIENT_ID"],
                "client_secret": os.environ["MCP_OAUTH_CLIENT_SECRET"],
                "audience": os.environ["MCP_OAUTH_AUDIENCE"],
                "scope": "mcp:read",
            },
        )
        r.raise_for_status()
        data = r.json()
    _cache["token"] = data["access_token"]
    _cache["expires_at"] = time.time() + data["expires_in"]
    return data["access_token"]


async def main():
    token = await get_token()
    async with streamablehttp_client(
        os.environ["MCP_REMOTE_BASE_URL"] + "/mcp",
        headers={"Authorization": f"Bearer {token}"},
    ) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **Token 频繁重拿** | 加缓存按 expires_in - 60s 提前刷新 |
| **audience 配错** | Token 在 MCP Server 端被拒，错误码 401 |
| **Gateway header 被伪造** | MCP Server 必须只通过私有网络接收请求 |
| **Service token 无 user claim** | 企业 IdP 配 Client Credentials 时也要带 `client_id` claim 供审计 |
| **token 缓存按 client_id × audience** | 不同 audience 不能用同一个 token |

---

## 10. 下一步

- 📖 安全防御（Prompt 注入 / Tool 投毒） → [04-security.md](./04-security.md)
- 📖 可观测 → [05-debugging-inspector.md](./05-debugging-inspector.md)
- 📖 远程部署 → [01-remote-mcp.md](./01-remote-mcp.md)

## 参考资料

- Enterprise-Managed Authorization：https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization
- OAuth Client Credentials：https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials
- SEP-1046：https://modelcontextprotocol.io/seps/1046-support-oauth-client-credentials-flow-in-authoriza
- SEP-990 Enterprise IdP：https://modelcontextprotocol.io/seps/990-enable-enterprise-idp-policy-controls-during-mcp-o
