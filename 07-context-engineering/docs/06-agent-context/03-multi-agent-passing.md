# 多 Agent 间的上下文传递

> **一句话**：当主 Agent 把子任务交给 sub-agent，关键不是「传多少上下文」而是「传哪一小片」——给子 Agent **任务相关的最少切片**而非全量历史，让它在干净的窗口里干活、只把压缩后的结论交回来，这往往比共享一条大上下文更准、更省、更可并行。

---

## 1. 多 Agent 的两个传递时刻

无论是 orchestrator-worker（编排者派活）还是 handoff（交接控制权），都涉及两次上下文穿越边界：

```
                   ① 下传（task brief）
   主 Agent ───────────────────────────────▶ sub-agent
   （编排）                                    （干净窗口里执行子任务）
          ◀───────────────────────────────
                   ② 上传（compressed result）
```

- **① 下传**：主 Agent 给子 Agent 多少上下文？全量历史？还是只给任务相关切片？
- **② 上传**：子 Agent 跑完一堆步骤产生几万 token 轨迹，怎么压缩成几百 token 交回主 Agent？

两个方向都做错，多 Agent 不但不省 token，反而比单 Agent 更费——因为上下文在边界来回复制。

---

## 2. 下传：给最少必要上下文，不是全量

最常见的错误是把主 Agent 的完整 messages 一股脑塞给子 Agent。这等于让子 Agent 背着主任务的全部噪声去干一件很窄的活。

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

好的 task brief 三要素：**目标（做什么）+ 约束（边界 / 返回格式）+ 必要素材（句柄而非全文）**。子 Agent 拿到这个就能独立开工，不需要也不应该看见主 Agent 的全部历史。

---

## 3. 上传：把子 Agent 的长轨迹压缩成结论

子 Agent 内部可能跑了 20 步、产生 60K token 轨迹，但主 Agent **只需要结果**。直接把子 Agent 的全部 messages 回灌主上下文是灾难——多 Agent 的省钱优势全没了。

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

原则：**子 Agent 的轨迹是它的私有上下文，跟着子任务一起销毁；只有「主 Agent 推进所需的最终产物」才穿越边界回到主上下文。**

---

## 4. 为什么「各自干净的上下文」通常更好

这是 2025-2026 多 Agent 编排的核心共识——隔离的窗口胜过共享的大上下文：

| 维度 | 共享一条大上下文 | 每 Agent 各自干净窗口 |
|------|------------------|----------------------|
| **注意力** | 所有 Agent 的轨迹互相稀释，context rot 严重 | 每个 Agent 窗口只有本职任务，注意力集中 |
| **成本** | 大上下文每步重发，O(N²) 叠加 | 子任务轨迹不进主上下文，主线保持精简 |
| **并行** | 共享状态难并行 | 互不依赖的子 Agent 可同时跑 |
| **错误隔离** | 一个子任务的错误结果污染全局 | 子 Agent 的试错留在自己窗口，不传染（见 [06-failure-modes.md](./06-failure-modes.md)） |
| **可复现** | 全局上下文耦合，难调试 | 子任务输入（brief）+ 输出（结论）边界清晰 |

代价是**写协调一致型任务时会割裂**：如果子任务之间需要频繁共享中间状态、或最终要拼成一篇风格统一的长文，独立窗口的子 Agent 各写各的容易不连贯。这类任务要么不拆、要么靠主 Agent 做收口整合。隔离的取舍详见下一篇 [04-isolation.md](./04-isolation.md)。

---

## 5. handoff 时的上下文裁剪

handoff（一个 Agent 把控制权整个交给另一个，常见于 OpenAI Agents SDK 的 handoff 机制）和 orchestrator-worker 不同——它不是「派活后等结果回来」，而是**接力**。这里上下文是连续的，但仍要裁剪：

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

不论 orchestrator-worker 还是 handoff，同一条铁律：**上下文跨 Agent 边界时必须被裁剪压缩，而不是原样复制**。

---

## 下一步

- [04-isolation.md](./04-isolation.md)：上下文隔离——为每个子任务开独立窗口
- [02-tool-results.md](./02-tool-results.md)：句柄机制，子 Agent 怎么按需取回素材而非接收全文
- 跨章：[../05-compaction/02-summarization.md](../05-compaction/02-summarization.md) 子 Agent 结果压缩用到的摘要技术
- 跨章：[../09-practice/03-multi-agent-research.md](../09-practice/03-multi-agent-research.md) 多 Agent research 的端到端实践
