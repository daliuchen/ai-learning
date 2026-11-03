# MCP Production 03：企业鉴权（Client Credentials、企业管理授权与网关）

> 企业里的 MCP 鉴权不只有用户走 OAuth 这一种情况。还要支持没有用户参与的服务间调用（Client Credentials），让企业 IdP 统一决定谁能连哪个 MCP Server（Enterprise-Managed Authorization，也就是身份断言），以及用 API Gateway 把 MCP Server 包起来统一鉴权。前两种官方都已经做成了扩展（`io.modelcontextprotocol/oauth-client-credentials`、`io.modelcontextprotocol/enterprise-managed-authorization`），Python SDK 2.x 里各有现成的 Client provider。

---

## 1. 四种常见的企业模式

| 模式 | 场景 | 机制 |
|------|------|------|
| A 用户身份 | 员工用 Claude Code / Cursor 连接公司的 MCP Server | 标准 OAuth 2.1（见[远程 MCP 的 OAuth 2.1 鉴权](02-auth-oauth.md)） |
| B 服务到服务 | 后台任务、CI、守护进程，没有用户参与 | OAuth Client Credentials 扩展 |
| C 企业管理授权 | IT 想在 IdP 里统一决定谁能访问哪些 MCP Server | Enterprise-Managed Authorization 扩展（SEP-990，ID-JAG） |
| D 网关托管 | 内网 MCP Server 本身不做鉴权，外部访问全部经过 Gateway | Gateway 做 SSO，下游信任 Gateway |

---

## 2. 模式 B：Client Credentials（扩展）

### 2.1 它是什么

标准的 MCP 授权需要用户在浏览器里点「允许」。没有用户的场景（定时任务、CI、服务间集成）用 `io.modelcontextprotocol/oauth-client-credentials` 扩展：Client 用应用级的凭据直接向授权服务器证明身份，换取 access token，不需要浏览器，也不需要用户。凭据有两种格式。

第一种是 JWT Bearer 断言，也是推荐的做法：Client 用自己的私钥签一个 JWT 作为凭证（RFC 7523 §2.2 `private_key_jwt`），授权服务器用登记过的公钥验证。JWT 里包含 `iss` = `sub` = client ID、`aud` = 授权服务器的 token 端点、`exp`、`iat`。它有效期短，签名密钥也不用在网络上传输。

第二种是 Client Secret，即 `client_id` 加 `client_secret`。用起来简单，但 secret 是长期有效的凭据，一旦泄露，攻击者可以一直冒充你的应用，直到你轮换为止。要放在 secrets manager 里，定期轮换，并且只给最小权限。

token 请求如下（secret 默认用 HTTP Basic 传递，即 `client_secret_basic`）：

```http
POST /token HTTP/1.1
Authorization: Basic cmVwb3J0aW5nLWFnZW50OnMzY3JldA==
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&scope=reports:read&resource=https://mcp.example.com/mcp
```

### 2.2 Python Client 端（SDK 2.x）

不要再自己手写 `httpx.post(token_url)` 再拼 `Authorization` 头了，直接用现成的 provider（它是一个 `httpx2.Auth`）：

```python
import httpx2
from mcp import Client
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client

oauth = ClientCredentialsOAuthProvider(
    server_url="https://mcp.example.com/mcp",
    storage=storage,                                   # TokenStorage：4 个 async 方法
    client_id="reporting-agent",
    client_secret=os.environ["CLIENT_SECRET"],         # 不要进仓库
    scope="reports:read",                               # 空格分隔字符串（v1 的 scopes= 已改名 scope=）
    issuer="https://auth.example.com/",                 # 颁发这些凭据的授权服务器；用其元数据里 issuer 的原值；不写已弃用，3.0 起必填
    # token_endpoint_auth_method="client_secret_post",  # 有的授权服务器只接受 post
)
async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
    async with Client(streamable_http_client("https://mcp.example.com/mcp", http_client=http_client)) as client:
        ...
```

provider 会替你完成发现（PRM → 授权服务器元数据）、换取 token、缓存到 `storage`、过期后重新获取。`issuer` 参数的作用是：token 请求只会根据这个 issuer 的元数据来构造，如果 MCP Server 指向了别的授权服务器，会直接抛 `OAuthFlowError`，防止你的 secret 被发给错误的授权服务器。这个流程没有注册步骤（跳过动态注册）。

用 JWT 代替 secret：

