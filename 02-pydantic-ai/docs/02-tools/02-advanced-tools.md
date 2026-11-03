# Pydantic AI 02-02：Advanced Tool Features 高级工具特性

> 上一篇只讲了「把函数装饰成工具」。实际项目里还会碰到更多需求：按角色启用或隐藏工具，把图片、截图交回模型，危险操作先等人批准，有些数据只给应用代码看、不给模型看。这篇把这些进阶用法逐个过一遍。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 进阶能力总览

| 能力 | 关键词 | 作用 |
|------|--------|--------|
| 动态启用 | `prepare` | 每一步调用前改 / 隐藏 tool def |
| Agent 级 prepare | `PrepareTools` capability | 一次改一批工具 |
| 多模态返回 | `ImageUrl` / `BinaryContent` / `DocumentUrl` | 工具返回图片、PDF 给模型看 |
| 返回值分层 | `ToolReturn` | return_value / content / metadata 分开 |
| 严格 schema | OpenAI `strict=True` | 字段不漏、不乱编 |
| 重试控制 | `retries` / `max_retries` | 多层覆盖 |
| 人在回路 | `requires_approval=True` | DeferredToolRequests |
| 参数预校验 | `args_validator=` | 跑工具前先验 |
| 超时控制 | `tool_timeout=` | 工具卡住自动重试 |
| 失败反馈 | `ToolFailed` | 做完了但失败，不耗重试预算 |
| 选工具策略 | `tool_choice` | 禁用 / 强制 / 限定模型能用的工具 |
| 工具太多 | `defer_loading` + `ToolSearch` | 工具先藏起来，按需搜索 |
| 参数约束 | `Annotated[..., Field(...)]` | JSON Schema 自动带约束 |

---

## 2. 动态启用：`prepare` 钩子

`prepare` 在每一步执行前被调用，签名固定：

```python
async def prepare(
    ctx: RunContext[DepsType],
    tool_def: ToolDefinition,
) -> ToolDefinition | None:
    ...
```

返回 `None`，这一步就隐藏该工具；返回 `ToolDefinition`，就用这个（可能改过的）定义。

### 2.1 按 deps 启用工具

```python
from pydantic_ai import Agent, RunContext, ToolDefinition

agent = Agent('openai:gpt-6.1-sol', deps_type=int)

async def only_if_42(
    ctx: RunContext[int], tool_def: ToolDefinition
) -> ToolDefinition | None:
    return tool_def if ctx.deps == 42 else None

@agent.tool(prepare=only_if_42)
def hitchhiker(ctx: RunContext[int], answer: str) -> str:
    """Reveal the meaning of life."""
    return f'{ctx.deps} → {answer}'

# deps != 42 时模型根本看不到 hitchhiker 这个工具
agent.run_sync('What is the meaning of life?', deps=10)   # 工具被隐藏
agent.run_sync('What is the meaning of life?', deps=42)   # 工具可用
```

### 2.2 按角色改 description

`prepare` 还能改 description 和参数 schema，同一个函数对不同角色呈现不同的样子：

```python
from dataclasses import replace

async def role_aware(ctx: RunContext[str], td: ToolDefinition) -> ToolDefinition | None:
    if ctx.deps == 'admin':
        return replace(td, description='[ADMIN] Delete a user by id. Use with extreme caution.')
    if ctx.deps == 'user':
        return replace(td, description='List your own user info only.')
    return None   # guest 看不到这个工具
```

这里有个容易踩的坑，官方文档也专门提醒过：**在拿到的定义上改，不要新建一个。** `ToolDefinition` 除了名字和 schema，还有 `kind`、`strict`、`sequential`、`metadata`、`timeout`、`defer_loading` 等预填好的字段，其中 `kind` 负责把「这个工具需要审批」带进运行。在 `prepare` 里用 `ToolDefinition(name=..., ...)` 新建一个，没传的字段会被重置成默认值，需要审批的工具就悄悄变成直接执行了。用 `dataclasses.replace(td, ...)` 保留其它字段，或者直接改属性（`ToolDefinition` 不是 frozen）都行。如果不想依赖每个 `prepare` 函数都记得保住 `kind`，可以在 toolset 层用 `ApprovalRequiredToolset` / `.approval_required(...)` 要求审批。

### 2.3 Agent 级 prepare：`PrepareTools` capability

要对所有工具统一做一件事（比如在 OpenAI 上给所有工具开 `strict=True`），用 `PrepareTools`：

```python
from pydantic_ai import Agent, RunContext, ToolDefinition
from pydantic_ai.capabilities import PrepareTools
from dataclasses import replace

async def strict_on_openai(
    ctx: RunContext[None], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition] | None:
    if ctx.model.system == 'openai':
        return [replace(td, strict=True) for td in tool_defs]
    return tool_defs

agent = Agent('openai:gpt-6.1-sol', capabilities=[PrepareTools(strict_on_openai)])
```

