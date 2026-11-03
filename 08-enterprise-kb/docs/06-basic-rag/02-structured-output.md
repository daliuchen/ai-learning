# EKB 28：用 Pydantic AI 生成带引用的结构化答案

> 检索到的片段交给模型之后，不能任由它自由输出一段文字。这里用 Pydantic AI 的结构化输出，要求它必须返回答案、引用的文档 id、是否找到依据这三个字段，引用和兜底就由类型来保证，不用碰运气。这篇把选型那章 [为什么生成框架选 Pydantic AI](../03-selection/04-framework-pydantic-ai.md) 里的最小生成器接到真实检索上。

---

## 1. 定义输出结构

```python
# generate/answer.py
from pydantic import BaseModel, Field
from pydantic_ai import Agent

class Answer(BaseModel):
    text: str = Field(description="基于文档片段的回答，用中文")
    cited_doc_ids: list[int] = Field(
        default_factory=list,
        description="回答实际引用的文档 id，只填用到的")
    found: bool = Field(
        description="片段是否足以回答问题；不足则为 false")
```

三个字段各对应一个需求：`text` 是答案，`cited_doc_ids` 用于引用溯源，`found` 用于兜底。

---

## 2. 系统提示：要求模型忠于文档

生成质量很大程度取决于系统提示。企业知识库的几条底线（只用片段、不自行发挥、找不到就承认）要在这里写死：

```python
answer_agent = Agent(
    "anthropic:claude-sonnet-5-5",    # 或千问等任意 provider，见 03-selection/04
    output_type=Answer,
    instructions=(
        "你是企业知识库问答助手。严格遵守：\n"
        "1. 只能基于【文档片段】中的信息回答，禁止使用片段外的常识或推测。\n"
        "2. 若片段不足以回答，把 found 设为 false，text 写「未在知识库中找到相关信息」。\n"
        "3. cited_doc_ids 只填回答中实际用到的文档 id。\n"
        "4. 回答简洁、直接，不要复述问题。"
    ),
)
```

这几条的写法参考 04 手册的 [一条 Prompt 的解剖](/docs/04-prompt-engineering/01-foundations/02-anatomy)：约束要清楚、可执行、有边界。第 1、2 条是底线，不能省。

---

## 3. 把检索片段拼进 prompt

检索返回的 chunk 按固定格式拼进去，并且要带上 doc_id，模型才能引用：

```python
def format_chunks(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        blocks.append(
            f"[文档 {c['doc_id']}] {c['title']} · {c['section_path']}\n"
            f"{c['content']}"
        )
    return "\n\n---\n\n".join(blocks)

async def generate(question: str, chunks: list[dict]) -> Answer:
    if not chunks:                       # 检索为空，直接兜底，不浪费一次调用
        return Answer(text="未在知识库中找到相关信息。", cited_doc_ids=[], found=False)
    ctx = format_chunks(chunks)
    prompt = f"问题：{question}\n\n文档片段：\n{ctx}"
    result = await answer_agent.run(prompt)
    return result.output
```

`[文档 7]` 这样的标记告诉模型每段属于哪个 doc，它输出 `cited_doc_ids: [7]` 时才对得上。检索结果为空时直接返回兜底答案，省掉一次模型调用。

---

## 4. 结构化输出为什么比自由文本可靠

Pydantic AI 会把 `Answer` 的 JSON schema 做成一个「输出工具」交给模型，并校验返回结果：

```
模型返回不合规（少字段/类型错）→ 校验错误回给模型 → 自动重试 → 合规为止
重试次数有上限（默认输出重试 1 次；agent 上 retries={'output': 3} 调大），用完抛 UnexpectedModelBehavior
```

> V2 有两处小变化：取结果用 `result.output`（早期 0.x 的 `result.data` 已移除）；`result.usage` 现在是属性而不是方法，写成 `result.usage()` 会收到弃用警告。

换成自由文本加一句「请在末尾标注引用」，模型可能忘了标，可能格式不统一，也可能引用一篇不存在的文档，你还得写一段很脆弱的正则去解析。结构化输出把「必须有引用、引用必须是 int 列表」变成了框架强制执行的约束。我选 Pydantic AI，主要就是看中这一点（见 [生成框架选型](../03-selection/04-framework-pydantic-ai.md)）。

---

## 5. 校验引用是否合法

模型偶尔会引用一个根本没在片段里出现过的 doc_id，也就是幻觉引用。加一步后处理把它剔掉：

```python
def sanitize(answer: Answer, chunks: list[dict]) -> Answer:
    valid_ids = {c["doc_id"] for c in chunks}
    answer.cited_doc_ids = [i for i in answer.cited_doc_ids if i in valid_ids]
    # 如果声称 found 但没有任何合法引用，降级为未找到
    if answer.found and not answer.cited_doc_ids:
        answer.found = False
        answer.text = "未在知识库中找到相关信息。"
    return answer
```

有了这一步，「引用只能来自本次检索到的片段」就从口头约定变成了代码保证。在企业场景里，一条指向不存在文档的引用，足以让用户对整个系统失去信任。

---

## 6. 更严格的校验：output_validator

`sanitize` 是自己写的后处理，发现问题就悄悄剔掉。如果你希望引用不合法时让模型重新回答，可以把校验写进 `output_validator`，抛出 `ModelRetry` 把原因告诉模型。校验要用到本轮检索到的片段，所以通过 `deps` 传进去：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, ModelRetry, RunContext

@dataclass
class KBDeps:
    valid_doc_ids: set[int]          # 本轮检索到的文档 id

answer_agent = Agent(
    "anthropic:claude-sonnet-5-5",
    deps_type=KBDeps,
    output_type=Answer,
    instructions="...",
)

@answer_agent.output_validator
def check_citations(ctx: RunContext[KBDeps], out: Answer) -> Answer:
    bad = [i for i in out.cited_doc_ids if i not in ctx.deps.valid_doc_ids]
    if bad:
        raise ModelRetry(f"引用了不存在的文档 id {bad}，只能引用：{sorted(ctx.deps.valid_doc_ids)}")
    return out

result = await answer_agent.run(prompt, deps=KBDeps({c["doc_id"] for c in chunks}))
```

每抛一次 `ModelRetry` 就消耗一次输出重试额度，额度用完会抛 `UnexpectedModelBehavior`。所以生产环境里 `sanitize` 仍然要留着，作为最后一道兜底。流式运行（`run_stream`）时，validator 会对每一段部分输出各调用一次；如果只想校验完整结果，先判断 `ctx.partial_output`（见 [问答页](../09-frontend/01-qa-page-streaming.md)）。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 让模型输出自由文本 | 引用格式乱、难解析 | 结构化输出 |
| 片段不带 doc_id | 模型无法正确引用 | 拼入 [文档 N] 标记 |
| 检索为空也调模型 | 浪费调用，还容易被诱导编答案 | 空检索直接兜底 |
| 不校验引用合法性 | 幻觉引用指向不存在的文档 | sanitize 剔除非法引用 |
| 系统提示没写「只用片段」 | 模型掺入自己的参数化知识 | 底线约束写进 `instructions` |
| 还在写 `result.data` / `system_prompt=` | V2 里已移除 / 不再推荐 | `result.output` / `instructions=` |

---

## 下一步

有了引用，下一篇把它做成能点回原文的溯源：[引用溯源](./03-citation.md)
