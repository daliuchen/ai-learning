# Just-in-time 检索 / Agentic Search

> **一句话**：别在生成前替模型预检索，**把搜索做成工具，让模型自己决定查什么、查几次、还要不要再查**——这就是 2025-2026 Claude / GPT agent 的主流玩法，准但慢，和一次性 RAG 各有适用面。

---

## 1. 从「预检索」到「按需检索」

经典 RAG 是**预检索（pre-retrieval）**：管线在调模型前就用 query 检索好，把结果硬塞进上下文，模型只能用你喂的那几段。

Just-in-time（JIT）检索反过来：**不预先检索，把检索能力暴露成工具，模型在推理过程中自己调用**。

```
经典 RAG（预检索）                  Agentic Search（按需检索）
────────────────                   ──────────────────────────
query → 检索 → 拼 → 生成            query → 模型推理
       (一次，固定)                       ↓ 不够？调 search 工具
                                          ↓ 看结果，还不够？再调
                                          ↓ 够了 → 生成
                                    (多次，模型自主决定)
```

本质区别：**检索的"决策权"从管线转移到了模型**。模型可以先粗查、看结果、改写 query 再细查，像人查资料一样迭代。

---

## 2. 把检索做成工具

JIT 的核心实现就是给模型一个 `search` 工具（外加 `read_file` 之类）：

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
            model="claude-sonnet-4-6",
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

注意循环结构：模型每轮要么调 `search_kb`（我们执行后把结果回灌），要么直接出答案（`stop_reason != "tool_use"`）。**几轮、查什么，全由模型自己定**。

---

## 3. Agentic 检索的优势

| 优势 | 说明 |
|------|------|
| **模型自定 query** | 用户问得含糊时，模型能改写成更好的检索词；甚至拆成多个子查询 |
| **迭代加深** | 先粗查，看到线索后用新关键词细查——单轮 RAG 做不到这种多跳 |
| **按需触发** | 答案已在上下文里就不检索，省一次往返；真不够才查 |
| **多源组合** | 可以给多个工具（知识库、读文件、查数据库、调 API），模型自己选用哪个 |
| **上下文更干净** | 只在需要时拉相关内容进窗口，不像预检索那样一股脑塞 top-k 噪声 |

最后一条在上下文工程里尤其关键：JIT 天然贴合"**按需把 token 拉进窗口**"的理念，对抗 context rot（第 1 篇）。Anthropic 把这种思路称为 "just-in-time context"——让 agent 像人一样用轻量标识（文件路径、链接、ID）按需加载，而不是预先灌满。

---

## 4. 代价与适用面

JIT 不是免费午餐：

- **慢**：多轮往返，每轮一次模型调用 + 一次检索，延迟成倍。
- **贵**：累计 token 和调用次数上去了；中间轮的检索结果都留在上下文里。
- **可能跑偏 / 不收敛**：模型可能反复无效检索，必须设 `max_turns` 兜底。
- **难调试**：检索路径不固定，复现 bug 比预检索难。

对比，什么时候用哪个：

| 场景 | 选择 |
|------|------|
| 简单事实问答、延迟敏感、query 清晰 | 一次性 RAG（预检索） |
| 复杂/多跳问题、需要迭代探索 | Agentic search（JIT） |
| 代码库 / 大文件系统导航 | JIT（模型按路径逐步读取，几乎是事实标准） |
| 高并发、成本敏感的客服 | 预检索为主，必要时单次回退检索 |

```
# ❌ 简单问答硬上 agentic：5 轮往返答一句"营业到几点"，慢且贵
# ✅ 简单问答用一次性 RAG；复杂探索 / 代码任务才上 agentic search
```

---

## 5. 2025-2026 趋势：agentic search 成主流

几个值得知道的现状：

- **Claude / GPT 的原生方向**：Claude（Agent SDK / Claude Code）和 GPT 的 agent 模式都默认走"工具化检索 + 多轮自主推理"，而非把知识预灌进 prompt。代码助手读代码库就是典型 JIT——按需 grep、read file，而不是把整个 repo 塞进窗口。
- **托管 Web 搜索工具**：各家提供了内置 `web_search` 类工具，模型自主决定何时上网查，是 agentic search 的特例。
- **混合范式**：成熟系统常是「静态核心知识（缓存）+ 必要时 agentic 检索补充」，结合第 2 篇的静态/动态思路。

一句话定位：**预检索是"我替你查好了"，agentic search 是"工具给你，自己查"**。后者更接近通用 agent 的工作方式，但要为延迟、成本和收敛性买单。

---

## 下一步

- [04-rank-trim.md](./04-rank-trim.md)：无论预检索还是 JIT，结果进窗口前都要排序裁剪去重
- [05-attribution.md](./05-attribution.md)：让模型基于检索结果回答并标注出处
- 跨章：[../06-agent-context/02-tool-results.md](../06-agent-context/02-tool-results.md) agent 的工具上下文管理
