# MCP Client 04：Elicitation、Roots 与多轮请求（MRTR）

> Elicitation 让 Server 在执行过程中向用户要额外的输入。这个能力保留了下来，但 2026-07-28 改了传递方式：Server 不再反向发请求，而是在结果里返回问题，由 Client 带着答案重试。Roots 让 Client 告诉 Server 可以访问哪些目录，它已被弃用，但仍能用。这篇的重点是 Multi Round-Trip Requests（MRTR，多轮请求），它是 2026 版协议里最重要的新机制之一。

---

## 1. Server 为什么不能再回头问

在 2025-11-25 及更早的版本里，Server 处理 `tools/call` 时，可以沿着同一条连接主动给 Client 发 `elicitation/create`，等用户回答后再继续。这要求 Server 一直占着这条连接，也就是必须有状态，请求不能随便切到别的副本。

2026-07-28 删除了所有 Server → Client 的请求。Server 需要信息时，就在结果里返回：

```
Client → Server: tools/call provision {name:"orders"}                            (id=1)
Server → Client: { resultType:"input_required",
                   inputRequests:{ region:{ method:"elicitation/create", params:{message:"数据库放在哪个区域？", requestedSchema:{...}} } },
                   requestState:"<加密的不透明字符串>" }
        （Client 弹表单，用户答「eu-west-1」）
Client → Server: tools/call provision {name:"orders",                            (id=2，新 ID)
                   inputResponses:{ region:{action:"accept", content:{region:"eu-west-1"}} },
                   requestState:"<原样回传>" }
Server → Client: { resultType:"complete", content:[{type:"text", text:"已在 eu-west-1 创建数据库 'orders'"}] }
```

涉及的类型：

| 类型 | 含义 |
|------|------|
| `InputRequiredResult`（`resultType:"input_required"`） | Server 的中间结果，表示还需要这些信息 |
| `inputRequests` | 一个字典，键由 Server 命名，值是 `elicitation/create` / `sampling/createMessage`（已弃用）/ `roots/list`（已弃用）的请求体 |
| `inputResponses` | Client 重试时带上的答案，键和 `inputRequests` 对应 |
| `requestState` | Server 给出的不透明字符串，Client 必须原样传回（不能检查，也不能修改） |

几条规则：

- 重试是一个独立的请求，JSON-RPC 的 `id` 必须换新；`requestState` 只作用于这次重试，不能用在并行的其它请求上
- Server 不能问 Client 没声明支持的东西，比如 Client 没声明 elicitation，Server 就不能返回 elicitation 请求，否则就是 `-32021`
- `inputRequests` 和 `requestState` 至少要有一个；只有 `requestState` 时，Client 可以立即重试（意思是「还没完，再来一次」）
- 缺了必要的答案时，Server 应该再返回一次 `InputRequiredResult`，而不是报错
- `tools/call`、`prompts/get`、`resources/read` 都可以这样回答

### 安全：`requestState` 是客户端可控的输入

`requestState` 握在 Client 手里，所以它可能被篡改、可能已经过期，也可能是从别的调用里偷来的。规范要求 Server 对它做完整性保护，Python SDK 的 `MCPServer` 默认就做了：加密并认证，同时绑定以下信息：

- 时间窗口（默认 600 秒，每一轮重新封装）
- 已认证的主体（OAuth token 里的 client、issuer、subject）
- 发起的请求（方法、工具名或资源 URI、参数摘要）
- 被问的具体问题（问题文案改了就会重新问，不会复用旧答案）

默认密钥只存在于当前进程里，所以多实例部署、负载均衡或者重启之后，都需要共享密钥：

```python
from mcp.server.mcpserver import MCPServer, RequestStateSecurity
mcp = MCPServer("fleet", request_state_security=RequestStateSecurity(keys=[KEY]))   # 每个 >= 32 字节；keys[0] 签发，全部可验证，便于轮换
```

校验失败统一返回 `-32602 "Invalid or expired requestState"`，故意不区分具体原因。

---

## 2. Elicitation：中途询问用户

