# EKB 14：为什么生成框架选 Pydantic AI

> 企业知识库的生成层只做一件事：拿着检索到的片段，输出答案、引用了哪几篇、有没有找到依据。Pydantic AI 的结构化输出能把这三项写进类型，引用溯源和兜底由类型来保证，不依赖模型自觉。这个场景最需要的就是这项能力。
>
> 代码基于 `pydantic-ai 2.64`（V2）。V2 以 capability 为核心原语，但「`output_type` 结构化输出 + `instructions`」这条主线没变；早期 API 里的 `system_prompt=` 现在推荐写成 `instructions=`（区别见第 5 节）。

---

## 1. 生成层的真实需求

我们不需要复杂的编排，只需要生成层稳定地输出一个固定结构：

```python
from pydantic import BaseModel, Field

class Answer(BaseModel):
    text: str = Field(description="基于提供的文档片段的回答")
    cited_doc_ids: list[int] = Field(description="回答引用的文档 id")
    found: bool = Field(description="是否在提供的片段里找到了依据")
```

要求很明确：答案必须带引用，对应 `cited_doc_ids`；没找到依据时必须能识别出来，对应 `found`；这个结构每次都要可靠地返回，不能时有时无。

---

## 2. 为什么结构化输出最重要

对比两种做法。

### 错误做法：靠 prompt 约定格式

```
prompt: "回答问题，并在末尾用【引用：文档1,文档3】标注引用的文档"
```

模型经常不照做：有时忘了标，有时格式写错，有时引用了根本没给它的文档。你还得写正则去解析这段自由文本，非常脆弱。

### 正确做法：用结构化输出强制

```python
from pydantic_ai import Agent

agent = Agent(
    "anthropic:claude-sonnet-5-5",   # 任意 provider:model 字符串
    output_type=Answer,              # 强制输出 Answer 结构
    instructions="只基于提供的文档片段回答。找不到依据就把 found 设为 false。",
)

result = await agent.run(f"问题：{q}\n\n文档片段：\n{chunks_text}")
answer: Answer = result.output   # 保证是合法 Answer，否则框架自动让模型重试
```

Pydantic AI 默认把 `Answer` 变成一个输出工具交给模型调用（Tool Output 模式），再用 Pydantic 校验参数。不合规就把校验错误回传给模型，让它重来（默认输出重试 1 次，可用 `retries={'output': N}` 调整，次数用完抛 `UnexpectedModelBehavior`）。这样引用和兜底就由框架保证，不用指望模型配合。结构化输出的完整用法见 02 手册的 [结构化输出（Output Types）](/docs/02-pydantic-ai/01-basics/06-output-types)。

---

## 3. 为什么不用 LangChain

LangChain 也能做结构化输出，但它的编排能力我们用不上，带来的复杂度却要照单全收：

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 结构化输出 | 一等公民，原生基于类型 | 支持，但 API 较绕 |
| 编排（链/图） | 轻，够用 | 强，但我们不需要 |
| 学习和调试成本 | 低，代码就是逻辑 | 抽象层多，黑盒感强 |
| 和 FastAPI/Pydantic 集成 | 同一套体系 | 要做适配 |
| 本场景适配 | 刚好合适 | 杀鸡用牛刀 |

我们的 pipeline 是固定的检索加生成，不需要动态分支，也不需要多 Agent 编排。用轻框架就够了，复杂度留给真正需要的地方。详见[什么时候需要重框架](05-need-heavy-framework.md)。

---

## 4. 模型无关

Pydantic AI 的另一个好处是不绑定模型。它支持几十家 provider，换模型只改一个字符串：

```python
Agent("anthropic:claude-sonnet-5-5", output_type=Answer)   # Claude
Agent("openai:gpt-6.1-sol", output_type=Answer)            # OpenAI
Agent("alibaba:qwen-flash", output_type=Answer)            # 千问（注意地域，见下）
```

接千问有个坑：`alibaba:` 前缀默认连的是国际站（`dashscope-intl`），国内账号的 key 要显式指定国内地址。本手册 demo 就是这么写的，同样的写法也适用于任何 OpenAI 兼容端点：

```python
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    "qwen-flash",
    provider=OpenAIProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
)
agent = Agent(model, output_type=Answer)
```

这样在评估阶段就能比较不同模型在忠于文档、不乱编这件事上的表现，选最合适的那个，不会被某一家绑死。

> 关于选模型，这里只给定性建议，不报榜单。生成层先用你手头有 key 的主力档模型起步，再拿第 04 章的评估集去比便宜档。便宜模型最容易出的问题是找不到依据也硬编，而这恰恰是知识库最不能出的错，所以要在答不出类的用例上专门测，不达标再换强模型。价格以各厂商官方定价页为准，本手册不抄价格表，因为很快会过期。

---

## 5. 一个完整的最小生成器

```python
# generate/answer.py
from pydantic import BaseModel, Field
from pydantic_ai import Agent

class Answer(BaseModel):
    text: str
    cited_doc_ids: list[int]
    found: bool

answer_agent = Agent(
    "anthropic:claude-sonnet-5-5",
    output_type=Answer,
    instructions=(
        "你是企业知识库助手。只能基于【文档片段】回答，不得使用片段外的知识。"
        "若片段不足以回答，把 found 设为 false，text 写「未找到相关信息」。"
        "cited_doc_ids 只填实际用到的文档 id。"
    ),
)

async def generate(question: str, chunks: list[dict]) -> Answer:
    ctx = "\n\n".join(f"[文档{c['doc_id']}] {c['content']}" for c in chunks)
    r = await answer_agent.run(f"问题：{question}\n\n文档片段：\n{ctx}")
    return r.output
```

二十几行代码，引用、兜底、模型无关都有了。后面的章节会在这个基础上加流式输出和权限。

用 `instructions` 还是 `system_prompt`？两个参数都还在。区别是 `system_prompt` 会随 `message_history` 一起保留，`instructions` 每次运行都重新生成，不存进历史。知识库每次问答都是一次独立的 `run`，历史里不带系统提示，改了提示词立刻对所有会话生效，官方也推荐这种情况用 `instructions`。

测试时不必调真模型。Pydantic AI 自带 `TestModel` 和 `FunctionModel`，单测里像下面这样换掉模型，一分钱不花：

```python
from pydantic_ai.models.test import TestModel

with answer_agent.override(model=TestModel(custom_output_args={
        "text": "年假 5 天", "cited_doc_ids": [1], "found": True})):
    r = await answer_agent.run("年假几天")
    assert r.output.found
```

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 靠 prompt 文本约定引用格式 | 模型不照做，解析脆弱 | 用结构化输出强制 schema |
| 固定 pipeline 上 LangChain | 黑盒，难调试 | 用轻框架 Pydantic AI |
| 用便宜模型做生成却没专门测 | 找不到依据也硬编 | 在答不出类用例上专门测，不达标再换强模型 |
| 模型绑死一家 | 想对比或迁移都困难 | 利用 Pydantic AI 的模型无关 |

---

## 下一步

什么时候才真的该上重框架？下一篇把这个判断讲清楚：

→ [什么时候才需要重框架](./05-need-heavy-framework.md)