执行顺序：先跑每个工具自己的 `prepare`，留下来的工具集合再交给 Agent 级的 `PrepareTools`。

---

## 3. 多模态返回：让工具把图片交给模型

工具除了字符串，也可以直接返回图片、PDF 或二进制内容：

```python
from pydantic_ai import Agent, ImageUrl, BinaryContent, DocumentUrl

agent = Agent('openai:gpt-6.1-sol')

@agent.tool_plain
def get_company_logo() -> ImageUrl:
    """Return the company logo URL."""
    return ImageUrl(url='https://example.com/logo.png')

@agent.tool_plain
def get_handbook() -> DocumentUrl:
    """Return employee handbook PDF."""
    return DocumentUrl(url='https://example.com/handbook.pdf')

@agent.tool_plain
def get_screenshot() -> BinaryContent:
    """Take a screenshot and return raw bytes."""
    png_bytes = b'\x89PNG\r\n...'
    return BinaryContent(data=png_bytes, media_type='image/png')
```

支持图片输入的模型会看到图片并接着推理；不支持的会报错或被降级。各家对工具结果里带文件的支持也不一样：有的 provider 只允许把文件放进后续的用户消息，不能放在工具结果本身里，Pydantic AI 会按 provider 的能力决定放在哪。千问的 OpenAI 兼容端点不接受文档（PDF）部件，图片可以。

---

## 4. `ToolReturn`：返回值、富内容、metadata 分开

普通工具只返回一个值。有时你需要同时做三件事：

1. 给模型一个简短结果（"Clicked at (100, 200)"）
2. 附上富内容（操作前后的截图）让模型看
3. 带一份只给应用代码用、模型看不到的 metadata（坐标、耗时）

`ToolReturn` 就是为这种情况准备的：

```python
from pydantic_ai import Agent, BinaryContent, ToolReturn

@agent.tool_plain
def click_and_capture(x: int, y: int) -> ToolReturn:
    """Click at coordinates and show before/after screenshots."""
    before = BinaryContent(data=b'\x89PNG', media_type='image/png')
    after = BinaryContent(data=b'\x89PNG', media_type='image/png')
    return ToolReturn(
        return_value=f'Clicked at ({x}, {y})',     # 这一段进 tool result
        content=['Before:', before, 'After:', after],  # 这一段作为新 user message
        metadata={'coordinates': {'x': x, 'y': y}},    # LLM 完全看不到
    )
```

三部分各自去哪：

| 字段 | 进 LLM？ | 形式 |
|------|----------|------|
| `return_value` | 是 | 标准 ToolReturnPart |
| `content` | 是 | 额外插一条 user message |
| `metadata` | 否 | 只在 `all_messages()` 里给业务代码用 |

浏览器操作类的 Agent 很适合这样用：每一步给模型看截图，DOM 树留给业务监控。

---

## 5. 严格 schema：`strict=True`

在 OpenAI 的 [Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs) 模式下，工具可以开 `strict=True`，保证模型填齐字段、不编造新字段：

```python
@agent.tool_plain(strict=True)
def create_user(name: str, email: str, age: int) -> dict:
    """Create a user."""
    return {'id': 1, 'name': name, 'email': email, 'age': age}
```

几点限制：

- `strict=True` 要求 schema 落在 OpenAI 支持的子集里（所有字段都 required、不能有带默认值的复杂类型等）
- Anthropic / Gemini 没有对应机制，开了会被忽略，不报错
- 同时对接多个 provider 时，用 `PrepareTools` 按模型来源动态开关更干净（见 2.3）

---

## 6. 工具出错、重试与失败反馈

先回顾三种结局（详见[Function Tools 函数工具](01-function-tools.md)第 8 节）：`ModelRetry` 让模型改了再试，消耗预算；`ToolFailed` 表示做完但失败，不消耗预算；其它异常直接中断运行。重试预算可以在多个层级设置：

```python
# 1) Agent 级
agent = Agent('openai:gpt-6.1-sol', retries={'tools': 2})

# 2) Toolset 级
toolset = FunctionToolset(max_retries=5)

# 3) 工具级
@agent.tool(retries=3)
def my_tool(...): ...

# 4) Tool() 构造
Tool(my_func, max_retries=4)
```

优先级：工具级 > toolset 级 > override > 运行参数 > agent 默认 > 内置默认值 1。预算按工具分别计数，超过次数抛 `UnexpectedModelBehavior('Tool ... exceeded max retries count of N')`，生产代码里最好 `try ... except UnexpectedModelBehavior` 接住。

