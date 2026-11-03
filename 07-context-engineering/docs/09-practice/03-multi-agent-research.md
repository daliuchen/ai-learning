# CE 09-03：实战 · 多 Agent 研究系统的上下文编排

> 一个 orchestrator 把研究任务拆成若干子任务，派给多个 worker sub-agent。每个 worker 在相互隔离的干净上下文里并行检索，只装自己那份资料（窗口用得多，但不必用满），结果压缩之后才交回 orchestrator。这套架构能消化的原文总量可以远超单个窗口，orchestrator 自己的窗口却只装摘要。

---

## 1. 为什么要多 Agent

一个深度研究任务，比如「调研 2026 年主流开源向量数据库的选型」，要读几十个来源。全塞进一个窗口肯定装不下，而且 context rot 会让中间的内容被忽略。

```
# ❌ 单 Agent：所有来源挤一个窗口
单窗口（按 200K 预算）── 塞 40 篇资料 ──▶ 超窗 / 腐烂 / 注意力稀释，结论平庸

# ✅ 多 Agent：每个 worker 一个独立窗口，并集远超单窗口
orchestrator (规划 + 综合，窗口只装"任务+摘要")
   ├── worker 1 (独立 200K，专读"性能对比"类来源)
   ├── worker 2 (独立 200K，专读"生态/社区"类来源)
   └── worker 3 (独立 200K，专读"成本/部署"类来源)
```

N 个 worker 就是 N 个独立的 200K 窗口（模型窗口本身已有 1M，这里按每个窗口 200K 的质量和成本预算来算；对应[多 Agent 间的上下文传递](../06-agent-context/03-multi-agent-passing.md)）。每个 worker 只装自己那部分资料，互不干扰；orchestrator 的窗口始终只装任务定义和各 worker 的压缩摘要，保持干净。容量能成倍增加，靠的是上下文隔离（context isolation）。

---

## 2. 整体架构

```
                    ┌───────────────────────────────────────┐
   研究问题 ───────▶│  Orchestrator（主控）                    │
                    │  ① 拆解问题 → N 个子任务                  │
                    │  ② 派发给 worker（各带独立干净上下文）     │
                    │  ⑤ 收集压缩摘要 → 综合成最终报告           │
                    └───────────┬───────────────────────────┘
                  派发(子任务+最小上下文)  │  ▲ 回传(压缩摘要,非原文)
              ┌────────────┬───────────────┴────┬───────────┐
              ▼            ▼                    ▼            
        ┌─────────┐  ┌─────────┐          ┌─────────┐       
        │ worker1 │  │ worker2 │  ......   │ workerN │  ← 并行
        │ 独立窗口 │  │ 独立窗口 │          │ 独立窗口 │       
        │ ③检索   │  │ ③检索   │          │ ③检索   │       
        │ ④压缩   │  │ ④压缩   │          │ ④压缩   │       
        └─────────┘  └─────────┘          └─────────┘       
```

数据在 Agent 之间流动时要经过两道裁剪，整个架构能不能成立就看这两道：

- 下行裁剪（orchestrator → worker）：只传这个子任务需要的最小上下文，不把全局历史灌给 worker，保证它的上下文干净。
- 上行裁剪（worker → orchestrator）：worker 读了大量资料，但只交回压缩后的发现（几百字摘要加关键引用），不交回原文，否则 orchestrator 的窗口一样会爆。

---

## 3. 上下文怎么在 Agent 间传递和裁剪

| 流向 | 传什么 | 不传什么 | 为什么 |
|------|--------|----------|--------|
| orch → worker | 子任务描述 + 简短的全局目标 + 输出格式 | orchestrator 的完整历史、其他 worker 的内容 | worker 要干净隔离，避免互相污染 |
| worker 内部 | 检索到的原文片段（只装自己那份，用得多但不必用满） | — | 重活放在 worker 这里做 |
| worker → orch | 结构化摘要 + 关键引用 + 置信度 | 读过的原文全文 | orchestrator 的窗口要轻，只做综合 |
| orch → 用户 | 综合报告 + 汇总引用 | 中间过程的噪声 | 最终交付要干净 |

---

## 4. 核心代码：Worker（隔离 + 检索 + 压缩）

每个 worker 都是一次全新的、干净的模型调用，没有共享历史，只有它自己的子任务和它自己检索到的资料。

```python
# ✅ Worker：独立干净上下文，检索 → 读 → 压缩回传
import asyncio
import anthropic

client = anthropic.AsyncAnthropic()  # 异步，便于并行

# 模拟检索：生产换成真 web search / 向量库
async def search(subtask: str) -> list[str]:
    await asyncio.sleep(0)  # 占位
    return [f"[来源] 关于「{subtask}」的资料正文片段……"] * 8  # 假设读到一批片段

WORKER_SYSTEM = """你是研究子 Agent。只负责你被分配的子任务。
读完给你的资料后，输出结构化发现：
1) 3-5 条核心发现（每条带来源标注）
2) 一句话置信度（高/中/低）
不要复述原文，只给提炼后的结论。控制在 400 字内。"""

async def run_worker(subtask: str, global_goal: str) -> dict:
    docs = await search(subtask)                      # ③ 独立检索，只装自己那份资料
    sources = "\n\n".join(f"[{i+1}] {d}" for i, d in enumerate(docs))
    resp = await client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=2048,
        system=WORKER_SYSTEM,
        # 下行裁剪：只给子任务 + 全局目标一句话，不给任何全局历史
        messages=[{"role": "user", "content":
                   f"全局研究目标：{global_goal}\n你的子任务：{subtask}\n\n资料：\n{sources}"}],
    )
    # ④ 上行裁剪：只回传压缩后的发现，原文留在 worker 窗口里不带走
    return {"subtask": subtask, "findings": next(b.text for b in resp.content if b.type == "text")}
```

