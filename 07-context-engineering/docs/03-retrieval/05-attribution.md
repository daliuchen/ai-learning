# 引用与归因（Citation & Grounding）

> 把检索内容拼进窗口还不够，还要让模型的答案落在这些内容上，并标出处编号。给每个 chunk 编号、要求逐句引用、明确告诉模型找不到就说找不到，这几步做完，RAG 的每句答案都能对回原文核查。

---

## 1. 为什么要引用和归因

模型就算拿到了正确的检索内容，也可能出这几种问题：

- 脱离资料乱编（hallucination）：无视 context，用权重里的旧知识回答，语气照样很自信。
- 掺进资料里没有的细节：在真实内容里夹带自己编的东西，用户分不清哪句有依据、哪句是编的。
- 无法核查：用户或下游系统看不到答案出自哪一段，没法验证，出了问题也没法追溯。

Grounding（让回答以资料为依据）加上 Citation（引用），就是针对这三点：让模型只根据 context 回答，并说明每个结论来自哪一条。在合规、医疗、法律、客服这些场景里，这基本是硬性要求。

---

## 2. 基础做法：给 chunk 编号，要求引用

最朴素、也最通用的做法：给每个 chunk 一个稳定的编号，在 prompt 里要求模型引用编号。

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
    model="claude-sonnet-5-5",
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": user}],
)
print(next(b.text for b in resp.content if b.type == "text"))
# → "支持 7 天无理由退款，定制商品除外 [1][2]。配送默认 3-5 个工作日 [3]。"
```

四条规则都要有，其中第 3 条最重要。只说「基于 context 回答」约束力不够，必须明确给出「没有答案」这个出口，否则模型倾向于硬编一个。

---

## 3. 结构化引用：让程序能校验引用

让模型把答案和引用输出成 JSON，下游就可以自动校验、渲染脚注，甚至拦下没有引用的句子：

```python
import json

CITE_SYSTEM = (
    "依据 <context> 回答，输出严格 JSON：\n"
    '{"answer": "...", "claims": [{"text": "单条结论", "sources": [1,3]}]}\n'
    "每条 claim 的 sources 必须是 context 中真实存在的编号；"
    "无法支撑的结论不要输出。"
)

resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1024,
    system=CITE_SYSTEM,
    messages=[{"role": "user", "content": user}],   # user 同第 2 节，含编号后的 <context>
)
data = json.loads(next(b.text for b in resp.content if b.type == "text"))   # 要保证一定能解析，改用结构化输出（JSON schema）

# 校验：引用的编号必须真实存在，且每条结论都得有来源
valid_ids = set(range(1, len(chunks) + 1))
for claim in data["claims"]:
    assert claim["sources"], f"无来源的结论被拦截：{claim['text']}"
    assert set(claim["sources"]) <= valid_ids, "引用了不存在的编号（疑似幻觉）"
```

这样在返回给用户之前，就能用程序把编号是编造的、或者没有来源的结论打回去。比起纯文本里的 `[1]` 标注，这种方式更可控。

---

## 4. Claude 原生的 Citations 功能

除了自己写 prompt，Anthropic API 还提供原生的 Citations：把文档作为结构化的 `document` 内容块传进去，并开启 citations，模型返回的文本会自动带上精确到字符区间的引用，指向原文的具体位置，不靠模型自己去标编号。

```python
resp = client.messages.create(
    model="claude-sonnet-5-5",
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

好处是引用区间由系统标定，不是模型编的，所以更可信，不会引用到不存在的位置，也省得自己设计编号格式。合规要求高的生产环境，我会优先用原生 Citations；需要兼容多家模型时，再退回第 2、3 节的手写方案。

---

## 5. 防止乱编的几个加固手段

| 手段 | 作用 | 备注 |
|------|------|------|
| 明确的「无答案」出口 | 给模型一个说「没有」的合法选项 | 效果最好，一定要做 |
| 要求每个结论附编号 | 每句话都能查到依据 | 配合结构化输出可以自动校验 |
| 用程序校验引用 | 拦下编造的编号和没有来源的结论 | 见第 3 节 |
| 原生 Citations | 系统级的精确引用 | Claude 等模型支持，最可信 |
| 调低 temperature | 减少发散性的编造 | grounding 类任务建议 0 到 0.3；Claude 5.x 不接受 temperature（传了返回 400），用 Claude 时靠上面几条和原生 Citations |

```
# ❌ 弱 grounding：模型可能自信地编
system = "根据下面的资料回答问题。"

# ✅ 强 grounding：只用 context、逐句引用、没有就明说、可校验
system = "只依据 <context>；每个结论标 [编号]；找不到就答『资料中未提及』。"
```

上线前至少做到前三条：有「无答案」出口、每个结论带编号、程序校验编号是否真实存在。

---

## 下一步

- 回顾：[检索结果的排序、裁剪、去重](./04-rank-trim.md)，进窗口前给 chunk 编的号，正好接上这里的引用
- 跨章：[如何评测上下文质量](../08-production/04-evaluation.md)，怎么评测 RAG 的 grounding 和引用准确率
- 跨章：[短期记忆 vs 长期记忆](../04-memory/01-short-vs-long.md)，检索之外，记忆也是上下文的动态来源
