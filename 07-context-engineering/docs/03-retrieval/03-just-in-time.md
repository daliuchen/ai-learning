# Just-in-time 检索 / Agentic Search

> Just-in-time 检索把搜索做成工具，交给模型自己决定查什么、查几次、要不要再查，生成前不再预先检索。Claude Code 这类 coding agent 找资料主要就靠这种方式，复杂问题上结果更准但更慢，和一次性 RAG 各有适合的场景。这篇讲它怎么实现、好在哪、代价是什么。

---

## 1. 从预检索到按需检索

经典 RAG 是预检索（pre-retrieval）：调用模型之前，流水线先用 query 检索好，把结果直接塞进上下文，模型只能用你给的那几段。

Just-in-time（JIT）检索反过来：不预先检索，而是把检索能力做成工具，模型在推理过程中自己调用。

```
经典 RAG（预检索）                  Agentic Search（按需检索）
────────────────                   ──────────────────────────
query → 检索 → 拼 → 生成            query → 模型推理
       (一次，固定)                       ↓ 不够？调 search 工具
                                          ↓ 看结果，还不够？再调
                                          ↓ 够了 → 生成
                                    (多次，模型自主决定)
```

两者的区别在于，**检索的决定权从流水线交给了模型**。模型可以先粗查一遍，看看结果，改写 query 再细查，像人查资料一样一轮轮迭代。

---

## 2. 把检索做成工具

JIT 的实现就是给模型一个 `search` 工具，有时再加上 `read_file` 之类：

```python
from anthropic import Anthropic

client = Anthropic()

TOOLS = [
    {
        "name": "search_kb",
        "description": (
            "在公司知识库中检索。当你需要的信息不在已知上下文里时调用。"
            "可以多次调用、用不同关键词，直到拿到足够信息再回答。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"},
                "k": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": ["query"],
        },
    }
]

def search_kb(query: str, k: int = 5) -> str:
    chunks = retriever.search(query, k=k)   # 底层机制见 Embedding 手册
    return "\n\n".join(f"[{i+1}] {c.text}" for i, c in enumerate(chunks))

def agentic_answer(user_query: str, max_turns: int = 5) -> str:
    messages = [{"role": "user", "content": user_query}]
    for _ in range(max_turns):
        resp = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            # 模型决定不再检索，直接给答案
            return "".join(b.text for b in resp.content if b.type == "text")

        # 执行模型发起的（可能多个）检索调用
        results = []
        for block in resp.content:
            if block.type == "tool_use" and block.name == "search_kb":
                out = search_kb(**block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": out,
                })
        messages.append({"role": "user", "content": results})
    return "（达到最大检索轮数仍未收敛）"
```

看循环结构：模型每一轮要么调用 `search_kb`（我们执行后把结果回灌给它），要么直接给出答案（`stop_reason != "tool_use"`）。查几轮、查什么，都由模型自己决定。

---

## 3. Agentic 检索的好处

| 好处 | 说明 |
|------|------|
| 模型自己定 query | 用户问得含糊时，模型能改写成更好的检索词，甚至拆成几个子查询 |
| 逐步深入 | 先粗查，看到线索后换关键词细查，单轮 RAG 做不到这种多跳 |
| 按需触发 | 答案已经在上下文里就不检索，省一次往返，确实不够才查 |
| 多源组合 | 可以给好几个工具（知识库、读文件、查数据库、调 API），模型自己选用哪个 |
| 上下文更干净 | 只在需要时把相关内容拉进窗口，不像预检索那样一股脑塞进 top-k 和其中的噪声 |

从上下文工程的角度，最后一条最有价值：JIT 按需把 token 拉进窗口，能缓解 context rot（见第 1 篇）。Anthropic 把这种思路叫 "just-in-time context"：agent 像人一样，拿着文件路径、链接、ID 这类轻量标识，用到时再加载，而不是事先全部灌进去。

---

## 4. 代价和适用场景

JIT 也有代价：

- 慢：多轮往返，每轮都是一次模型调用加一次检索，延迟成倍增加。
- 贵：累计的 token 和调用次数都上去了，中间几轮的检索结果也都留在上下文里。
- 可能跑偏或停不下来：模型可能反复做无效检索，必须设 `max_turns` 兜底。
- 难调试：检索路径不固定，复现 bug 比预检索难。

什么时候用哪个：

| 场景 | 选择 |
|------|------|
| 简单事实问答，对延迟敏感，query 清楚 | 一次性 RAG（预检索） |
| 复杂或多跳的问题，需要迭代探索 | Agentic search（JIT） |
| 在代码库或大型文件系统里找东西 | JIT，模型按路径一步步读，基本已是通行做法 |
| 高并发、成本敏感的客服 | 以预检索为主，必要时退回到单次检索 |

```
# ❌ 简单问答硬上 agentic：5 轮往返答一句"营业到几点"，慢且贵
# ✅ 简单问答用一次性 RAG；复杂探索 / 代码任务才上 agentic search
```

---

## 5. 2026 年的现状

- Claude Agent SDK、Claude Code 和 OpenAI 的 agent 产品都以工具化检索加多轮自主推理为主，而不是把知识预先灌进 prompt。代码助手读代码库就是 JIT：按需 grep、读文件，不会把整个 repo 塞进窗口。
- 各家都提供了托管的 Web 搜索工具，比如内置的 `web_search`，由模型自己决定什么时候上网查，可以看作 agentic search 的一个特例。
- 成熟的系统一般是混合的：核心知识静态放进前缀并缓存，必要时再用 agentic 检索补充，也就是把第 2 篇的静态、动态思路结合起来。

预检索相当于「我替你查好了」，agentic search 相当于「工具给你，自己查」。后者更接近通用 agent 的工作方式，但延迟、成本和能否收敛都要自己兜住。

---

## 下一步

- [检索结果的排序、裁剪、去重](./04-rank-trim.md)：不管是预检索还是 JIT，结果进窗口前都要排序、裁剪、去重
- [引用与归因（Citation & Grounding）](./05-attribution.md)：让模型基于检索结果回答并标注出处
- 跨章，agent 的工具上下文管理：[工具结果的上下文管理](../06-agent-context/02-tool-results.md)
