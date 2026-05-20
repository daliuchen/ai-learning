# Pydantic AI 02-02：Advanced Tool Features 高级工具特性

> **一句话**：Pydantic AI 工具不止"装饰一个函数"——你还能动态启用/禁用、塞富内容回模型、做人在回路审批、把内部 metadata 不暴露给 LLM。

---

## 1. 进阶能力总览

| 能力 | 关键词 | 一句话 |
|------|--------|--------|
| 动态启用 | `prepare` | 每一步调用前改 / 隐藏 tool def |
| Agent 级 prepare | `PrepareTools` capability | 一次改一批工具 |
| 多模态返回 | `ImageUrl` / `BinaryContent` / `DocumentUrl` | 工具吐图、PDF 给模型看 |
| 返回值分层 | `ToolReturn` | return_value / content / metadata 三层 |
| 严格 schema | OpenAI `strict=True` | 字段不漏、不乱编 |
| 重试控制 | `retries` / `max_retries` | 多层覆盖 |
| 人在回路 | `requires_approval=True` | DeferredToolRequests |
| 参数预校验 | `args_validator=` | 跑工具前先验 |
| 超时控制 | `tool_timeout=` | 工具卡住自动重试 |
| 参数约束 | `Annotated[..., Field(...)]` | JSON Schema 自动带约束 |

下面挨个展开。

---

## 2. 动态启用：`prepare` 钩子

`prepare` 是一个**每一步执行前**被调用的函数，签名固定：

```python
async def prepare(
    ctx: RunContext[DepsType],
    tool_def: ToolDefinition,
) -> ToolDefinition | None:
    ...
```

返回 `None` → 这一步**隐藏**该工具；返回 `ToolDefinition` → 用这个（可能改过的）定义。

### 2.1 按 deps 启用工具

```python
from pydantic_ai import Agent, RunContext, ToolDefinition

agent = Agent('openai:gpt-4o-mini', deps_type=int)

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

`prepare` 可以**改 description / 改参数 schema**，让同一函数对不同角色表现不同：

```python
from dataclasses import replace

async def role_aware(ctx: RunContext[str], td: ToolDefinition) -> ToolDefinition | None:
    if ctx.deps == 'admin':
        return replace(td, description='[ADMIN] Delete a user by id. Use with extreme caution.')
    if ctx.deps == 'user':
        return replace(td, description='List your own user info only.')
    return None   # guest 看不到这个工具
```

⚠️ 注意：**必须用 `dataclasses.replace`** 生成新对象，不要原地改 `tool_def.description`（部分版本是 frozen dataclass，直接改会报错）。

### 2.3 Agent 级 prepare：`PrepareTools` capability

如果是"对所有工具批量做一件事"（比如 OpenAI 上把所有工具开 `strict=True`），用 `PrepareTools`：

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

agent = Agent('openai:gpt-4o-mini', capabilities=[PrepareTools(strict_on_openai)])
```

**执行顺序**：每个工具自己的 `prepare` 先跑 → 留下的工具集合再过 Agent 级 `PrepareTools`。

---

## 3. 多模态返回：让工具吐图给模型

工具不仅能返回字符串，还能直接返回图片/PDF/二进制内容：

```python
from pydantic_ai import Agent, ImageUrl, BinaryContent, DocumentUrl

agent = Agent('openai:gpt-4o-mini')

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

支持视觉的模型（Gemini / GPT-4o / Claude Sonnet 4）会直接"看到"图片继续推理；不支持的模型会被序列化为引用。

---

## 4. `ToolReturn`：返回值 / 富内容 / metadata 三层分离

普通工具返回单一值，但有时你想：

1. 返回给模型一个**简短结果**（"Clicked at (100, 200)"）
2. 同时附**富内容**（操作前后截图）让模型"看到"
3. 再带一份**只给应用代码用、模型看不到**的 metadata（坐标、耗时）

这就是 `ToolReturn` 的设计：

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

三层各自的去处：

| 字段 | 进 LLM？ | 形式 |
|------|----------|------|
| `return_value` | ✅ | 标准 ToolReturnPart |
| `content` | ✅ | 额外插一条 user message |
| `metadata` | ❌ | 只在 `all_messages()` 里给业务代码用 |

典型应用：**浏览器操作 Agent**（每一步给模型看截图，但把 DOM 树留给业务监控用）。

---

## 5. 严格 schema：`strict=True`

OpenAI 的 [Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs) 模式下，工具可以开 `strict=True` 让模型**保证字段齐全、不乱编新字段**：

```python
@agent.tool_plain(strict=True)
def create_user(name: str, email: str, age: int) -> dict:
    """Create a user."""
    return {'id': 1, 'name': name, 'email': email, 'age': age}
```

**注意事项**：

- `strict=True` 会要求 schema 必须满足 OpenAI 的子集（所有字段都 required、不能有默认值的复杂类型等）
- Anthropic / Gemini 没有等价机制，开了会被忽略（不报错）
- 跨 provider 项目里用 `PrepareTools` 按 model 系统动态开关更优雅（见 §2.3）

---

## 6. 工具重试控制：四层优先级

```python
# 1) Agent 级
agent = Agent('openai:gpt-4o-mini', retries={'tools': 2})

