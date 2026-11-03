# 上下文隔离

> 上下文隔离的做法很朴素：每个子任务或子 Agent 各用一块独立的窗口，彼此看不到对方的内容。这是「最少必要上下文」原则在多 Agent 编排里的落地，准确率、主上下文的长度和并行度都能从中受益，代价是总 token 往往更多。

---

## 1. 隔离要解决什么：污染与稀释

所有子任务挤在同一条上下文里，会出两类问题。

- 污染（pollution）：子任务 A 的工具结果、试错过程甚至错误信息留在共享窗口里，子任务 B 看到后拿来误用。一个错误事实进了上下文，后面的 Agent 都会被它带偏，也就是 context poisoning（见[上下文相关的 Agent 失败模式](./06-failure-modes.md)）。
- 稀释（dilution）：每个子任务只关心自己那一小片，窗口里却堆着所有人的内容，模型分给任何一片的注意力都变少，context rot 加剧，越往后表现越差。

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

用得最多的隔离架构是一个编排者（orchestrator）加若干 worker。编排者只拆任务、派活、汇总，每个 worker 在自己的窗口里做一件子任务。Anthropic 的多 Agent research 系统和各类 deep-research Agent 都是这个结构。

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

这段代码里有三点要守住。编排者的窗口要保持精简，里面只有子任务列表和各 worker 交回的结论，worker 内部几十步的轨迹它一步都不看。worker 的窗口从零开始，只装自己的 task brief 和自己的轨迹，彼此不可见。最后的整合在编排者那块干净窗口里完成。

---

## 3. 隔离带来的三个收益

| 收益 | 机制 |
|------|------|
| 准确率提高 | 每个窗口只有相关内容，注意力集中，避开 context rot 和跨任务误用 |
| 主上下文精简 | 子任务轨迹不进主上下文，主线不会被几十万 token 的细节撑大；隔离窗口在任务结束后就丢弃。注意总 token 不一定少：Anthropic 介绍多 Agent research 系统时提到，它的 token 消耗约是普通聊天的 15 倍 |
| 可并行 | 互不依赖的 worker 没有共享的可变状态，可以同时跑，墙钟时间大幅缩短 |

并行这一点最实在。research 或多文件改造这类任务，5 个 worker 同时跑，总耗时接近最慢的那一个 worker，共享上下文的方案只能一个接一个串行。

---

## 4. 隔离 vs 共享：什么时候不该隔离

隔离的前提是子任务足够独立。子任务之间耦合很紧时，硬拆开反而出问题：

| 任务特征 | 选隔离 | 选共享 |
|----------|--------|--------|
| 子任务互相独立（并行检索多个主题） | 适合 | |
| 只读、不互相依赖中间结果 | 适合 | |
| 子任务需要实时看到彼此的中间状态 | | 适合 |
| 最终要拼成风格、逻辑连贯的整体（如一篇长文） | | 适合（或由编排者强力收口） |
| 写操作有顺序依赖（A 必须先于 B） | | 适合 |

一个常见的反例是让 3 个隔离的 worker 分头写一篇报告的不同章节。它们看不到彼此，结论可能互相矛盾，术语不统一，还会重复论述。这种需要大量协调的任务，要么干脆不拆，要么由编排者做强整合，必要时整篇重写。子任务之间的依赖越弱，隔离的收益越大。

---

## 5. 隔离的实现层级

多 Agent 只是隔离的一种粒度。从轻到重大致有三层：

- 同一 Agent 内分阶段清空：长任务做完阶段 A 后，把 A 的轨迹压成摘要、清掉细节，再进入阶段 B，相当于在时间维度上做隔离（和[压缩那一章](../05-compaction/01-why-compact.md)衔接）。
- subagent 或工具式委派：把一类脏活（比如探索整个目录结构）封装成 subagent 工具，它在独立窗口里跑完，只交回摘要。Claude Code 的 subagent 就是这个思路，探索过程完全不进主上下文。
- 完整的 orchestrator-worker：多个独立进程或线程的 Agent，各有完整窗口，也就是第 2 节的结构。

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

- [State vs Context：不是所有状态都该进上下文](./05-state-vs-context.md)：隔离的窗口之间靠外部 state 协调，不共享上下文
- [多 Agent 间的上下文传递](./03-multi-agent-passing.md)：隔离窗口的输入 brief 和输出结论怎么裁剪
- 跨章：[为什么要压缩上下文](../05-compaction/01-why-compact.md)，时间维度隔离用到的阶段性压缩
- 跨章：[最少必要上下文原则](../01-foundations/06-minimal-context.md)