### 并行与 `sequential`

模型在一次回复里发出多个工具调用时，async 工具默认并行执行。工具可以标 `sequential=True`，V2 里它的含义是屏障：这个工具单独跑，同一次回复里的其它工具在它之前和之后照样并行。

### Tool Choice：控制模型能用哪些工具

`model_settings={'tool_choice': ...}`：

| 值 | 效果 |
|----|------|
| `'auto'`（默认） | 模型自己决定 |
| `'none'` | 禁用函数工具（仍可用输出工具 / 纯文本） |
| `'required'` | 强制必须调用函数工具（会排除输出工具，所以通常通过 capability 动态设置，或用 direct 请求） |
| `['tool_a', ...]` | 强制调用点名的工具之一（静态列表必须包含一个输出工具，否则运行结束不了） |
| `ToolOrOutput(function_tools=[...])` | 限定能用的函数工具，同时保留所有输出工具 |

```python
from pydantic_ai.settings import ToolOrOutput

agent.run_sync('Hello', model_settings={'tool_choice': 'none'})
agent.run_sync('Hello', model_settings={'tool_choice': ToolOrOutput(function_tools=['get_weather'])})
```

### 工具太多：`defer_loading` 与工具搜索

接上几个 MCP 服务器后，工具定义可能有几十个、上万 token。模型还没开始干活就花掉不少输入，选工具的准确率也会下降。给工具标上 `defer_loading=True`，它一开始就不会出现在模型面前，框架会自动注入 `ToolSearch` capability：支持的模型用厂商原生的工具搜索，其它模型用本地的 `search_tools` 工具，按关键词匹配名字和描述。官方建议在这几种情况下用：工具超过 10 个左右，工具定义超过约 1 万 token，或者工具分属不同领域。常用的几个保持直接加载；工具名用统一前缀（`github_*`），描述里写上用户可能会搜的词。

---

## 7. 工具超时

```python
agent = Agent('openai:gpt-6.1-sol', tool_timeout=30)  # agent 默认 30 秒

@agent.tool_plain(timeout=5)        # 单工具覆盖为 5 秒
async def fast_tool() -> str:
    await asyncio.sleep(1)
    return 'done'
```

超时按可重试的失败处理：模型收到一条 `outcome='retried'` 的工具结果 `"Timed out after 5 seconds."`，并计入该工具的重试预算。`tool_timeout` / `timeout` 由 `FunctionToolset` 执行，MCP 服务器和外部 toolset 提供的工具不受这两个设置约束（MCP 有自己的超时配置）。

---

## 8. 人在回路：`requires_approval=True`

删数据、转账、发邮件这类操作，应该等人点了同意再执行：

```python
from pydantic_ai import Agent, DeferredToolRequests, RunContext

agent = Agent(
    'openai:gpt-6.1-sol',
    deps_type=int,
    output_type=[str, DeferredToolRequests],   # ← 关键
)

@agent.tool(requires_approval=True)
def delete_user(ctx: RunContext[int], user_id: int) -> str:
    """Permanently delete a user."""
    return f'Deleted {user_id}'

result = agent.run_sync('Delete user 42', deps=0)

if isinstance(result.output, DeferredToolRequests):
    # 模型决定调 delete_user 了，但暂停等批
    print('Approval needed for:', result.output.approvals)
    # → 拿到批准后再 result = agent.run_sync(..., deferred_tool_results=...)
```

详细用法见 [Deferred Tools 与人审批](../03-advanced/08-deferred-tools.md)。这里先记两点：把 `DeferredToolRequests` 放进 `output_type`，运行会停下来把决定交给调用方；V2 另外提供了 `HandleDeferredToolCalls` capability，可以在运行中途用 handler 就地拿到审批结果，不中断运行。

### 8.1 参数预校验：`args_validator=`

`requires_approval` 常和 `args_validator` 一起用：先确认参数合法，再交给人批，免得审批人面对一堆明显不合法的请求：

```python
from pydantic_ai import ModelRetry

def validate_sum_limit(ctx: RunContext[int], x: int, y: int) -> None:
    if x + y > ctx.deps:
        raise ModelRetry(f'Sum must not exceed {ctx.deps}')

@agent.tool(requires_approval=True, args_validator=validate_sum_limit)
def add_numbers(ctx: RunContext[int], x: int, y: int) -> int:
    return x + y
```

---

## 9. 参数约束：`Annotated` + `Field`

约束可以直接写在类型注解里：

```python
from typing import Annotated
from pydantic import Field

@agent.tool_plain
def create_post(
    title: Annotated[str, Field(min_length=1, max_length=100, description='Post title')],
    rating: Annotated[int, Field(ge=1, le=5, description='1-5 stars')],
    tags: Annotated[list[str], Field(max_length=5, description='Up to 5 tags')] = [],
) -> dict:
    """Create a blog post."""
    return {'title': title, 'rating': rating, 'tags': tags}
```