# 2) Toolset 级
toolset = FunctionToolset(max_retries=5)

# 3) 工具级
@agent.tool(retries=3)
def my_tool(...): ...

# 4) Tool() 构造
Tool(my_func, max_retries=4)
```

**优先级**：工具级 > toolset 级 > agent 级。

超过次数会抛 `UnexpectedModelBehavior('Tool ... exceeded max retries count of N')`，建议在生产里 `try ... except UnexpectedModelBehavior` 兜底。

---

## 7. 工具超时

```python
agent = Agent('openai:gpt-4o-mini', tool_timeout=30)  # agent 默认 30 秒

@agent.tool_plain(timeout=5)        # 单工具覆盖为 5 秒
async def fast_tool() -> str:
    await asyncio.sleep(1)
    return 'done'
```

超时会触发 `ModelRetry`-语义的重试提示 `"Timed out after 5 seconds."`，并计入重试计数。

---

## 8. 人在回路：`requires_approval=True`

危险操作（删数据、转账、发邮件）应该**等人点同意再执行**：

```python
from pydantic_ai import Agent, DeferredToolRequests, RunContext

agent = Agent(
    'openai:gpt-4o-mini',
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

详细用法详见后续 *Deferred Tools* 章节，这里先有个印象。

### 8.1 参数预校验：`args_validator=`

`requires_approval` 经常配 `args_validator` 用——**先校验参数合法、再让人批**，避免给人看到一堆明显非法的请求：

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

Pydantic AI 工具支持把约束直接写进类型注解：

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

这些约束会**自动写进 JSON Schema**，模型大概率不会违反；万一违反，Pydantic 会拒接参数 → 自动触发 `ModelRetry`。

### 9.1 单参数对象的"扁平化"

当工具只有**一个参数**且它是 Pydantic Model / dataclass / TypedDict 时，Pydantic AI 会把它**直接展开成顶层 schema**，模型看着像多参数：

```python
class CreateOrderArgs(BaseModel):
    """Args for creating an order"""
    product_id: int = Field(description='Product ID')
    quantity: int = Field(ge=1, description='Quantity')

@agent.tool_plain
def create_order(args: CreateOrderArgs) -> dict:
    return {'order_id': 1, 'product_id': args.product_id, 'quantity': args.quantity}
```

模型看到的 schema 就是 `{product_id, quantity}`，不是 `{args: {product_id, quantity}}`。

---

## 10. 实战：按用户角色启用工具集

```python
from dataclasses import dataclass, replace
from pydantic_ai import Agent, RunContext, ToolDefinition

@dataclass
class UserCtx:
    user_id: str
    role: str   # 'admin' / 'user' / 'guest'

agent = Agent('openai:gpt-4o-mini', deps_type=UserCtx)

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
| `prepare` 改 `td.description` 直接报 frozen | 没用 `dataclasses.replace` | `return replace(td, description=...)` |
| `prepare` 必须 async？ | 同步也行，但官方示例都是 async | 任意，IO 操作建议 async |
| `strict=True` 在 Anthropic 上没生效 | 只 OpenAI 支持 | 用 `PrepareTools` 按 system 动态开 |
| 工具返回大图片导致 LLM 上下文爆 | 富内容也算 token | 用 `ToolReturn.metadata` 放业务字段、富内容尽量小 |
| `requires_approval` 后程序直接结束 | 没有把 `DeferredToolRequests` 加进 `output_type` | `output_type=[str, DeferredToolRequests]` |
| `Annotated[..., Field(ge=1)]` 不生效 | 用了 `typing.Annotated` 但缺 `from pydantic import Field` | 一定要 import Pydantic 的 Field |
| `args_validator` 抛普通 Exception 直接挂 | 校验也要"让模型改" | 在 validator 里 raise `ModelRetry(...)` |
| `tool_timeout` 设了但工具仍跑很久 | 写的是 `def`（同步），框架 timeout 对线程不能强制中断 | 业务关键路径写 async + asyncio 超时 |

---

## 12. 生产建议

1. **写多 provider 的 Agent 用 `PrepareTools` 动态开 `strict`**，别在工具上硬编码
2. **危险操作一律 `requires_approval=True`** + `args_validator`，把不合法的过滤掉再让人批
3. **多模态工具用 `ToolReturn` 分层**，业务字段塞 metadata、模型看 content
4. **`prepare` 函数保持纯函数**，别有副作用，否则同一 run 内多次 step 会执行多次
5. **`Annotated[..., Field(...)]` 替代手写 schema**，类型安全 + 自动校验

---

## 13. 本章 demo

完整可运行代码：[`demos/tools/02_advanced_tools.py`](../../demos/tools/02_advanced_tools.py)

跑通后下一篇：[03-toolsets.md](03-toolsets.md) — Toolset 与组合复用。