worker 之间完全没有共享状态，worker 2 不知道 worker 1 读了什么。正因为这样隔离，它们才能并行，每个窗口里也只有和自己子任务相关的资料。

---

## 5. 核心代码：Orchestrator（拆解 + 派发 + 综合）

orchestrator 的窗口里自始至终只有三样东西：原始问题、子任务列表、各 worker 的压缩摘要。它从不接触原文。

```python
# ✅ Orchestrator：拆解 → 并行派发 → 综合
import json

async def plan(question: str) -> list[str]:
    """① 把研究问题拆成正交的子任务。"""
    resp = await client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=2048,
        messages=[{"role": "user", "content":
            f"把这个研究问题拆成 3-4 个互不重叠、可独立调研的子任务，"
            f"输出 JSON 字符串数组：\n{question}"}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    text = text[text.find("["): text.rfind("]") + 1]   # 容错抽 JSON
    return json.loads(text)

async def synthesize(question: str, results: list[dict]) -> str:
    """⑤ 把各 worker 的压缩摘要综合成最终报告。"""
    digest = "\n\n".join(
        f"## 子任务：{r['subtask']}\n{r['findings']}" for r in results)
    resp = await client.messages.create(
        model="claude-opus-5-5",   # 综合用更强的模型
        max_tokens=2048,
        messages=[{"role": "user", "content":
            f"研究问题：{question}\n\n各子调研的发现如下，请综合成一份结构化报告，"
            f"标注冲突点与来源：\n\n{digest}"}],
    )
    return next(b.text for b in resp.content if b.type == "text")

async def research(question: str) -> str:
    subtasks = await plan(question)                      # ① 拆解
    # ② 并行派发：N 个 worker 各自独立窗口同时跑
    results = await asyncio.gather(
        *(run_worker(st, question) for st in subtasks))
    return await synthesize(question, results)           # ⑤ 综合

# --- 跑起来 ---
report = asyncio.run(research("2026 年主流开源向量数据库该怎么选型？"))
print(report)
```

整条链路里，orchestrator 的窗口最多只装问题加 4 段各 400 字的摘要，约 2K token，跟 worker 们一共读了多少原文无关。

---

## 6. 为什么这种架构能突破单窗口限制

把账算一下就清楚了：

| 指标 | 单 Agent | 多 Agent（4 worker） |
|------|----------|---------------------|
| 可消化的原文总量 | 1 × 200K | 4 × 200K = 800K |
| orchestrator 实际窗口占用 | 200K（被原文挤爆） | ~2K（只装摘要） |
| 并行度 | 串行读 | 4 路并行，墙钟时间取决于最慢的那个 worker，理想情况约 1/4 |
| context rot 风险 | 高（窗口塞满） | 低（单个窗口内容单一） |
| 注意力质量 | 被稀释 | 每个 worker 专注一块 |

读原文这件事可以并行、可以横向扩展，综合只需要摘要，是收敛的。消化原文的重活交给一次性的 worker，全局推理留给上下文干净的 orchestrator。Anthropic 介绍自家多 Agent 研究系统时也提到，它在内部评测上明显好于单 Agent，表现差异大部分能由用掉的 token 量解释：多个 worker 并行地花了更多 token 去读资料。代价同样来自这里，这类系统的 token 消耗约是普通聊天的 15 倍。

---

## 7. 优化点

| 优化 | 做法 |
|------|------|
| 动态决定 worker 数 | 让 orchestrator 按问题复杂度决定派几个，别写死 |
| worker 内部再压缩 | 资料太多时，worker 先 map-reduce 做摘要，再产出发现 |
| 引用可溯源 | worker 的摘要带上来源 URL/ID，综合时保留，报告能点回原文 |
| 防止 worker 跑偏 | 子任务描述写清边界和输出格式，约束 worker 不越界 |
| 成本控制 | worker 用 Sonnet，只有 orchestrator 综合时用 Opus |
| 失败容错 | `asyncio.gather(..., return_exceptions=True)`，单个 worker 失败不拖垮全局 |

---

## 8. 常见坑

| 坑 | 后果 | 解法 |
|----|------|------|
| 把全局历史灌给每个 worker | worker 上下文被污染、token 暴涨，隔离的好处没了 | 下行只给最小的子任务上下文 |
| worker 交回原文 | orchestrator 的窗口一样会爆 | 上行强制压缩成摘要 |
| 子任务重叠 | 多个 worker 读同一批资料，浪费，结论也重复 | 拆解时要求子任务互不重叠 |
| 串行跑 worker | 没有并行，就没有速度优势 | 用 `asyncio.gather` 真正并行 |
| 综合时丢了冲突点 | 报告把相互矛盾的来源抹平，结论失真 | 综合 prompt 里明确要求标注冲突 |
| 无限递归派 sub-agent | 成本和延迟失控 | 限制层级深度，一般 1-2 层就够 |

---

## 9. 下一步

- Agent 上下文与 sub-agent 的隔离：[多 Agent 间的上下文传递](../06-agent-context/03-multi-agent-passing.md)
- 上下文隔离与 context offloading：[上下文隔离](../06-agent-context/04-isolation.md)
- worker 结果的压缩策略：[摘要式压缩](../05-compaction/02-summarization.md)
- 为什么上下文腐烂逼着我们做隔离：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- 三篇实战到这里就结束了，其他手册见[仓库首页](../../../README.md)

## 参考资料

- Anthropic, "How we built our multi-agent research system": https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic Messages API（async / 并行）：https://platform.claude.com/docs/en/api/messages