```python
from mcp.client.auth.extensions.client_credentials import PrivateKeyJWTOAuthProvider, SignedJWTParameters

oauth = PrivateKeyJWTOAuthProvider(
    server_url="https://mcp.example.com/mcp", storage=storage, client_id="reporting-agent",
    assertion_provider=SignedJWTParameters(issuer="reporting-agent", subject="reporting-agent", signing_key=PRIVATE_KEY).create_assertion_provider(),
    issuer="https://auth.example.com/",
)
```

（可以用 `static_assertion_provider(token)` 包一个预先签好的 JWT。v1 的 `RFC7523OAuthClientProvider` / `JWTParameters` 已移除，按上面的 provider 替换即可。）

### 2.3 Token 缓存

provider 已经把 token 存进 `storage`，并在过期时刷新。如果你自己实现 `TokenStorage`，可以存到磁盘、Keychain 或 Redis，但要按 `client_id × audience(resource)` 分开缓存，不同资源的 token 不能混用。

### 2.4 完整可运行的 demo（授权服务器、MCP Server、Client 都在一个文件里）

为了在没有 Okta 或 Keycloak 的机器上也能看到完整流程，下面这个文件里自带了一个迷你授权服务器（只有元数据端点和 `/token`）。MCP Server 用 `TokenVerifier` 校验它签发的 JWT，Client 用 `ClientCredentialsOAuthProvider` 自动完成发现、换 token、调用：

```python
# demos/production/03_enterprise_auth.py
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
```

实际输出：

```
tools : ['nightly_report']
call  : report for caller=reporting-agent
token 已缓存在 storage: True
```

这里有两个容易踩的点：

- 授权服务器元数据（RFC 8414）里的 `authorization_endpoint` 是必填字段，即使 client_credentials 用不到它。缺了的话，SDK 会认为元数据无效，报 `OAuthFlowError: No authorization server metadata discovered for configured issuer ...`
- `AnyHttpUrl` 会给 issuer 补上尾斜杠（PRM 里是 `http://127.0.0.1:9100/`），所以元数据里的 `issuer` 和 provider 的 `issuer=` 必须是同一个字符串

---

## 3. 模式 C：企业管理授权（Enterprise-Managed Authorization，SEP-990）

### 3.1 它解决什么问题

标准模式下，每个用户要给每个 MCP Server 各授权一次。在企业里这既麻烦又有安全缺口：员工不该需要理解每个 Server 的授权细节；安全团队没办法强制执行统一的访问策略；新员工入职要手动授权几十个服务，离职时又要逐个撤销。Enterprise-Managed Authorization 让企业 IdP（Okta、Microsoft Entra ID 或自建的）成为唯一的决策方，IT 管理员在 IdP 里集中管理哪些员工能在什么条件下访问哪些 MCP Server。

### 3.2 流程：ID-JAG

Client 先向企业 IdP 申请一种特殊的令牌 ID-JAG（Identity Assertion JWT Authorization Grant），再拿它去 MCP Server 的授权服务器换取 access token：

```
1. 用户在企业 IdP 登录（OIDC） → Client 拿到 ID Token
2. Client 用 ID Token 向 IdP 做 RFC 8693 token exchange，换 ID-JAG   ← IdP 在这里评估策略；不允许就不发
3. Client 把 ID-JAG 当 RFC 7523 jwt-bearer 授权发给 MCP 授权服务器 → 拿到 MCP access token
4. Client 带着 access token 调 MCP Server（MCP Server 本身一点不用改）
```

这样做的好处是：策略集中管理（组、角色、条件访问）；单点登录，登录一次就能用所有被批准的 Server；集中撤销，在 IdP 里撤销后立即对所有 MCP Client 生效；并且便于审计。

### 3.3 Python SDK

SDK 只实现第 3 步，也就是它自己发出的那个请求。第 2 步调用的是你的 IdP 的 API，SDK 不碰 IdP，你在一个 async 回调里自己完成：

```python
from mcp.client.auth.extensions.identity_assertion import IdentityAssertionOAuthProvider

async def fetch_id_jag(audience: str, resource: str) -> str:
    # 真实实现：向企业 IdP 做 RFC 8693 token exchange，换取针对这个 audience / resource 的 ID-JAG
    return await my_idp.exchange_for_id_jag(id_token, audience=audience, resource=resource)

oauth = IdentityAssertionOAuthProvider(
    server_url="https://mcp.example.com/mcp",
    storage=storage,
    client_id="finance-agent", client_secret="...",        # 预先在 MCP 授权服务器登记的 client
    issuer="https://auth.example.com/",                     # MCP 授权服务器
    assertion_provider=fetch_id_jag,                        # 每次换 token 都会调一次：ID-JAG 是一次性的
    scope="notes:read",
)
# 用法和其它 provider 完全一样：httpx2.AsyncClient(auth=oauth) → streamable_http_client → Client
```

