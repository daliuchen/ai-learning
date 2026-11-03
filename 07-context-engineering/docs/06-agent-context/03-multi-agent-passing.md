# 多 Agent 间的上下文传递

> 主 Agent 把子任务交给 sub-agent 时，要想清楚传哪一小片上下文。只给子 Agent 和任务相关的最少切片，不给全量历史，让它在干净的窗口里干活，只把压缩后的结论交回来。这样通常比几个 Agent 共享一条大上下文更准，主上下文更精简，也更容易并行。

---

## 1. 多 Agent 的两个传递时刻

不管是 orchestrator-worker（编排者派活）还是 handoff（交接控制权），上下文都要两次穿越边界：

```
                   ① 下传（task brief）
   主 Agent ───────────────────────────────▶ sub-agent
   （编排）                                    （干净窗口里执行子任务）
          ◀───────────────────────────────
                   ② 上传（compressed result）
```

第一次是下传：主 Agent 给子 Agent 多少上下文，全量历史，还是只给和任务相关的切片？第二次是上传：子 Agent 跑完一堆步骤，产生几万 token 的轨迹，怎么压成几百 token 交回主 Agent？

两个方向都做错的话，多 Agent 不仅不省 token，反而比单 Agent 更费，因为上下文在边界两边来回复制。

---

## 2. 下传：给最少必要的上下文

最常见的错误是把主 Agent 的完整 messages 整个塞给子 Agent，等于让子 Agent 背着主任务的全部噪声去干一件很窄的活。

```python
# ❌ 反模式：把主 Agent 全量历史下传给子 Agent
sub_messages = main_messages + [{"role": "user", "content": "查一下这个库的最新版本"}]
# 子 Agent 窗口里塞满与「查版本」毫无关系的历史，又贵又容易跑偏

# ✅ 正解：构造一个自包含的 task brief，只给完成子任务所需的东西
task_brief = {
    "goal": "查出 pydantic 当前在 PyPI 上的最新稳定版本号",
    "context": "主任务在写依赖升级方案，只需要版本号这一个事实",
    "constraints": "只返回版本号字符串，不要解释",
    "artifacts": ["handle=deps-3f2a"],   # 需要时让子 Agent 自己取回（见上一篇）
}
```

一份好的 task brief 包含三样东西：目标（做什么）、约束（边界和返回格式）、必要素材（给句柄，不给全文）。子 Agent 拿到这些就能独立开工，它不需要、也不应该看到主 Agent 的全部历史。

---

## 3. 上传：把子 Agent 的长轨迹压成结论

子 Agent 内部可能跑了 20 步，产生 60K token 的轨迹，但主 Agent 只需要结果。把子 Agent 的全部 messages 直接灌回主上下文，多 Agent 省钱的优势就全没了。

```python
from anthropic import Anthropic
client = Anthropic()

def run_subagent(task_brief: dict) -> dict:
    """子 Agent 在自己的窗口里跑完，只返回压缩结论。"""
    sub_messages = [{"role": "user", "content": render_brief(task_brief)}]
    # ... 子 Agent 内部 ReAct 循环，几十步，几万 token 轨迹 ...
    trajectory = run_react_loop(sub_messages, tools=SUB_TOOLS)

    # 关键：只把「主 Agent 需要的最终产物」交回去，轨迹留在子 Agent 自己的窗口里随任务结束丢弃
    return {
        "answer": extract_final_answer(trajectory),     # 简短结论
        "artifacts": list_new_handles(trajectory),       # 新产生的落盘句柄（要细节自己取）
        # 不返回：思考链、工具调用全文、中间试错——这些是子 Agent 的私有上下文
    }

# 主 Agent 侧：只把结论 append 回去
res = run_subagent(task_brief)
main_messages.append({"role": "user", "content":
    f"[子任务完成] {res['answer']}\n相关产物：{res['artifacts']}"})
```

子 Agent 的轨迹是它的私有上下文，随子任务一起丢弃；只有主 Agent 往下推进需要的最终产物，才回到主上下文。

---

## 4. 为什么各自用干净的上下文通常更好

对能拆开独立完成的子任务，各自用干净的窗口通常比共享一条大上下文效果好。这一点也有反对声音：Cognition 的《Don't Build Multi-Agents》主张共享完整上下文、少拆子 Agent，理由就是本节末尾说的割裂问题。

| 维度 | 共享一条大上下文 | 每 Agent 各自干净窗口 |
|------|------------------|----------------------|
| 注意力 | 所有 Agent 的轨迹互相稀释，context rot 严重 | 每个 Agent 窗口只有本职任务，注意力集中 |
| 成本 | 大上下文每步重发，O(N²) 叠加 | 子任务轨迹不进主上下文，主线保持精简 |
| 并行 | 共享状态难并行 | 互不依赖的子 Agent 可同时跑 |
| 错误隔离 | 一个子任务的错误结果污染全局 | 子 Agent 的试错留在自己窗口，不传染（见 [上下文相关的 Agent 失败模式](./06-failure-modes.md)） |
| 可复现 | 全局上下文耦合，难调试 | 子任务输入（brief）+ 输出（结论）边界清晰 |

代价是需要协调一致的任务会被割裂。如果子任务之间要频繁共享中间状态，或者最后要拼成一篇风格统一的长文，各自独立窗口的子 Agent 各写各的，结果容易接不上。这类任务要么不拆，要么由主 Agent 负责最后的整合。隔离的取舍在下一篇 [上下文隔离](./04-isolation.md) 里细讲。

---

## 5. handoff 时的上下文裁剪

handoff 是一个 Agent 把控制权整个交给另一个，OpenAI Agents SDK 的 handoff 机制就是这种。它和 orchestrator-worker 不同，它是接力，交出去就不等结果回来。这时上下文是连续的（Agents SDK 默认把完整对话历史交给接手的 Agent，可以用 handoff 的 `input_filter` 过滤），但仍然要裁剪：

```python
# ✅ handoff 时只携带「接力者需要的状态」，丢弃前一个 Agent 的工作细节
def build_handoff_context(prev_messages, target_role: str) -> list:
    # 不是把 prev_messages 全量交棒，而是提炼一份交接摘要
    handoff_summary = summarize_for_handoff(prev_messages, target_role)
    return [
        {"role": "system", "content": role_prompt(target_role)},
        {"role": "user", "content": f"[交接摘要]\n{handoff_summary}"},
    ]
# 退款 Agent 接手时，只需要「订单号 + 已核实事实 + 待办」，
# 不需要前一个客服 Agent 的全部寒暄和工具调用日志
```

不管是 orchestrator-worker 还是 handoff，规则都一样：上下文跨过 Agent 边界时，要裁剪压缩，不能原样复制。

---

## 下一步

- [上下文隔离](./04-isolation.md)：为每个子任务开独立窗口
- [工具结果的上下文管理](./02-tool-results.md)：句柄机制，子 Agent 怎么按需取回素材，而不是接收全文
- 跨章：[摘要式压缩](../05-compaction/02-summarization.md)，压缩子 Agent 结果用到的摘要技术
- 跨章：[实战 · 多 Agent 研究系统的上下文编排](../09-practice/03-multi-agent-research.md)，多 Agent research 的端到端实践