### 2.1 协议（form 模式）

Client 必须声明这个能力，在 Client 上注册 `elicitation_callback` 就等于声明：

```json
{"capabilities": {"elicitation": {"form": {}, "url": {}}}}
```

问题的请求体如下。字段名是 `requestedSchema`，不是 `schema`：

```json
{
  "method": "elicitation/create",
  "params": {
    "mode": "form",
    "message": "确认你的巴塞罗那行程：",
    "requestedSchema": {
      "type": "object",
      "properties": {
        "confirmBooking": {"type": "boolean", "description": "确认下单（机票+酒店 $3000）"},
        "seatPreference": {"type": "string", "enum": ["window", "aisle", "no_preference"]},
        "addInsurance": {"type": "boolean", "default": false}
      },
      "required": ["confirmBooking"]
    }
  }
}
```

答案：

```json
{"action": "accept", "content": {"confirmBooking": true, "seatPreference": "window", "addInsurance": false}}
```

`action` 有三种取值：`"accept"`（提交）、`"decline"`（拒绝）、`"cancel"`（关掉对话框）。拒绝不算错误，它意味着什么由工具自己决定。

在 2026 版里，上面的 `method` + `params` 放在 `inputRequests` 的某个键下；在 2025 版里则是一个独立请求。

### 2.2 Schema 的限制

Elicitation 的 schema 必须是扁平的 object，每个字段都是基本类型（string、number、integer、boolean 或字符串 enum），不能嵌套 object。在 Python SDK 里，嵌套的模型会在发出前就抛 `TypeError`，工具以 `Error executing tool xxx` 结束。毕竟这是在中途打断一个人，如果答案需要嵌套结构，它本来就应该是工具的参数。

### 2.3 URL 模式（带外交互）

如果要的东西不能经过模型或 Client，比如凭据、银行卡、OAuth 授权页，那就不要直接要数据，而是让用户去别处完成：

```json
{"method": "elicitation/create", "params": {"mode": "url", "message": "请在外部页面完成支付", "url": "https://pay.example.com/deposit/abc"}}
```

结果里只有 `action`，而且 `accept` 只表示用户同意打开链接，不代表已经完成。用户在外部完成操作后，Server 通过其它渠道（webhook、轮询）得知结果。

> 2026-07-28 的变化：`notifications/elicitation/complete` 通知和请求里的 `elicitationId` 都被删除了。Client 通过重试原请求来得知外部流程的结果；Server 需要关联时，把自己的标识放进 `requestState`。URL 模式本身也走 MRTR。

### 2.4 Python Server 端：推荐用 `Resolve(Elicit(...))`

把提问从工具函数体里挪到一个解析器里，工具参数用 `Annotated[T, Resolve(fn)]` 接收答案。解析器如果已经知道答案，就直接返回值；需要问的时候返回 `Elicit(message, Model)`。SDK 根据连接的协议版本选择通道（2025 直接发请求，2026 返回 `InputRequiredResult`），一份代码两代客户端都能用：

```python
from typing import Annotated
from pydantic import BaseModel, Field
from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, DeclinedElicitation, Elicit, ElicitationResult, Resolve

mcp = MCPServer("project-analyzer")

class Confirm(BaseModel):
    ok: bool = Field(description="确认继续？")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件")

def confirm_analysis(path: str) -> Elicit[Confirm]:       # 参数按名字对应工具的 path
    return Elicit(f"将分析目录 {path}。确认继续？", Confirm)   # 文本只能由稳定数据构造

@mcp.tool()
async def analyze(path: str, confirm: Annotated[ElicitationResult[Confirm], Resolve(confirm_analysis)]) -> str:
    match confirm:
        case AcceptedElicitation(data=Confirm(ok=True) as c): ...
        case DeclinedElicitation(): return "用户拒绝"
        case _: return "用户取消"
```