授权服务器那一端需要额外接受 `jwt-bearer` 授权类型，MCP Server（资源服务器）用的还是[远程 MCP 的 OAuth 2.1 鉴权](02-auth-oauth.md)里那个 `TokenVerifier`。官方的 Identity assertion 页面有完整可运行的 Client 和授权服务器两端示例（运行它们需要搭一个 IdP）。

> 其它客户端是否支持这两个扩展，可以查官方的 Extension Support Matrix（modelcontextprotocol.io/extensions/client-matrix）。

---

## 4. 模式 D：API Gateway 托管

这是企业里最常见的部署方式：内部 MCP Server 跑在 K8s 集群内，不对外开放，外部访问全部经过 Gateway。

### 4.1 架构

```
   [外部用户/Claude Code]
            │
            ▼
   ┌──────────────────┐
   │   API Gateway    │  ← SSO / OAuth / mTLS / SAML
   │  (Cloudflare /   │  ← 把身份传给下游
   │   Pomerium / ...)│
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │   MCP Server     │  ← 信任 Gateway
   │   (内网)         │
   └──────────────────┘
```

### 4.2 不要直接信任 `X-User-Id` 这种明文请求头

常见的做法是让 Gateway 注入 `X-User-Id`，Server 里用 `ctx.request_context.request.headers.get("X-User-Id")` 读取。问题在于 `ctx.headers` 是客户端可以控制的输入，官方文档反复强调，请求头适合放 locale、功能开关，绝对不能拿来当身份。只要存在任何一条能绕过 Gateway 直连的路径（K8s 里别的 Pod、配置有误的 Service），攻击者就可以自己填这个头。更稳妥的做法有两种：

1. Gateway 传一个签名的 JWT（或者在 Gateway 终止 OAuth 后，把 access token 转发给后端），MCP Server 用 `TokenVerifier` 验证签名和 `aud`，再用 `get_access_token()` 获取身份，和[远程 MCP 的 OAuth 2.1 鉴权](02-auth-oauth.md)的做法完全一样
2. 如果一定要用请求头，就必须在网络层强制隔离（NetworkPolicy 或私有网络，只有 Gateway 能访问 MCP Server），并且 Gateway 要覆盖客户端自带的同名请求头，而不是追加

### 4.3 Gateway 端配置

Cloudflare Access：

```
Policy: "Engineering team only"
   - Include: Group "engineering@example.com"
   - Action: Allow
```

Pomerium（自建）：

```yaml
routes:
  - from: https://mcp.example.com
    to: http://mcp-internal:8000
    pass_identity_headers: true      # 传 Pomerium 签名的身份 JWT（X-Pomerium-Jwt-Assertion），Server 验签
    policy:
      - allow:
          and:
            - email:
                is: "*@example.com"
```

（配置项会随版本变化，以各产品的文档为准。）

### 4.4 模式 D 的好处与代价

好处是 Server 代码很简单，鉴权策略集中管理，能复用公司的 SSO，审计日志也统一。代价是 MCP Server 必须信任 Gateway，所以网络层隔离是前提；Host 白名单（01 第 7 节）要加上 Gateway 对外的域名，或者在代理已经控制了 `Host` 头的情况下关掉 DNS rebinding 检查。

2026 版协议还给网关模式带来了一个好处：每个请求都带 `Mcp-Method` / `Mcp-Name` 头，Gateway 不用解析 body 就能按方法或工具名做授权和限流。

---

## 5. 对接企业 IdP

### 5.1 Okta / Auth0
用 OAuth 2.1 + JWT，按[远程 MCP 的 OAuth 2.1 鉴权](02-auth-oauth.md)的方式配置。在 IdP 里注册一个 Application（Native 或 Web），audience 设成 MCP Server 的 URL，配置好 scopes，然后拿到 issuer URL 和 jwks_uri。Auth0 用自己的 API identifier 作为 audience，这时要设置 `validate_token_resource=False`，在 `TokenVerifier` 里自己检查 `aud`。

### 5.2 Azure AD / Entra ID
在 App registration 里配置 API permissions 并申请 scope，Token version 选 v2.0，Issuer 是 `https://login.microsoftonline.com/{tenant_id}/v2.0`，audience 是应用 ID（同样要关掉 `validate_token_resource`，自己检查 `aud`）。

### 5.3 Google Workspace
用 Google Identity Platform 作为 OIDC issuer：`issuer: https://accounts.google.com`，audience 是你的 Google client id。

