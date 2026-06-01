# 上下文隔离

> **一句话**：上下文隔离就是给每个子任务 / 子 Agent **一块独立的窗口**，让它们互不污染、互不稀释——这是「最少必要上下文」原则在多 Agent 编排里的落地，能同时换来更高准确率、更低 token 成本和可并行执行。

---

## 1. 隔离要解决什么：污染与稀释

把所有子任务塞进同一条上下文，会发生两件坏事：

- **污染（pollution）**：子任务 A 的工具结果、试错、甚至错误信息，留在共享窗口里，被子任务 B 看到并误用。一个错误事实进了上下文，后续所有 Agent 都被它带偏（即 context poisoning，见 [06-failure-modes.md](./06-failure-modes.md)）。
- **稀释（dilution）**：每个子任务都只关心自己那一小片，但共享窗口里塞着所有人的内容，模型对任意单片的注意力被摊薄——context rot 加剧，越往后越笨。

```
# ❌ 共享窗口：5 个子任务的轨迹挤在一条上下文
[ 任务A轨迹 30K | 任务B轨迹 25K | 任务C轨迹 40K | ... ]
# 处理任务C时，A/B 的内容纯属噪声，还可能把 C 带偏

# ✅ 隔离：每个任务自己的窗口，只有本职内容
任务A窗口: [ A的brief | A的轨迹 ]      ← 30K，干净
任务B窗口: [ B的brief | B的轨迹 ]      ← 25K，干净
任务C窗口: [ C的brief | C的轨迹 ]      ← 40K，干净
```

---

## 2. orchestrator-worker 下的隔离模式

最主流的隔离架构：一个**编排者（orchestrator）**只负责拆任务、派活、收口；多个**worker**各自在独立窗口里干一件子任务。Anthropic 的多 Agent research 系统、各类 deep-research Agent 都是这个形态。

```python
import concurrent.futures as cf

def orchestrate(main_task: str) -> str:
    # 编排者：在自己干净的窗口里只做规划，不执行细节
    subtasks = plan_subtasks(main_task)        # 拆成 N 个自包含子任务

    # 每个 worker 一块独立窗口，互不可见对方的轨迹
    with cf.ThreadPoolExecutor() as pool:
        results = list(pool.map(run_isolated_worker, subtasks))

    # 编排者收口：只看 worker 交回的压缩结论，不看它们的轨迹
    return synthesize(main_task, results)

def run_isolated_worker(subtask: dict) -> dict:
    messages = [{"role": "user", "content": render_brief(subtask)}]  # 全新上下文，零历史
    trajectory = run_react_loop(messages, tools=WORKER_TOOLS)
    return {"answer": extract_final_answer(trajectory)}              # 只回结论（见上一篇）
```

要点：

- **编排者窗口保持精简**：它只看到「子任务列表」和「各 worker 的结论」，从不接触 worker 内部几十步的轨迹。
- **worker 窗口从零开始**：每个 worker 只装自己的 task brief + 自己的轨迹，互相看不见。
- **收口在编排者**：最终整合由编排者在它的干净窗口里完成。

---

## 3. 隔离带来的三个收益

| 收益 | 机制 |
|------|------|
| **准确率↑** | 每个窗口只有相关内容，注意力集中，规避 context rot 和跨任务误用 |
| **省 token** | 子任务轨迹不进主上下文，主线不被几十万 token 的细节撑大；隔离窗口任务结束即销毁 |
| **可并行** | 互不依赖的 worker 没有共享可变状态，可同时跑，墙钟时间大幅缩短 |

第三点尤其值钱：research / 多文件改造这类任务，5 个 worker 并行跑，总耗时接近单个 worker，而共享上下文方案只能串行。

---

## 4. 隔离 vs 共享：什么时候不该隔离

隔离不是万能。它的前提是**子任务足够独立**。当子任务之间高度耦合，强行隔离反而出问题：

| 任务特征 | 选隔离 | 选共享 |
|----------|--------|--------|
| 子任务互相独立（并行检索多个主题） | ✅ | |
| 只读、不互相依赖中间结果 | ✅ | |
| 子任务需要实时看到彼此的中间状态 | | ✅ |
| 最终要拼成风格 / 逻辑连贯的整体（如一篇长文） | | ✅（或编排者强力收口） |
| 写操作有顺序依赖（A 必须先于 B） | | ✅ |

典型反例：让 3 个隔离 worker 各写一篇报告的不同章节，它们看不见彼此，结论可能互相矛盾、术语不统一、重复论述。这种**协调密集型**任务要么不拆，要么靠编排者做强整合（甚至重写）。判断准则：**子任务之间的依赖越弱，隔离收益越大。**

---

## 5. 隔离的实现层级

隔离不止「多 Agent」一种粒度，从轻到重：

- **同一 Agent，分阶段清空**：一个长任务内部，阶段 A 做完后把 A 的轨迹压成摘要、清掉细节，再进阶段 B——相当于时间维度的隔离（和 [../05-compaction/](../05-compaction/01-why-compact.md) 衔接）。
- **subagent / 工具式委派**：把一类脏活（如「探索整个目录结构」）封装成一个 subagent 工具，它在独立窗口里跑完只回摘要——Claude Code 的 subagent 就是这个思路，主上下文完全不被探索过程污染。
- **完整 orchestrator-worker**：独立进程 / 线程的多 Agent，各自完整窗口，如本篇第 2 节。

```python
# ✅ subagent 作为「带独立上下文的工具」：脏活在隔离窗口完成，主上下文只见结果
def explore_codebase_subagent(question: str) -> str:
    """主 Agent 调它就像调一个工具，但它内部有自己的完整 ReAct 窗口。"""
    iso_messages = [{"role": "user", "content": f"探索代码库回答：{question}"}]
    traj = run_react_loop(iso_messages, tools=[grep, read_file, ls])  # 几十步全在隔离窗口
    return extract_final_answer(traj)   # 主上下文只拿到一句结论
```

---

## 下一步

- [05-state-vs-context.md](./05-state-vs-context.md)：隔离窗口之间靠外部 state 而非共享上下文协调
- [03-multi-agent-passing.md](./03-multi-agent-passing.md)：隔离窗口的输入 brief 与输出结论怎么裁剪
- 跨章：[../05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md) 时间维度隔离用到的阶段性压缩
- 跨章：[../01-foundations/06-minimal-context.md](../01-foundations/06-minimal-context.md) 最少必要上下文原则