- `confirm` 不会出现在 `inputSchema` 里，模型没法填它
- 注解成 `ElicitationResult[Confirm]` 时，三种答案都交给你 `match`；注解成裸的 `Confirm` 时，只有 accept 才会进入工具函数体，decline/cancel 会自动让调用以错误结束
- 解析器可以只在需要时才问：比如目录是空的，就直接返回 `Confirm(ok=True)`，一次往返都不用
- 每一轮重试时，问题的内容必须完全相同（SDK 会把已经回答过的问题固定下来，文案变了就会重新问）。不要用时间戳、随机数、实时汇率去拼问题，否则每一轮都会被当作新问题，一直问到轮数上限
- 在同一个调用里，`Resolve(...)` 参数和手写的 `InputRequiredResult` 不能混用

### 2.5 老写法：`ctx.elicit()`（只能用在 2025 式连接上）

```python
result = await ctx.elicit(message="Would you like to try another date?", schema=AlternativeDate)   # schema 是 Pydantic 模型
if result.action == "accept": print(result.data)   # data 只在 accept 时存在，已按模型校验
```

URL 版本是 `ctx.elicit_url(message, url, elicitation_id)`（以及 `ctx.session.send_elicit_complete(...)`）。它们都是 Server → Client 的请求，在 2026-07-28 的连接上会抛 `NoBackChannelError`。写测试时要注意，`Client(mcp)` 默认协商到 2026，用了 `ctx.elicit()` 的工具在测试里会失败，要么改用 `Resolve`，要么给测试 Client 传 `mode="legacy"`。

### 2.6 手写 MRTR（低层或特殊场景）

不想用 `Resolve` 的话，工具也可以直接返回 `InputRequiredResult`，在重试的那一轮从 `ctx.input_responses` 读取答案：

```python
@mcp.tool()
async def provision(name: str, ctx: Context) -> str | InputRequiredResult:
    answer = (ctx.input_responses or {}).get("region")
    if not isinstance(answer, ElicitResult) or answer.content is None:
        return InputRequiredResult(input_requests={"region": ASK_REGION}, request_state="provision-v1")
    return f"已在 {answer.content['region']} 创建数据库 {name!r}"
```

只有 2026 版连接才有 `InputRequiredResult`，在 2025 版连接上返回它会得到 `-32603 Handler returned an invalid result`。同时服务两代客户端的 Server，要先检查 `ctx.protocol_version`。`prompts/get` 和资源模板函数也一样（静态资源不适用，因为没有 `Context`）。

### 2.7 Python Client 端

同一个 `elicitation_callback` 既能回答 2025 版的独立请求，也能回答 2026 版的多轮请求：

```python
from mcp import Client
from mcp.client import ClientRequestContext
from mcp.types import ElicitRequestParams, ElicitRequestURLParams, ElicitResult

async def on_elicit(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    if isinstance(params, ElicitRequestURLParams):             # URL 模式：给用户看链接，返回 action，不带 content
        print("请打开:", params.url)
        return ElicitResult(action="accept")
    print(params.message)                                       # 表单模式：真实应用用 params.requested_schema 渲染表单
    return ElicitResult(action="accept", content={"ok": True})

async with Client(url, elicitation_callback=on_elicit) as client:
    result = await client.call_tool("analyze", {"path": "."})   # 中间轮次对调用方不可见
```

`Client` 会自动跑重试循环：遇到 `InputRequiredResult` 时，把 `inputRequests` 里的每一项分派给对应的回调（elicitation、sampling、roots），带着答案和原样的 `request_state` 重试，直到拿到最终结果。轮数上限是 `input_required_max_rounds`（默认 10），Server 一直要信息的话会抛错；如果只有 `requestState` 没有问题，会短暂退避（从 50ms 倍增到 250ms）。`get_prompt` / `read_resource` 也一样。

有些情况需要自己驱动这个循环：问用户的进程和发起调用的进程不是同一个（分布式 Client），想审计每一轮，或者想用墙钟时间做超时：

```python
result = await client.session.call_tool("provision", {"name": "orders"}, allow_input_required=True)
while isinstance(result, InputRequiredResult):
    responses = {k: fulfil(req) for k, req in (result.input_requests or {}).items()}   # fulfil 里放你的 UI
    result = await client.session.call_tool("provision", {"name": "orders"},
        input_responses=responses, request_state=result.request_state, allow_input_required=True)
```

