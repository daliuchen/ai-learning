# EKB 14：生成框架选型——为什么是 Pydantic AI

> **一句话**：企业知识库的生成层只做一件事——拿着检索到的片段，输出「答案 + 引用了哪几篇 + 是否找到依据」。Pydantic AI 的**结构化输出**能把这三样焊进类型系统，让引用溯源和兜底从「祈祷模型照做」变成「类型保证」。这正是这个场景最需要的能力。

---

## 1. 生成层的真实需求

我们不需要复杂编排，只需要生成层稳定输出一个固定结构：

```python
from pydantic import BaseModel, Field

class Answer(BaseModel):
    text: str = Field(description="基于提供的文档片段的回答")
    cited_doc_ids: list[int] = Field(description="回答引用的文档 id")
    found: bool = Field(description="是否在提供的片段里找到了依据")
```

要求很明确：
- 答案**必须**带引用 → `cited_doc_ids`
- 没找到依据时**必须**能识别 → `found`
- 这个结构**每次都要可靠**，不能有时返回有时不返回

---

## 2. 为什么结构化输出是关键

对比两种做法：

### ❌ 靠 prompt 祈祷

```
prompt: "回答问题，并在末尾用【引用：文档1,文档3】标注引用的文档"
```

问题：模型**经常不照做**——有时忘了标，有时格式写错，有时引用了没给的文档。你还得写正则去解析这个自由文本，脆弱不堪。

### ✅ 用结构化输出强制

```python
from pydantic_ai import Agent

agent = Agent(
    "openai:gpt-4o",
    output_type=Answer,        # 强制输出 Answer 结构
    system_prompt="只基于提供的文档片段回答。找不到依据就把 found 设为 false。",
)

result = await agent.run(f"问题：{q}\n\n文档片段：\n{chunks_text}")
answer: Answer = result.output   # 保证是合法 Answer，否则框架自动重试
```

Pydantic AI 会把 `Answer` 的 schema 喂给模型，并**校验输出**——不合规就自动让模型重试。引用和兜底从「希望模型配合」变成「框架保证」。这是 [02 手册 Pydantic AI](/docs/02-pydantic-ai/03-advanced/01-structured-output) 的核心卖点。

---

## 3. 为什么不用 LangChain

LangChain 也能做结构化输出，但它带来的**编排能力我们用不上，复杂度却要承担**：

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 结构化输出 | 一等公民、类型原生 | 支持，但 API 较绕 |
| 编排（链/图） | 轻，够用 | 强，但我们不需要 |
| 学习/调试成本 | 低，代码即逻辑 | 抽象层多，黑盒感强 |
| 和 FastAPI/Pydantic 集成 | 天生一对 | 要适配 |
| 本场景适配 | ✅ 刚刚好 | 杀鸡用牛刀 |

我们的 pipeline 是固定的「检索→生成」，不需要动态分支/多 Agent 编排。**用轻框架，把复杂留给真正需要的地方。** 详见下一篇。

---

## 4. 模型无关：Pydantic AI 的另一个好处

Pydantic AI 支持 OpenAI / Anthropic / Gemini 等，换模型只改一个字符串：

```python
agent = Agent("anthropic:claude-sonnet-4-6", output_type=Answer)  # 换成 Claude
```

这让我们能在评估阶段对比不同模型在「忠于文档、不乱编」上的表现，挑最适合的——而不被绑死在某一家。

> 模型默认建议：生成层用能力强、指令遵循好的模型（如 Claude Sonnet / GPT-4o 级别）。便宜小模型容易在「找不到就承认」上翻车（硬编答案），这恰是知识库最不能出的错。

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
    "openai:gpt-4o",
    output_type=Answer,
    system_prompt=(
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

40 行不到，引用 + 兜底 + 模型无关全有了。后续章节会在这个基础上加流式、加权限。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 靠 prompt 文本约定引用格式 | 模型不照做，解析脆弱 | 结构化输出强制 schema |
| 定型 pipeline 上 LangChain | 黑盒、调试难 | 用轻框架 Pydantic AI |
| 用便宜小模型做生成 | 找不到也硬编 | 生成层用强模型 |
| 模型绑死一家 | 想对比/迁移困难 | Pydantic AI 模型无关 |

---

## 下一步

到底什么时候才该上重框架？把这个判断讲清楚：

→ [05-need-heavy-framework](./05-need-heavy-framework.md)
