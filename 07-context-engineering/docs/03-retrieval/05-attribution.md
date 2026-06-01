# 引用与归因（Citation & Grounding）

> **一句话**：检索回来的内容拼进窗口还不够——要**逼模型把答案"锚"在这些内容上、并标出处编号**。给每个 chunk 编号、要求逐句引用、明确"没有就说没有"，是把 RAG 从"看起来有依据"变成"真有依据、可核查"的关键一步。

---

## 1. 为什么要引用与归因

模型即使拿到了正确的检索内容，也可能：

- **脱离资料胡编（hallucination）**：忽略 context，凭权重里的旧知识答，且语气一样自信。
- **混入未提及的细节**：在真内容里掺私货，用户分不清哪句有据、哪句是编的。
- **无法核查**：用户/下游系统看不到答案出自哪一段，没法验证、没法追责。

Grounding（接地）+ Citation（引用）就是对治这三点：**让模型只基于 context 回答，并明确每个结论来自哪一条**。这在合规、医疗、法律、客服等场景几乎是硬要求。

---

## 2. 基础手法：chunk 编号 + 要求引用

最朴素也最通用的做法——给每个 chunk 一个稳定编号，在 prompt 里要求模型引用编号：

```python
from anthropic import Anthropic

client = Anthropic()

def build_grounded_prompt(query: str, chunks: list[str]):
    context = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(chunks))
    system = (
        "你是知识库问答助手，必须严格遵守：\n"
        "1. 只依据 <context> 中的内容回答，禁止使用其中没有的信息。\n"
        "2. 每个事实性结论后用 [编号] 标注来源，可多个，如 [1][3]。\n"
        "3. 若 context 中找不到答案，直接回答『资料中未提及』，不要编造，不要猜测。\n"
        "4. 不要复述整段原文，用自己的话概括并附编号。"
    )
    user = f"<context>\n{context}\n</context>\n\n问题：{query}"
    return system, user

system, user = build_grounded_prompt(query, chunks)
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": user}],
)
print(resp.content[0].text)
# → "支持 7 天无理由退款，定制商品除外 [1][2]。配送默认 3-5 个工作日 [3]。"
```

四条规则缺一不可，尤其**第 3 条「找不到就说没有」**是抑制幻觉的核心。光说"基于 context 回答"不够强，要显式给出"无答案"的出口，否则模型倾向于硬编一个。

---

## 3. 结构化引用：让 citation 可程序化校验

让模型把答案和引用输出成 JSON，下游就能自动校验、渲染脚注、甚至拦截无引用的句子：

```python
import json

CITE_SYSTEM = (
    "依据 <context> 回答，输出严格 JSON：\n"
    '{"answer": "...", "claims": [{"text": "单条结论", "sources": [1,3]}]}\n'
    "每条 claim 的 sources 必须是 context 中真实存在的编号；"
    "无法支撑的结论不要输出。"
)

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=CITE_SYSTEM,
    messages=[{"role": "user",
               "content": f"<context>\n{context}\n</context>\n\n问题：{query}"}],
)
data = json.loads(resp.content[0].text)

# 校验：引用的编号必须真实存在，且每条结论都得有来源
valid_ids = set(range(1, len(chunks) + 1))
for claim in data["claims"]:
    assert claim["sources"], f"无来源的结论被拦截：{claim['text']}"
    assert set(claim["sources"]) <= valid_ids, "引用了不存在的编号（疑似幻觉）"
```

这样你能在返回用户前**程序化拦截**：编号编造的、没来源的结论一律打回。比纯文本 `[1]` 标注更可控。

---

## 4. Claude 原生 Citations 特性

除了自己写 prompt，Anthropic API 提供了**原生 Citations**：把文档作为结构化 `document` 内容块传入并开启 citations，模型返回的文本会自动附带**精确到字符区间**的引用，指回原文具体位置，不靠模型"自觉"标编号：

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain",
                           "data": "退款政策：7 天无理由退款，定制商品除外……"},
                "title": "退款政策",
                "citations": {"enabled": True},   # 开启原生引用
            },
            {"type": "text", "text": "定制商品能退吗？"},
        ],
    }],
)
# 返回的 content 块中带 citations 字段：标明引用了哪个文档的哪段字符区间
for block in resp.content:
    if block.type == "text":
        print(block.text, getattr(block, "citations", None))
```

优势：引用区间由系统标定而非模型编造，**更可信、不会引用不存在的位置**，且省去自己设计编号格式的麻烦。生产中合规要求高时优先用原生 Citations；要兼容多家模型时退回第 2/3 节的手写方案。

---

## 5. 防胡编：grounding 的几个加固手段

| 手段 | 作用 | 备注 |
|------|------|------|
| 显式"无答案出口" | 给模型说"没有"的合法选项 | 最有效，必做 |
| 要求逐结论附编号 | 强制每句话有据可查 | 配结构化输出可自动校验 |
| 程序化校验引用 | 拦截编造的编号 / 无来源结论 | 见第 3 节 |
| 原生 Citations | 系统级精确引用 | Claude 等支持，最可信 |
| 调低 temperature | 减少发散性编造 | grounding 任务建议 0~0.3 |

```
# ❌ 弱 grounding：模型可能自信地编
system = "根据下面的资料回答问题。"

# ✅ 强 grounding：只用 context、逐句引用、没有就明说、可校验
system = "只依据 <context>；每个结论标 [编号]；找不到就答『资料中未提及』。"
```

最后提醒：引用归因不是锦上添花，而是**让 RAG 可信、可核查、可上线**的底线工程。一个能标出处、肯说"我不知道"的系统，远比一个总能流畅作答但分不清真假的系统有价值。

---

## 下一步

- 回顾：[./04-rank-trim.md](./04-rank-trim.md) 进窗口前的 chunk 编号正好对接这里的引用
- 跨章：[../08-production/04-evaluation.md](../08-production/04-evaluation.md) 如何评测 RAG 的 grounding / 引用准确率
- 跨章：[../04-memory/01-short-vs-long.md](../04-memory/01-short-vs-long.md) 检索之外，记忆也是上下文的动态来源