`request_state` 可以存到磁盘上，让流程在新进程里继续（仍然受 Server 端 TTL 和密钥有效期的限制）。

---

## 3. Roots：沙盒边界（已弃用）

> 规范原文是 "Roots are coordination, NOT security"。Roots 是 Client 告诉 Server「应该只在这些目录下工作」，并不强制执行。安全边界要靠操作系统权限、容器、AppArmor。

2026-07-28 把 Roots 标为 Deprecated（SEP-2577）：还能用，但不建议新设计依赖它。官方建议通过工具参数、资源 URI 或 Server 配置来传递目录。`notifications/roots/list_changed` 也同时被删除了。

如果还要用：

- Client 端：注册 `list_roots_callback`（签名 `async (context) -> ListRootsResult`），等于声明 `roots` 能力
- Server 端：用 `Resolve(ListRoots())` 注入 `ListRootsResult`（`roots[i].uri` 是 `file://` URI，可以带 `name`）：

```python
from mcp.server.mcpserver import ListRoots, Resolve
from mcp.types import ListRootsResult

def workspace_roots() -> ListRoots:
    return ListRoots()

@mcp.tool()
async def first_root(roots: Annotated[ListRootsResult, Resolve(workspace_roots)]) -> str:
    return str(roots.roots[0].uri) if roots.roots else "Client 没声明 roots"
```

设计上要注意两点：不要把 roots 当成强制约束，Server 访问文件时仍然要做路径边界检查，防止越权；没声明 `roots` 能力时会得到 `-32021`，SDK 不会发出请求。

---

## 4. Roots 与 Elicitation 对比

| 维度 | Roots（已弃用） | Elicitation（保留） |
|------|-----------|------------------|
| 内容 | 工作目录列表 | 向用户提问（表单 / URL） |
| 2026 版的传递方式 | 放在 `inputRequests` 里，Client 回 `ListRootsResult` | 放在 `inputRequests` 里，Client 回 `ElicitResult` |
| UI | 通常没有（IDE 自动维护） | 弹窗 / 表单 |
| 官方建议 | 改用参数 / URI / 配置 | 继续使用 |

---

## 5. 安全注意事项

| 风险 | 缓解 |
|------|------|
| 用 Elicitation 骗取敏感信息 | 规范明确禁止 Server 用表单模式收集密码、API key、银行卡；这类信息走 URL 模式的外部流程 |
| Elicitation 冒充系统提示 | UI 必须醒目地标明「这是 Server XXX 发起的请求」 |
| `requestState` 被篡改或重放 | SDK 默认会封装保护；多实例要共享 `keys`；涉及一次性动作（如扣费）时，服务端要自己保证至多执行一次 |
| Roots 没声明或只声明了一部分 | 不能靠 roots 做安全约束，仍然需要路径白名单 |
| URL 模式被用来钓鱼 | 跳转外部 URL 前校验合法性，并弹出警告 |
| 每轮重试的问题都在变 | 问题只用稳定数据构造，否则会一直问下去 |

---

## 6. 综合 demo：Resolve、Roots、手写 MRTR 与手动驱动

