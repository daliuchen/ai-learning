# PE Practice 03：用 Claude Code 当 Prompt 优化器

> **一句话**：Claude Code（或类似 Agent）能成为你的 PE 协作者——帮你看 evalset 失败、提改进假设、改 prompt、跑 evalset 验证。本篇讲怎么让 Claude Code 进入这种"PE optimizer" 模式。

---

## 1. 思路

```
[你] 提供:
  - 当前 prompt (prompts/v_n.md)
  - evalset (evalset/v1.0.jsonl)
  - eval_runner.py
  - 评测结果 (results/v_n.json)
       ↓
[Claude Code] 做:
  1. 看失败案例聚类
  2. 提改进假设
  3. 改 prompt → v_{n+1}
  4. 跑 evalset 验证
  5. 给 diff + 报告
       ↓
[你] Review + 决定接受 / rollback
```

整个 PE 迭代闭环交给 Agent。

---

## 2. 起步：让 Claude Code 看你的项目

项目结构：

```
my-classifier/
├── prompts/
│   ├── v0.md
│   ├── v1.md
│   └── current.txt
├── evalset/
│   └── v1.0.jsonl
├── eval_runner.py
└── results/
    └── v0.json
```

打开 Claude Code 在这个目录，第一轮 prompt：

```
看一下当前项目结构。我有一个客服分类 prompt v0，在 evalset/v1.0.jsonl 上跑分 76%。
请：

1. 读 prompts/v0.md 理解当前 prompt
2. 读 results/v0.json 看失败案例
3. 把失败按"模式"聚类（每类 2-3 个例子）
4. 针对最大的失败类提一个改进假设
5. 写 prompts/v1.md（只针对那一类改）
6. 用 `python eval_runner.py prompts/v1.md evalset/v1.0.jsonl > results/v1.json` 跑评测
7. 比较 v0 vs v1 结果（fixed / broken / 净增）
8. 给我 diff 总结

只改 prompts/v1.md，不要动其他文件。
```

Claude Code 会按这个 plan 走。

---

## 3. 给 Claude Code 的"约束"

让它做得好的关键：明确的 PE 工程纪律。

```
重要约束（PE 工程纪律）：

1. 每轮只改 prompt 一处（不要同时改 5 处）
2. 不要改 evalset（评测变量必须冻结）
3. 用 git commit 每个版本（带 changelog）
4. 通过率不能下降 - regression > 0 时给出权衡分析
5. 总通过率不是唯一指标，按 tag 分组看
6. 输出报告必须含 fixed / broken / 净增 / 决策建议
7. 改完跑 evalset 验证，不要"我觉得这样会好"就报告
8. 失败的样本里如果发现是 evalset 本身有问题（标错），单独标注，不要默默修
```

Claude Code 是好工具但需要约束——否则它倾向"激进改"。

---

## 4. 多轮迭代

第二轮 prompt：

```
继续迭代。基于 v1 结果，做 v2。同样规则。
```

Claude Code 会：
- 读 v1 结果
- 找新的最大失败类（v1 解决了 reverse sarcasm，可能还有 multi-class 问题）
- 改一处 → v2
- 跑评测
- 报告

跑 5-10 轮 → 通常 88-93%。

---

## 5. 让 Claude Code 看 trace

如果有线上 trace 数据（LangSmith / Langfuse），可以让 Claude Code 也看：

```
我把过去 7 天 100 条线上 trace export 到 traces/recent.jsonl。
请看一下：

1. 用户感知差的 case 分布在什么类别
2. 哪些 input 模式 evalset 没覆盖
3. 提议加 10 条到 evalset

只提议，不要直接写 evalset（我要 review）。
```

Claude Code 充当 trace 分析师 + evalset 编辑器。

---

## 6. 让 Claude Code 重构 prompt

```
现在 v8 通过率 91%，但 prompt 已经 1500 token + 5 个示例。
请：

1. 看哪些约束是"叠加但没必要"（早期某轮的修补，后来已被新示例覆盖）
2. 哪些示例是冗余的
3. 给一个"精简版 v8.1"，目标减 30% token，通过率不降

跑 evalset 确认。
```

定期"减肥"避免 prompt 一直膨胀。

---

## 7. Claude Code 不擅长的事

| 不擅长 | 你来做 |
|--------|--------|
| **判断业务可接受门槛** | "91% 够吗" 要你 / PM 决定 |
| **决定上线** | 灰度策略、风险评估 |
| **改 evalset 的"权威"** | 加 / 改 / 删 evalset 必须人审 |
| **跨模型决策** | 换模型涉及成本 / 合规 |
| **架构改造** | 拆 sub-agent / 加 RAG 是大决策 |
| **rollback** | 别让 Agent 自动 rollback |

Claude Code 是**协作者**不是**决策者**。

---

## 8. 工作流自动化（高级）

可以把 PE 迭代写成 Claude Code Skill：

