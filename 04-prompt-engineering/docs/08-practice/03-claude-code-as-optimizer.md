# PE Practice 03：用 Claude Code 当 Prompt 优化器

> 前面讲的迭代闭环（看评测失败、提假设、改 prompt、重跑验证）步骤固定，很适合交给 Claude Code 这类编码 agent 去执行，你只负责 review 和拍板。这篇讲怎么给它布置任务、设哪些约束，以及哪些决定不能交给它。

---

## 1. 分工

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

---

## 2. 第一轮：让它熟悉项目

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

在这个目录下打开 Claude Code，第一轮这样交代：

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

Claude Code 会按这个步骤往下走。

---

## 3. 给它立规矩

能不能用好，取决于你有没有把迭代纪律写清楚：

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

不加约束的话，它倾向于一次改很多地方。

---

## 4. 多轮迭代

第二轮只需要一句：

```
继续迭代。基于 v1 结果，做 v2。同样规则。
```

它会读 v1 的结果，找出新的最大失败类（v1 解决了反讽，接下来可能是多类别问题），改一处生成 v2，跑评测，再给报告。

一般跑 5-10 轮，通过率能到 88-92%。

---

## 5. 让它分析线上 trace

如果有 LangSmith 或 Langfuse 的线上 trace，也可以交给它看：

```
我把过去 7 天 100 条线上 trace export 到 traces/recent.jsonl。
请看一下：

1. 用户感知差的 case 分布在什么类别
2. 哪些 input 模式 evalset 没覆盖
3. 提议加 10 条到 evalset

只提议，不要直接写 evalset（我要 review）。
```

这时它同时在做 trace 分析和评测集补充，但评测集的最终写入要由你把关。

---

## 6. 让它给 prompt 减肥

```
现在 v8 通过率 91%，但 prompt 已经 1500 token + 5 个示例。
请：

1. 看哪些约束是"叠加但没必要"（早期某轮的修补，后来已被新示例覆盖）
2. 哪些示例是冗余的
3. 给一个"精简版 v8.1"，目标减 30% token，通过率不降

跑 evalset 确认。
```

一轮轮打补丁，prompt 只会越来越长，隔一段时间就该精简一次。

---

## 7. 不该交给它的事

| 事情 | 由谁决定 |
|--------|--------|
| 业务上多少分算够 | "91% 够不够"要你或 PM 来定 |
| 是否上线 | 灰度策略、风险评估 |
| 评测集的增删改 | 必须人工审核 |
| 换模型 | 涉及成本和合规 |
| 架构改造 | 拆子 agent、加 RAG 都是大决策 |
| 回滚 | 不要让 agent 自动回滚 |

这些事 Claude Code 可以给出分析和建议，决定要由人来做。

---

## 8. 做成 Skill

可以把一轮迭代封装成 Claude Code Skill，文件放在 `.claude/skills/pe-iter/SKILL.md`（frontmatter 必须在文件第一行）：

```markdown
---
name: pe-iter
description: 跑一轮 PE 迭代：看失败 → 改 prompt → 验证 → 报告。当用户说"迭代一下 prompt"或"run pe iter"时使用。
---

工作流：
1. 找 prompts/current.txt 确定当前版本
2. 读最新 results/v_n.json
3. 按 PE 工程纪律改一处生成 v_{n+1}
4. 跑 evalset
5. git commit
6. 报告给用户
```

之后在 Claude Code 里输入 `/pe-iter` 就能跑一轮。skill 的触发条件写在 `description` 里，不再用单独的 `triggers` 字段。

> 补充：Anthropic 官方的 `claude-api` skill 自带 `build-eval`（访谈式建评测集）和 `hillclimb`（在评测上按 train/validation/test 拆分迭代改 prompt）两个子命令，思路和本篇一致，可以直接拿来用。

---

## 9. 一段完整对话

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

这样跑 5-10 轮，通过率到 88-92%，一个晚上就能做完。

---

## 10. 注意事项

- 它改过的 prompt 一定要自己 review，可能会顺手改坏关键约束
- 每轮都 git commit，方便回滚
- 重要的 prompt 不要让 agent 自动推到生产环境
- 盯着 token 花费，每轮跑评测集都要钱
- 设好最大轮数，别让它无限循环

---

## 11. demo：用脚本自动迭代

```python
"""手动用 Claude Code Skill 之外，也可以脚本化"""
import subprocess, json
from pathlib import Path
import anthropic


client = anthropic.Anthropic()


def iter_one_round(prompt_dir: Path, evalset: Path, current_results: Path):
    """让 Claude 改一版 prompt 然后跑 evalset"""
    # 拉数据
    current_version = (prompt_dir / "current.txt").read_text().strip()   # 例如 "v3"
    current_prompt = (prompt_dir / f"{current_version}.md").read_text()
    failures = json.loads(current_results.read_text())["failures"][:20]
    
    # 让 Claude 改 prompt
    resp = client.messages.create(
        model="claude-sonnet-5-5",
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
    
    result = json.loads(resp.content[-1].text)
    
    # 写新版
    new_version = f"v{len(list(prompt_dir.glob('v*.md')))}.md"   # v0 起编号，已有 N 个就叫 vN
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

## 12. 全书回顾

全书 44 篇：

| 章 | 篇数 |
|---|------|
| 01-foundations | 5 |
| 02-process（主线） | 6 |
| 03-techniques | 10 |
| 04-advanced | 6 |
| 05-by-task | 5 |
| 06-models | 4 |
| 07-production | 5 |
| 08-practice（本章） | 3 |
| 合计 | 44 |

读完这本手册，你应该能：

- 把模糊的需求问清楚，写成 Spec
- 建评测集和评测器
- 严格按"一次只改一处"迭代
- 判断什么时候该停
- 用好三家 API 各自的特性
- 做缓存、版本管理和可观测
- 让 LLM 协助你迭代 prompt

---

## 13. 下一步

- 照着 [从需求到一个分类器（完整闭环）](./01-build-classifier.md) 做一个自己的分类器
- 在团队里推行这套迭代纪律
- 写自己的 PE 工具或 Skill
- 把你踩过的坑也写出来分享

## 参考资料

- 全书入口：[README](../../README.md)
- 其他手册：[LangSmith](../../../01-langchain/docs/02-langsmith/) · [Pydantic Evals](../../../02-pydantic-ai/docs/04-modules/02-evals.md) · [MCP](../../../03-mcp/README.md)