```python
# demos/server/04_roots_elicit.py
import asyncio
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from mcp import Client
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    ListRoots,
    Resolve,
)
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitRequestParams,
    ElicitResult,
    InputRequiredResult,
    ListRootsResult,
    Root,
    TextContent,
)

mcp = MCPServer("project-analyzer")


# ---------- 1) 推荐：Resolve(Elicit) —— 问题挂在参数上，SDK 按连接的协议版本选择传递方式 ----------
class Confirm(BaseModel):
    ok: bool = Field(description="确认继续？")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件")


def confirm_analysis(path: str) -> Elicit[Confirm]:
    # 注意：问题文本只能由工具参数等稳定数据构造；每一轮重试都必须渲染出完全相同的问题
    return Elicit(f"将分析目录 {path}。确认继续？", Confirm)


@mcp.tool()
async def analyze(path: str, confirm: Annotated[ElicitationResult[Confirm], Resolve(confirm_analysis)]) -> str:
    """分析一个目录（先问用户授权）"""
    match confirm:
        case AcceptedElicitation(data=Confirm(ok=True) as c):
            files = [p for p in Path(path).rglob("*") if p.is_file() and (c.include_hidden or not p.name.startswith("."))]
            return f"{path}: {len(files)} 个文件"
        case AcceptedElicitation():
            return "用户选择不分析"
        case DeclinedElicitation():
            return "用户拒绝"
        case _:
            return "用户取消"


# ---------- 2) Roots（已弃用，但仍可用）：同样用 Resolve ----------
def workspace_roots() -> ListRoots:
    return ListRoots()


@mcp.tool()
async def first_root(roots: Annotated[ListRootsResult, Resolve(workspace_roots)]) -> str:
    """返回 Client 声明的第一个工作目录（Roots 只是协作提示，不是安全边界）"""
    return str(roots.roots[0].uri) if roots.roots else "Client 没声明 roots"


# ---------- 3) 手写 MRTR：不走 Resolve，直接返回 InputRequiredResult（和 Resolve 不能混用于同一个工具）----------
ASK_REGION = ElicitRequest(
    params=ElicitRequestFormParams(
        message="数据库放在哪个区域？",
        requested_schema={"type": "object", "properties": {"region": {"type": "string"}}, "required": ["region"]},
    )
)


@mcp.tool()
async def provision(name: str, ctx: Context) -> str | InputRequiredResult:
    """手写多轮请求：第一轮返回问题，重试轮从 ctx.input_responses 读答案"""
    answer = (ctx.input_responses or {}).get("region")
    if not isinstance(answer, ElicitResult) or answer.content is None:
        return InputRequiredResult(input_requests={"region": ASK_REGION}, request_state="provision-v1")
    return f"已在 {answer.content['region']} 创建数据库 {name!r}"


# ---------- 演示 Client ----------
async def on_elicit(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    print(f"  [Client 回调] 收到提问: {params.message}")
    if "区域" in params.message:
        return ElicitResult(action="accept", content={"region": "eu-west-1"})
    return ElicitResult(action="accept", content={"ok": True, "include_hidden": False})


async def list_roots(context: ClientRequestContext) -> ListRootsResult:
    return ListRootsResult(roots=[Root(uri="file:///tmp/demo-workspace", name="demo")])  # type: ignore[arg-type]


async def main() -> None:
    here = str(Path(__file__).parent)
    for mode in ("auto", "legacy"):
        async with Client(mcp, mode=mode, elicitation_callback=on_elicit, list_roots_callback=list_roots) as client:
            print(f"== mode={mode} -> 协议 {client.protocol_version}")
            print("  analyze   :", (await client.call_tool("analyze", {"path": here})).content[0].text)
            print("  first_root:", (await client.call_tool("first_root", {})).content[0].text)

    # 手动驱动 MRTR：看清每一轮线上发生了什么（只有 2026 连接有这个机制）
    async with Client(mcp, elicitation_callback=on_elicit) as client:
        r = await client.session.call_tool("provision", {"name": "orders"}, allow_input_required=True)
        print("== 第 1 轮返回:", type(r).__name__, "| 要问:", list(r.input_requests), "| request_state 已加密:", r.request_state != "provision-v1")
        responses = {"region": ElicitResult(action="accept", content={"region": "ap-southeast-1"})}
        r = await client.session.call_tool("provision", {"name": "orders"}, input_responses=responses, request_state=r.request_state, allow_input_required=True)
        print("== 第 2 轮返回:", type(r).__name__, "|", r.content[0].text)
        # 自动循环版：Client 替你重试，调用方看不到中间轮
        print("== 自动循环  :", (await client.call_tool("provision", {"name": "orders"})).content[0].text)

    # 没注册回调：2026 里 SDK 的占位回调会拒绝，整个调用以错误结束
    async with Client(mcp) as client:
        try:
            await client.call_tool("analyze", {"path": here})
        except Exception as e:  # noqa: BLE001
            print("== 无回调    :", type(e).__name__, e)


if __name__ == "__main__":
    asyncio.run(main())
```

