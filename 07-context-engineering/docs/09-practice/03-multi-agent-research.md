# CE 09-03：实战 · 多 Agent 研究系统的上下文编排

> **一句话**：一个 orchestrator 把研究任务拆成子任务，派给多个 worker sub-agent；每个 worker 在**干净隔离**的上下文里并行检索、各自塞满自己的窗口，结果压缩后才汇回 orchestrator。这套架构的精髓不是「多个模型」，而是**用多个独立上下文窗口的并集，突破单个窗口的物理上限**。

---

## 1. 为什么要多 Agent：突破单窗口

一个深度研究任务（「调研 2025 年主流开源向量数据库的选型」）要读几十个来源，全塞进一个窗口必然爆，且 context rot 让中间内容被忽略。

```
# ❌ 单 Agent：所有来源挤一个窗口
单窗口 200K ── 塞 40 篇资料 ──▶ 超窗 / 腐烂 / 注意力稀释，结论平庸

# ✅ 多 Agent：每个 worker 一个独立窗口，并集远超单窗口
orchestrator (规划 + 综合，窗口只装"任务+摘要")
   ├── worker 1 (独立 200K，专读"性能对比"类来源)
   ├── worker 2 (独立 200K，专读"生态/社区"类来源)
   └── worker 3 (独立 200K，专读"成本/部署"类来源)
```

关键洞察（串 [06-agent-context](../06-agent-context/03-multi-agent-passing.md)）：**N 个 worker = N 个独立的 200K 窗口**。每个 worker 只装自己那块的资料，互不干扰；orchestrator 的窗口永远只装「任务定义 + 各 worker 的压缩摘要」，保持清爽。这是「上下文隔离」（context isolation）带来的容量倍增。

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

数据在 agent 间的流动有两道**裁剪关卡**，是整个架构成立的关键：

- **下行裁剪**（orchestrator → worker）：只传「这个子任务需要的最小上下文」，不把全局历史灌给 worker，保证它上下文干净。
- **上行裁剪**（worker → orchestrator）：worker 读了一窗口资料，但**只回传压缩后的发现**（几百字摘要 + 关键引用），不回传原文——否则 orchestrator 窗口照样爆。

---

## 3. 上下文怎么在 Agent 间传递和裁剪

| 流向 | 传什么 | 不传什么 | 为什么 |
|------|--------|----------|--------|
| orch → worker | 子任务描述 + 全局目标一句话 + 输出格式 | orchestrator 的完整历史、其他 worker 的内容 | worker 要干净隔离，避免互相污染 |
| worker 内部 | 检索到的原文片段（塞满自己的窗口） | — | 这是 worker 该「重」的地方 |
| worker → orch | 结构化摘要 + 关键引用 + 置信度 | 读过的原文全文 | orchestrator 窗口要「轻」，只综合 |
| orch → 用户 | 综合报告 + 汇总引用 | 中间过程的噪声 | 最终交付要干净 |

一句话记：**worker 重、orchestrator 轻；下行给最小、上行给摘要。**

---

## 4. 核心代码：Worker（隔离 + 检索 + 压缩）

每个 worker 是一次**全新的、干净的**模型调用——没有共享历史，只有它自己的子任务和它自己检索到的资料。

```python
# ✅ Worker：独立干净上下文，检索 → 读 → 压缩回传
import asyncio
import anthropic

client = anthropic.AsyncAnthropic()  # 异步，便于并行

# 模拟检索：生产换成真 web search / 向量库
async def search(subtask: str) -> list[str]:
    await asyncio.sleep(0)  # 占位
    return [f"[来源] 关于「{subtask}」的资料正文片段……"] * 8  # 假设塞满窗口

WORKER_SYSTEM = """你是研究子 Agent。只负责你被分配的子任务。
读完给你的资料后，输出结构化发现：
1) 3-5 条核心发现（每条带来源标注）
2) 一句话置信度（高/中/低）
不要复述原文，只给提炼后的结论。控制在 400 字内。"""

async def run_worker(subtask: str, global_goal: str) -> dict:
    docs = await search(subtask)                      # ③ 独立检索，塞满自己的窗口
    sources = "\n\n".join(f"[{i+1}] {d}" for i, d in enumerate(docs))
    resp = await client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=800,
        system=WORKER_SYSTEM,
        # 下行裁剪：只给子任务 + 全局目标一句话，不给任何全局历史
        messages=[{"role": "user", "content":
                   f"全局研究目标：{global_goal}\n你的子任务：{subtask}\n\n资料：\n{sources}"}],
    )
    # ④ 上行裁剪：只回传压缩后的发现，原文留在 worker 窗口里不带走
    return {"subtask": subtask, "findings": resp.content[0].text}
```

注意 worker 之间**完全无共享状态**——worker 2 不知道 worker 1 读了什么。这种隔离正是它们能并行、且各自吃满独立窗口的前提。

---

## 5. 核心代码：Orchestrator（拆解 + 派发 + 综合）

orchestrator 的窗口里**从头到尾只有**：原始问题、子任务列表、各 worker 的压缩摘要。它永远不碰原文。

