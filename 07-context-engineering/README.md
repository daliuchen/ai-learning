# Context Engineering 上下文工程

> 这本手册讨论的对象是一次调用里放进模型窗口的一切：system 指令、对话历史、检索资料、工具定义和结果、示例，以及它们的顺序和预算。

Prompt Engineering 讲怎么写指令，Embedding 讲怎么检索。但一个 LLM 应用能做到多好，往往取决于模型在某一次推理时窗口里装了什么、按什么顺序排、各部分占了多少预算。这件事就是 Context Engineering。

手册把上下文拆成几个可以分开优化的环节：组成 → 检索注入 → 记忆 → 压缩 → Agent 上下文 → 长上下文 → 生产化。每个环节讲取舍，也给出业界常用的默认值。

---

## 章节

| 章节 | 主题 | 篇数 |
|------|------|------|
| 01-foundations | 基础：什么是上下文工程 / 窗口本质 / Context Rot / 成本预算 | 6 |
| 02-anatomy | 上下文的组成：指令 / 历史 / 检索 / 工具 / few-shot / 组织结构 | 6 |
| 03-retrieval | 检索与注入：静态 vs 动态 / JIT / 排序裁剪 / 归因 | 5 |
| 04-memory | 记忆系统：短期 vs 长期 / 存储召回 / Agent 记忆架构 / 遗忘 | 6 |
| 05-compaction | 压缩与裁剪：摘要 / 滑窗 / 剪枝 / 触发时机 | 5 |
| 06-agent-context | Agent 上下文：累积 / 工具结果 / 多 Agent 传递 / 隔离 / 状态 | 6 |
| 07-long-context | 长上下文：1M token / vs RAG / 缓存复用 / 注意力 | 4 |
| 08-production | 生产化：可观测 / 成本 / 排障 / 评测 / 安全 | 5 |
| 09-practice | 实战：带记忆客服 / 长文档问答 / 多 Agent 编排 | 3 |

共 46 篇。

## 学习路径

不必从头读到尾，按手头的问题挑一条：

- 刚接触这个话题：[什么是上下文工程](docs/01-foundations/01-what-is-context-engineering.md) → [上下文窗口到底是什么](docs/01-foundations/02-context-window.md) → [Context Rot 与 Lost in the Middle](docs/01-foundations/03-context-rot.md) → [按预算分配上下文](docs/01-foundations/05-context-budget.md)。
- 在做 RAG 问答，答案不准或成本高：[RAG 作为上下文来源](docs/03-retrieval/01-rag-as-context.md) → [检索结果的排序、裁剪、去重](docs/03-retrieval/04-rank-trim.md) → [有了长上下文还要不要 RAG](docs/07-long-context/02-long-context-vs-rag.md) → [实战：长文档问答](docs/09-practice/02-long-doc-qa.md)。
- 在做长对话或带记忆的助手：[对话历史](docs/02-anatomy/02-history.md) → [短期记忆 vs 长期记忆](docs/04-memory/01-short-vs-long.md) → [为什么要压缩上下文](docs/05-compaction/01-why-compact.md) → [实战：带长期记忆的客服 Agent](docs/09-practice/01-memory-customer-agent.md)。
- 在做 Agent，跑长了就变笨或变贵：[Agent 的上下文累积问题](docs/06-agent-context/01-accumulation.md) → [工具结果的上下文管理](docs/06-agent-context/02-tool-results.md) → [上下文隔离](docs/06-agent-context/04-isolation.md) → [Prompt Caching 与上下文复用](docs/07-long-context/03-prompt-caching.md)。

---

## 写法约定

和这个仓库里其他手册一样，每篇大致按这个顺序：开头导语 → 概念 → 最小代码 → 进阶 → 生产建议 → 常见坑（表格）→ 下一步。代码可以单独运行，错误写法和正确写法用 `# ❌ / # ✅` 标出。

## 前置依赖

```bash
pip install -r requirements.txt
```

需要 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`（看具体示例）。用 OpenAI 兼容写法的示例可以指向任何兼容端点，比如千问：设 `OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，模型名换成 `qwen-flash`。

## 版本说明

以 2026 年 11 月的版本为准。

- 模型窗口、价格、缓存和 compaction 规则的出处：Anthropic Models overview / Pricing / Prompt caching / Compaction / Context editing / Memory tool，OpenAI Models / Conversation state / Prompt caching / Compaction，Gemini API Models / Pricing / Long context，阿里云百炼上下文缓存。这些数字和 beta header 变得很快，上线前请再对一遍官方页面。
- 当前模型：Claude 5 家族（Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5，1M 窗口）、OpenAI GPT-6 系（1.05M）、Gemini 4 Argon（1M）、千问 qwen3.8 / qwen3.7 系。
- 框架：LangChain 1.4（`SummarizationMiddleware` / `ContextEditingMiddleware`）、LangGraph 1.2。
- Anthropic 专属 API（compaction、context editing、memory tool 等）的示例按官方文档整理，运行需要 `ANTHROPIC_API_KEY`，部分还要带对应的 beta header。
- Claude 5.x 默认开自适应思考，思考 token 算在 `max_tokens` 里。示例里的 `max_tokens` 都留了余量，自己用时别设太小。