实际输出（目录路径因机器而异）：

```
== mode=auto -> 协议 2026-07-28
  [Client 回调] 收到提问: 将分析目录 .../demos/server。确认继续？
  analyze   : .../demos/server: 13 个文件
  first_root: file:///tmp/demo-workspace
== mode=legacy -> 协议 2025-11-25
  [Client 回调] 收到提问: 将分析目录 .../demos/server。确认继续？
  analyze   : .../demos/server: 13 个文件
  first_root: file:///tmp/demo-workspace
== 第 1 轮返回: InputRequiredResult | 要问: ['region'] | request_state 已加密: True
== 第 2 轮返回: CallToolResult | 已在 ap-southeast-1 创建数据库 'orders'
  [Client 回调] 收到提问: 数据库放在哪个区域？
== 自动循环  : 已在 eu-west-1 创建数据库 'orders'
== 无回调    : MCPError Client did not declare the form elicitation capability required by resolver '__main__:confirm_analysis'
```

同一份 `analyze` / `first_root` 代码，在 2026（多轮请求）和 2025（独立请求）两种连接上结果相同；手写的 `provision` 能看到每一轮的情况；没注册回调的 Client 会被 `-32021` 拒绝。

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| 在 2026 连接里用 `ctx.elicit()` | 会抛 `NoBackChannelError`，改用 `Resolve(Elicit(...))` |
| 用 `Client(mcp)` 测试时 `ctx.elicit()` 失败 | 进程内连接默认是 2026 版；改用 Resolve，或者传 `mode="legacy"` |
| `inputRequests` 里的问题每一轮都不一样 | 会一直问到 `input_required_max_rounds`；问题只用稳定数据构造 |
| 多实例部署后 MRTR 随机失败（`-32602 Invalid or expired requestState`） | 默认密钥只在进程内，需要配置共享的 `RequestStateSecurity(keys=[...])` |
| 在 2025 连接上返回 `InputRequiredResult` | 会得到 `-32603`；先检查 `ctx.protocol_version` |
| `Resolve` 参数和手写 `InputRequiredResult` 混用 | 一个工具只能二选一 |
| Elicitation schema 里有嵌套 object | 发出前就会抛 TypeError，保持扁平 |
| 用 Elicitation 收密码 | 规范禁止；用 URL 模式走 OAuth 或外部流程 |
| 没注册回调 | 注册即声明；否则 SDK 会用 `-32021` 或 "Elicitation not supported" 拒绝 |
| `action: accept` 但 content 是空的 | 用户可能跳过了可选字段，Server 端要处理缺省值 |
| 把 roots 当成安全约束 | 它只是约定，而且已经弃用，改用工具参数 |
| 写成 `params.schema`，或者在 2026 连接上调 `session.list_roots()` | 字段名是 `requestedSchema`；`list_roots()` 只适用于 2025 连接 |

---

## 8. 下一步

- 多 Server 聚合与客户端最佳实践：[多 Server 聚合与客户端最佳实践](./05-multi-server-best-practices.md)
- 安全细节：[防范 Prompt 注入、Tool 投毒与路径越权](../05-production/04-security.md)
- 长任务里的 `input_required`（Tasks 扩展和 MRTR 形状相同）：[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)

## 参考资料

- MRTR spec：https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr
- Elicitation spec：https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation
- Roots spec（已弃用）：https://modelcontextprotocol.io/specification/2026-07-28/client/roots
- Python SDK：Multi-round-trip https://py.sdk.modelcontextprotocol.io/handlers/multi-round-trip/ 、Elicitation https://py.sdk.modelcontextprotocol.io/handlers/elicitation/ 、Dependencies https://py.sdk.modelcontextprotocol.io/handlers/dependencies/
