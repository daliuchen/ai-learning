# PE Advanced 03：RAG Prompting —— 检索 + 引用约束

> **一句话**：RAG 不是把检索结果塞到 prompt 就完事——好 RAG prompt 要让模型**只用检索结果回答**、**明确引用源**、**没找到就 refuse**、**避免幻觉补全**。本篇专讲 prompt 这一层。

---

## 1. RAG 流程里的 prompt 在哪

```
[用户问题]
   ↓
[Embedding + Vector Search] → 拿到 top-k 段落
   ↓
[构造 prompt：问题 + 检索结果 + 系统指令] ← 本篇关注
   ↓
[LLM 生成答案]
```

PE 关注的是第三步——**怎么把检索结果喂给 LLM**。

---

## 2. 标准 RAG prompt 模板

```
你是企业知识助手。请**仅基于** <context> 中的信息回答用户问题。

<context>
[Source 1] {snippet_1}
[Source 2] {snippet_2}
[Source 3] {snippet_3}
</context>

<question>
{user_question}
</question>

规则：
1. 只用 <context> 里的信息。**不要**使用任何外部知识。
2. 答案末尾用 [Source N] 标注引用源。
3. 如果 <context> 不足以回答，回答："根据当前可用信息无法回答"。
4. 不要补全、不要猜测、不要扩展上下文里没有的内容。
```

四条规则缺一不可：

| 规则 | 防止 |
|------|------|
| 1. 仅用 context | 幻觉 |
| 2. 引用 | 可追溯 |
| 3. 没找到就拒绝 | 编造 |
| 4. 不扩展 | 过度推断 |

---

## 3. 改进版：要求逐句引用

```
回答时按以下格式：

每一句陈述后跟一个 [Source N] 引用。

示例：
"用户登录失败 [Source 1]。系统会自动锁定账号 30 分钟 [Source 2]。"

如果某条信息找不到引用源，删掉那条。
```

强制每一句都有引用 → 大幅降低幻觉。

---

## 4. 多文档时的 context 组织

```
<context>
<doc id="1" source="user-manual-v2.3.pdf" updated="2026-03-12">
{snippet}
</doc>

<doc id="2" source="faq-internal.md" updated="2025-11-08">
{snippet}
</doc>

<doc id="3" source="ticket-12345" updated="2026-05-01">
{snippet}
</doc>
</context>
```

让模型看到 metadata（source / 更新日期）→ 引用时能精确指。

---

## 5. 模型选择 source 的策略

多个 source 有冲突时，prompt 应该指导模型怎么选：

```
冲突解决规则：
- 如果两个 source 矛盾，选 updated 日期最新的
- 内部文档 > 外部文档
- 官方 FAQ > 社区帖子
- 引用时标注"基于最新文档"
```

或更安全的：

```
如果 source 冲突，列出两种说法 + 各自 source，让用户自己判断。
```

---

## 6. Context 大小管理

```
context 太短 → 信息不足
context 太长 → 模型 "lost in the middle"
```

实战建议：

| 维度 | 推荐 |
|------|------|
| chunk 大小 | 200-500 字 / 段 |
| top-k | 3-10 段 |
| 总 context 长度 | < 10k token（即使模型支持 100k+） |
| 重要 source 顺序 | 最相关放最前或最后（首尾 attention 红利） |

---

## 7. Re-rank 与多轮 RAG

### 7.1 Re-rank
拿到 vector search 的 top-20 → 用 cross-encoder 重排 → 取前 5 喂给 prompt。提升相关性。

### 7.2 Multi-hop RAG
单次检索不够时，让模型迭代查询：

```
[Q] 公司 Q3 营收同比？
   ↓
[Retrieve 1] 找到 Q3 营收数字
   ↓
[Q2 from model] 还需要 Q2 同期数字
   ↓
[Retrieve 2] 找到 Q2 数字
   ↓
[Answer] 同比 X%
```

这就是用 ReAct loop + 检索工具，参考 [01-react.md](./01-react.md)。

### 7.3 HyDE（Hypothetical Document Embedding）
先让 LLM 生成一个"假设的答案" → 用它做检索（而不是直接用 question）→ 相关性更好。

---

## 8. RAG evalset 特殊要求

RAG 的 evalset 不只看"答案对不对"，还看：