### 5.4 自建 Keycloak / Authelia
在 Realm 里配置 client、scope 和 groups，它们会提供标准的 OIDC 端点。

---

## 6. 服务身份与用户身份混合

Agent 用 Client Credentials 拿到 service token，但需要代表某个用户去调用时，用 OAuth On-Behalf-Of（OBO）或 RFC 8693 token exchange：

```
1. Agent 带着 user 的 token / ID token 去 IdP 的 token 端点做交换
2. 拿到带 user 身份、且 audience 是这个 MCP Server 的 token
3. 用这个 token 调 MCP
```

Azure AD 直接支持这个流程，自建的 IdP 要看有没有实现。收到的 token 绝对不要原样转发给下游 API（这会造成 confused deputy 问题），下游需要单独的 token。

---

## 7. 审计与合规

企业鉴权下必须记录的审计字段：

| 字段 | 来源 |
|------|------|
| `user_id` / `email` / `subject` | `get_access_token().subject` |
| `client_id` | `get_access_token().client_id` |
| `tool_name` + `arguments_hash` | MCP 调用 |
| `timestamp` | 服务端时间 |
| `ip` | `ctx.headers` 或代理传来的真实 IP（注意来源是否可信） |
| `auth_method` | "oauth_user" / "client_credentials" / "id_jag" / "gateway" |
| `result` | success / failed / error |

```python
import structlog
from mcp.server.auth.middleware.auth_context import get_access_token
log = structlog.get_logger()      # 输出到 stderr，交给采集器

@mcp.tool()
async def sensitive_op(target: str) -> str:
    t = get_access_token()
    log.info("mcp.tool_call", tool="sensitive_op", subject=t.subject if t else None, client_id=t.client_id if t else None, target=target)
    return await do_sensitive(target)
```

（也可以在 `MCPServer(middleware=[...])` 里统一处理，见[用 Streamable HTTP 部署远程 MCP](01-remote-mcp.md)第 8 节。）

---

## 8. 常见坑

| 坑 | 排查 |
|----|------|
| 手写 `httpx.post` 换 token 再塞进请求头 | 用 `ClientCredentialsOAuthProvider` / `PrivateKeyJWTOAuthProvider`，缓存、刷新和 issuer 校验它都会做 |
| `scopes=` 报错 | v2 里改名为 `scope=`（用空格分隔的字符串） |
| `OAuthFlowError: No authorization server metadata discovered for configured issuer` | 授权服务器元数据缺少必填字段（`authorization_endpoint`），或者元数据里的 `issuer` 和 provider 的 `issuer=` 不是同一个字符串（注意尾斜杠） |
| 没传 `issuer=` | 已弃用，3.0 起必填。不传就等于允许 MCP Server 把你引到任意授权服务器 |
| Client secret 提交进了仓库，或者长期不更换 | 放进 secrets manager 并定期轮换；能用 JWT 断言就用 JWT |
| audience 配错 | token 在 MCP Server 端被拒（401）；IdP 用自有 audience 时，关掉 `validate_token_resource` 并自己检查 `aud` |
| Gateway 的请求头被伪造 | 不要信任明文请求头；改传签名的 JWT，或者做网络隔离，并让 Gateway 覆盖同名请求头 |
| Service token 里没有用户信息 | Client Credentials 拿到的 token 代表应用而不是用户；需要审计到具体的人，就用 OBO 或 ID-JAG |
| token 缓存没按 client_id × audience 区分 | 不同 audience 不能共用同一个 token |
| 重复使用 ID-JAG | ID-JAG 是一次性的，每次换 token 都要重新申请 |

---

## 9. 下一步

- 安全防御（Prompt 注入、Tool 投毒）：[防范 Prompt 注入、Tool 投毒与路径越权](./04-security.md)
- 可观测：[调试与可观测（Inspector、日志与追踪）](./05-debugging-inspector.md)
- 远程部署：[用 Streamable HTTP 部署远程 MCP](./01-remote-mcp.md)

## 参考资料

- Enterprise-Managed Authorization：https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization
- OAuth Client Credentials：https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials
- 扩展支持矩阵：https://modelcontextprotocol.io/extensions/client-matrix
- Python SDK OAuth clients：https://py.sdk.modelcontextprotocol.io/client/oauth-clients/
- Python SDK Identity assertion：https://py.sdk.modelcontextprotocol.io/client/identity-assertion/
- SEP-990：https://github.com/modelcontextprotocol/modelcontextprotocol/issues/990