```markdown
# .claude/skills/pe-iter/SKILL.md

---
name: PE Iteration
description: 跑一轮 PE 迭代：看失败 → 改 prompt → 验证 → 报告
triggers:
  - "迭代一下 prompt"
  - "run pe iter"
---

工作流：
1. 找 prompts/current.txt 确定当前版本
2. 读最新 results/v_n.json
3. 按 PE 工程纪律改一处生成 v_{n+1}
4. 跑 evalset
5. git commit
6. 报告给用户
```

之后 Claude Code 里直接 `/pe-iter` 就跑一轮。

---

## 9. 完整示例对话

```
[You] 看一下项目。我有 prompts/v0.md，evalset/v1.0.jsonl，跑过一次 results/v0.json（76% 通过率）。
做一轮迭代。

[Claude Code] 让我看一下结构和数据...

读到 v0.md (500 字), evalset 100 条, results/v0.json。

失败聚类（24 条）:
1. sarcasm (反讽) - 6 条
2. multi-class - 5 条
3. PII detection - 4 条
4. confidence calibration (over-confident) - 5 条
5. other - 4 条

最大簇: sarcasm (6 条)

假设：v0 没 sarcasm 示例，模型按字面理解
改动：加 2 个反讽示例到 system

写 prompts/v1.md...
跑 eval_runner.py...

[Claude Code] 结果:

v0: 76/100
v1: 84/100  (+8)

By tag:
- sarcasm:     1/6 → 5/6  ✅ (+4)
- multi-class: 5/10 → 5/10
- pii:         1/5 → 1/5
- confidence:  0/5 → 0/5
- happy:       69/74 → 73/74

fixed: 8 条
broken: 0 条
净增: +8

建议：接受 v1。下一轮针对 multi-class 或 pii。

要继续吗？

[You] 继续，针对 multi-class

[Claude Code] ...
```

5-10 轮后到 88-92%——一晚上做完。

---

## 10. 注意事项

1. **始终 review LLM 改的 prompt**：可能改坏了关键约束
2. **每轮 git commit**：方便 rollback
3. **关键 prompt 别让 Agent 自动 push 到 production**
4. **Token cost 要监控**：每轮跑 evalset 都烧钱
5. **不要无限循环跑**：设 max_iter

---

## 11. demo：自动化 iter 脚本

```python
# demos/practice/03_auto_iter.py
"""手动用 Claude Code Skill 之外，也可以脚本化"""
import subprocess, json
from pathlib import Path
import anthropic


client = anthropic.Anthropic()


def iter_one_round(prompt_dir: Path, evalset: Path, current_results: Path):
    """让 Claude 改一版 prompt 然后跑 evalset"""
    # 拉数据
    current_prompt = (prompt_dir / "current.md").read_text()
    failures = json.loads(current_results.read_text())["failures"][:20]
    
    # 让 Claude 改 prompt
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system="""你是 PE 优化助手。看失败案例，提议改进 prompt v_{n+1}。

规则:
- 一次只改一处
- 不改 evalset
- 输出 JSON: {"new_prompt": "...", "rationale": "...", "expected_fix": ["..."]}
""",
        messages=[{
            "role": "user",
            "content": f"""当前 prompt:
{current_prompt}

失败案例 (top 20):
{json.dumps(failures, ensure_ascii=False, indent=2)}

请改一处。"""
        }],
    )
    
    result = json.loads(resp.content[0].text)
    
    # 写新版
    new_version = f"v{count_versions(prompt_dir) + 1}.md"
    (prompt_dir / new_version).write_text(result["new_prompt"])
    
    # 跑 evalset
    subprocess.run(["python", "eval_runner.py", str(prompt_dir / new_version), str(evalset)])
    
    return result


if __name__ == "__main__":
    result = iter_one_round(
        Path("prompts/"),
        Path("evalset/v1.0.jsonl"),
        Path("results/current.json"),
    )
    print(f"改动理由: {result['rationale']}")
    print(f"预期修复: {result['expected_fix']}")
```

---

## 12. 全本手册总结

44 篇覆盖完：

| 章 | 篇数 |
|---|------|
| 01-foundations | 5 |
| 02-process（中轴线） | 6 |
| 03-techniques | 10 |
| 04-advanced | 6 |
| 05-by-task | 5 |
| 06-models | 4 |
| 07-production | 5 |
| 08-practice（本章结尾） | 3 |
| **合计** | **44** |

走完这本手册你应该具备：

- 能澄清模糊需求，写 Spec
- 能建 evalset + 评测器
- 能按"一次改一处"严格迭代
- 知道什么时候停
- 会用三家 API 的特性
- 会做 caching / versioning / observability
- 能让 LLM 协作迭代 prompt

去做点真事吧。

---

## 13. 下一步

- 跟着 [08-practice/01-build-classifier.md](./01-build-classifier.md) 做一个自己的分类器
- 在公司里推广 PE 工程纪律
- 写自己的 PE 工具 / Skill
- 反哺：把你踩的坑也分享出来

## 参考资料

- 全本手册入口：[../../README.md](../../README.md)
- 跨手册关联：[LangSmith](../../../01-langchain/docs/02-langsmith/) · [Pydantic Evals](../../../02-pydantic-ai/docs/04-modules/02-evals.md) · [MCP](../../../03-mcp/README.md)