| 指标 | 含义 |
|------|------|
| **Faithfulness** | 答案是否忠实于 context（没幻觉）|
| **Context recall** | retrieved context 是否覆盖了 ground truth |
| **Context precision** | retrieved 里相关的比例 |
| **Answer relevance** | 答案是否真正回答了 question |
| **Citation accuracy** | 引用是否准确 |

工具：**Ragas** 是 RAG 评测专门库。

---

## 9. 防"信息不在 context" 时的编造

最常见的 RAG 失败：context 没相关信息，模型用训练数据"补全"了答案。

对策：

### 9.1 严格的 refusal prompt

```
重要：如果 <context> 不包含回答问题所需信息，必须返回：
{
  "status": "insufficient_context",
  "reason": "<具体说明哪里信息缺失>"
}

不要：
- 用训练数据补全
- 推测 / 猜测
- 给"通用"答案
```

### 9.2 Faithfulness check
答案出来后再调一次 LLM 检查：

```
检查下面答案是否完全基于 context（不含外部知识）：

<context>...</context>
<answer>...</answer>

返回 {"is_faithful": bool, "unsupported_claims": ["...", ...]}
```

---

## 10. 多语言 RAG

问 / 答 / context 可能不同语言：

```
重要：
- <context> 可能是英文，<question> 是中文
- 回答用 <question> 的语言
- 引用时保留原文 + 翻译
```

---

## 11. demo：基础 RAG prompt

```python
# demos/advanced/03_rag_prompt.py
import anthropic
client = anthropic.Anthropic()


RAG_SYSTEM = """你是企业知识助手。仅基于 <context> 回答用户问题。

规则：
1. 只用 <context> 信息，不用外部知识
2. 每条陈述末尾加 [Source N] 引用
3. 信息不足时返回 "根据当前文档无法回答"
4. 不补全、不推测
"""


def build_prompt(question: str, sources: list[dict]) -> str:
    ctx_blocks = "\n\n".join(
        f"[Source {i+1}] (from {s['source']})\n{s['text']}"
        for i, s in enumerate(sources)
    )
    return f"""<context>
{ctx_blocks}
</context>

<question>
{question}
</question>"""


SOURCES = [
    {"source": "manual-v2.3", "text": "用户首次登录需要绑定手机号。绑定后无法更改。"},
    {"source": "faq", "text": "修改手机号需要联系客服并提供身份证明。"},
]

QUESTION = "我能改绑手机号吗？怎么改？"

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=500,
    temperature=0,
    system=RAG_SYSTEM,
    messages=[{"role": "user", "content": build_prompt(QUESTION, SOURCES)}],
)
print(resp.content[0].text)
```

预期输出（含 [Source 1] / [Source 2] 引用）。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **不写 "仅基于 context"** | 模型用训练数据补 |
| **不要求引用** | 不可追溯，幻觉混入 |
| **context 太长** | 中部信息被忘 |
| **不防"信息不足"** | 模型硬编 |
| **没 faithfulness check** | 上线后用户投诉幻觉 |
| **chunk 切得太碎** | 失去语义边界 |
| **没考虑 source 冲突** | 模型挑了过时信息 |

---

## 13. 跨手册关联

- LangChain RAG 实战：[../../../01-langchain/docs/01-langchain/13-rag.md](../../../01-langchain/docs/01-langchain/13-rag.md)
- Pydantic AI RAG：[../../../02-pydantic-ai/docs/06-practice/02-project-rag.md](../../../02-pydantic-ai/docs/06-practice/02-project-rag.md)
- MCP 内部知识库：[../../../03-mcp/docs/07-practice/01-project-internal-kb.md](../../../03-mcp/docs/07-practice/01-project-internal-kb.md)

---

## 14. 下一步

- 📖 多模态 prompt → [04-multimodal.md](./04-multimodal.md)
- 📖 meta-prompting → [05-meta-prompting.md](./05-meta-prompting.md)
- 📖 注入防御 → [06-injection-defense.md](./06-injection-defense.md)

## 参考资料

- Ragas: https://docs.ragas.io
- LangChain RAG: https://python.langchain.com/docs/tutorials/rag/
- "HyDE" (Gao et al. 2022): https://arxiv.org/abs/2212.10496