```python
# ✅ Orchestrator：拆解 → 并行派发 → 综合
import json

async def plan(question: str) -> list[str]:
    """① 把研究问题拆成正交的子任务。"""
    resp = await client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content":
            f"把这个研究问题拆成 3-4 个互不重叠、可独立调研的子任务，"
            f"输出 JSON 字符串数组：\n{question}"}],
    )
    text = resp.content[0].text
    text = text[text.find("["): text.rfind("]") + 1]   # 容错抽 JSON
    return json.loads(text)

async def synthesize(question: str, results: list[dict]) -> str:
    """⑤ 把各 worker 的压缩摘要综合成最终报告。"""
    digest = "\n\n".join(
        f"## 子任务：{r['subtask']}\n{r['findings']}" for r in results)
    resp = await client.messages.create(
        model="claude-opus-4-1",   # 综合用更强的模型
        max_tokens=2048,
        messages=[{"role": "user", "content":
            f"研究问题：{question}\n\n各子调研的发现如下，请综合成一份结构化报告，"
            f"标注冲突点与来源：\n\n{digest}"}],
    )
    return resp.content[0].text

async def research(question: str) -> str:
    subtasks = await plan(question)                      # ① 拆解
    # ② 并行派发：N 个 worker 各自独立窗口同时跑
    results = await asyncio.gather(
        *(run_worker(st, question) for st in subtasks))
    return await synthesize(question, results)           # ⑤ 综合

# --- 跑起来 ---
report = asyncio.run(research("2025 年主流开源向量数据库该怎么选型？"))
print(report)
```

整条链路里，orchestrator 窗口装的最大也就是「问题 + 4 段各 400 字的摘要」——约 2K token，无论 worker 们总共读了多少原文。**这就是用多窗口并集突破单窗口上限的具体实现。**

---

## 6. 为什么这种架构能突破单窗口限制

把账算清楚就明白了：

| 指标 | 单 Agent | 多 Agent（4 worker） |
|------|----------|---------------------|
| 可消化的原文总量 | 1 × 200K | 4 × 200K = 800K |
| orchestrator 实际窗口占用 | 200K（被原文挤爆） | ~2K（只装摘要） |
| 并行度 | 串行读 | 4 路并行，墙钟时间约 1/4 |
| context rot 风险 | 高（窗口塞满） | 低（每个窗口都不满） |
| 注意力质量 | 稀释 | 每个 worker 专注一块 |

本质：**「读」是并行可水平扩展的（多窗口），「综合」是收敛的（只需摘要）**。把消化原文的重活下放给一次性的 worker、把全局推理留给上下文清爽的 orchestrator——这正是 Anthropic 多 Agent 研究系统跑赢单 Agent 的核心原因。

---

## 7. 优化点

| 优化 | 做法 |
|------|------|
| 动态 worker 数 | 让 orchestrator 按问题复杂度决定派几个，别写死 |
| worker 内再压缩 | worker 资料太多时，先 map-reduce 摘要再产出发现 |
| 引用可溯源 | worker 摘要带来源 URL/ID，综合时保留，报告可点回原文 |
| 防 worker 跑偏 | 子任务描述写清边界 + 输出格式，约束 worker 不越界 |
| 成本控制 | worker 用 Sonnet，仅 orchestrator 综合用 Opus |
| 失败容错 | `asyncio.gather(..., return_exceptions=True)`，单个 worker 挂了不拖垮全局 |

---

## 8. 常见坑

| 坑 | 后果 | 解法 |
|----|------|------|
| 把全局历史灌给每个 worker | worker 上下文被污染、token 暴涨、失去隔离优势 | 下行只给最小子任务上下文 |
| worker 回传原文 | orchestrator 窗口照样爆 | 上行强制压缩成摘要 |
| 子任务重叠 | 多个 worker 读同一批资料，浪费且结论冗余 | 拆解时要求「互不重叠」 |
| 串行跑 worker | 没有并行就没有速度优势 | `asyncio.gather` 真并行 |
| 综合时丢了冲突点 | 报告把矛盾来源抹平，结论失真 | 综合 prompt 显式要求标注冲突 |
| 无限递归派 sub-agent | 成本 / 延迟失控 | 限制层级深度（一般 1-2 层够用） |

---

## 9. 下一步

- 📖 Agent 上下文与 sub-agent 隔离 → [06-agent-context/02-sub-agents.md](../06-agent-context/03-multi-agent-passing.md)
- 📖 上下文隔离与 context offloading → [06-agent-context/03-context-isolation.md](../06-agent-context/04-isolation.md)
- 📖 worker 结果的压缩策略 → [05-compaction/02-map-reduce.md](../05-compaction/02-summarization.md)
- 📖 上下文腐烂为何逼着我们隔离 → [01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
- 🏠 三篇实战到此结束，回手册首页看全景 → [手册首页](/07-context-engineering)

---

> **致谢**：读到这里，你已经把上下文工程从「概念」走到了「能跑的系统」——分层记忆、长文档问答、多 Agent 编排，背后都是同一句话：**模型只看得见你放进窗口的东西，工程的全部价值在于决定放什么、怎么放、何时换。** 感谢一路读完这本手册，去把它用在你的项目里。

## 参考资料

- Anthropic, "How we built our multi-agent research system": https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, "Effective context engineering for AI agents": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic Messages API（async / 并行）：https://docs.anthropic.com/en/api/messages
