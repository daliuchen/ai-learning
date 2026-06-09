# EKB 28：结构化生成——用 Pydantic AI 输出答案 + 引用

> **一句话**：把检索到的片段喂给模型，但**不要**让它自由发挥输出一段文本。用 Pydantic AI 的结构化输出，强制它返回 `{答案, 引用的文档id, 是否找到依据}`。这样引用和兜底是类型保证，不靠运气。本篇把第 03 章的生成器接上真实检索。

---

## 1. 输出结构定义

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

三个字段对应三个需求：`text`（答案）、`cited_doc_ids`（引用溯源）、`found`（兜底）。

---

## 2. 系统提示：约束模型「忠于文档」

系统提示是生成质量的关键。企业知识库的核心约束——**只用片段、不发挥、找不到就承认**——要写死在这里：

```python
answer_agent = Agent(
    "openai:gpt-4o",
    output_type=Answer,
    system_prompt=(
        "你是企业知识库问答助手。严格遵守：\n"
        "1. 只能基于【文档片段】中的信息回答，禁止使用片段外的常识或推测。\n"
        "2. 若片段不足以回答，把 found 设为 false，text 写「未在知识库中找到相关信息」。\n"
        "3. cited_doc_ids 只填回答中实际用到的文档 id。\n"
        "4. 回答简洁、直接，不要复述问题。"
    ),
)
```

这几条对应 [04 手册](/docs/04-prompt-engineering/03-techniques/01-instructions) 的指令工程——清晰、可执行、有边界。第 1、2 条是底线，不能省。

---

## 3. 把检索片段拼进 prompt

检索返回的 chunk 要按固定格式拼进去，**带上 doc_id**（模型才能引用）：

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

`[文档 7]` 这种标记让模型知道每段属于哪个 doc，输出 `cited_doc_ids: [7]` 时就能对上。检索为空时**直接返回兜底**，省一次模型调用。

---

## 4. 为什么结构化输出比自由文本可靠

Pydantic AI 把 `Answer` 的 JSON schema 发给模型，并**校验返回**：

```
模型返回不合规（少字段/类型错）→ Pydantic AI 自动重试 → 直到合规
```

对比自由文本「请在末尾标注引用」——模型可能忘、可能格式乱、可能引用不存在的文档，你还得写脆弱的正则解析。结构化输出把「引用必须有、必须是 int 列表」变成**框架强制的约束**。这是选 Pydantic AI 的核心理由（见 [03-selection/04](../03-selection/04-framework-pydantic-ai.md)）。

---

## 5. 校验引用的合法性

模型偶尔会引用一个**没出现在片段里**的 doc_id（幻觉引用）。加一道后处理把它剔掉：

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

这一步把「引用只能来自实际检索到的片段」从约定变成保证——企业场景里，一个指向不存在文档的引用会直接摧毁信任。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 让模型输出自由文本 | 引用格式乱、难解析 | 结构化输出 |
| 片段不带 doc_id | 模型无法正确引用 | 拼入 [文档 N] 标记 |
| 检索为空也调模型 | 浪费调用，易被诱导编 | 空检索直接兜底 |
| 不校验引用合法性 | 幻觉引用指向不存在文档 | sanitize 剔除非法引用 |
| 系统提示没写「只用片段」 | 模型掺入参数化知识 | 底线约束写进 system prompt |

---

## 下一步

引用有了，怎么把它做成「能点回原文」的可信溯源：

→ [03-citation](./03-citation.md)