这些约束会写进 JSON Schema，模型一般不会违反；万一违反，Pydantic 拒收参数，自动走 `ModelRetry`。

### 9.1 单参数对象的扁平化

工具只有一个参数、而且是 Pydantic Model / dataclass / TypedDict 时，Pydantic AI 会把它展开成顶层 schema，在模型看来就是多个参数：

```python
class CreateOrderArgs(BaseModel):
    """Args for creating an order"""
    product_id: int = Field(description='Product ID')
    quantity: int = Field(ge=1, description='Quantity')

@agent.tool_plain
def create_order(args: CreateOrderArgs) -> dict:
    return {'order_id': 1, 'product_id': args.product_id, 'quantity': args.quantity}
```

模型看到的 schema 是 `{product_id, quantity}`，而不是 `{args: {product_id, quantity}}`。

---

## 10. 实战：按用户角色启用工具集

```python
from dataclasses import dataclass, replace
from pydantic_ai import Agent, RunContext, ToolDefinition

@dataclass
class UserCtx:
    user_id: str
    role: str   # 'admin' / 'user' / 'guest'

agent = Agent('openai:gpt-6.1-sol', deps_type=UserCtx)

async def admin_only(ctx, td):
    return td if ctx.deps.role == 'admin' else None

async def hide_for_guest(ctx, td):
    if ctx.deps.role == 'guest':
        return None
    return replace(td, description=f'[{ctx.deps.role}] {td.description}')

@agent.tool(prepare=admin_only)
def delete_user(ctx: RunContext[UserCtx], target_id: str) -> str:
    """Delete a user. Admin only."""
    return f'Deleted {target_id}'

@agent.tool(prepare=hide_for_guest)
def list_my_orders(ctx: RunContext[UserCtx]) -> list[dict]:
    """List the current user's orders."""
    return [{'order_id': 1, 'user': ctx.deps.user_id}]

# guest 只看到空工具集；user 看到 list_my_orders；admin 看到全部
agent.run_sync('show my orders', deps=UserCtx(user_id='u1', role='user'))
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 在 `prepare` 里新建 `ToolDefinition(...)` 后审批失效 / 字段丢失 | 新建的定义把 `kind`、`strict` 等字段重置成默认 | 改拿到的定义，或用 `dataclasses.replace(td, ...)` |
| `prepare` 必须 async？ | 同步也行，官方示例都是 async | 都可以，有 IO 操作时用 async |
| `strict=True` 在 Anthropic 上没生效 | 只有 OpenAI 支持 | 用 `PrepareTools` 按 system 动态开 |
| 工具返回大图片，LLM 上下文爆了 | 富内容也算 token | 业务字段放 `ToolReturn.metadata`，富内容尽量小 |
| 模型调用要审批的工具时抛 `UserError: A deferred tool call was present, but DeferredToolRequests is not among output types` | 没把 `DeferredToolRequests` 加进 `output_type` | `output_type=[str, DeferredToolRequests]` |
| `Annotated[..., Field(ge=1)]` 不生效 | 用了 `typing.Annotated`，但没 `from pydantic import Field` | 要 import Pydantic 的 Field |
| `args_validator` 抛普通 Exception，运行直接挂 | 校验失败也应该让模型改 | 在 validator 里 raise `ModelRetry(...)` |
| `tool_timeout` 设了，工具仍跑很久 | 写的是同步 `def`，框架的超时没法强制中断线程 | 关键路径写 async，配合 asyncio 超时 |
| MCP 工具设了 `tool_timeout` 没效果 | `tool_timeout` 只管 `FunctionToolset` 里的工具 | 在 MCP 客户端上配超时 |

---

## 12. 生产建议

1. 对接多个 provider 的 Agent，用 `PrepareTools` 动态开 `strict`，不要在工具上写死
2. 危险操作一律 `requires_approval=True`，配上 `args_validator`，先过滤掉不合法的请求再给人批
3. 多模态工具用 `ToolReturn` 分开返回：业务字段放 metadata，给模型看的放 content
4. `prepare` 函数不要有副作用，同一次运行里每一步都会调用它
5. 能用 `Annotated[..., Field(...)]` 就别手写 schema，类型和校验都有了

---

## 13. 本章 demo

完整可运行代码：[`demos/tools/02_advanced_tools.py`](../../demos/tools/02_advanced_tools.py)

下一篇：[Pydantic AI 02-03：Toolsets 工具集](03-toolsets.md)，讲 Toolset 的组合与复用。
