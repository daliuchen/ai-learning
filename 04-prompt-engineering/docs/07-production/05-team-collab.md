# PE Production 05：团队协作 —— 谁能改 Prompt，谁来 Review

> **一句话**：Prompt 不只是工程师的事——PM 想改文案、运营想试 A/B、内容团队要风格统一。本篇给一套**多角色协作机制**：分工、review 流程、权限、审计。

---

## 1. 团队中的 PE 角色

| 角色 | 职责 |
|------|------|
| **Prompt Engineer** | 主负责人，设计 prompt + evalset + 迭代 |
| **PM / 产品** | 提需求、定 acceptance 标准、做最终业务决策 |
| **内容 / 文案** | 把控 voice / style |
| **数据 / ML** | 评测 / 漂移 / 模型选型 |
| **SRE / 运维** | 监控告警、上线流程 |
| **法务 / 合规** | safety、refusal 边界 |

不一定每个公司都齐——但**至少 Prompt Engineer + PM 要明确**。

---

## 2. 协作流程

```
[PM 提需求]
   ↓ 7 问澄清
[PE 写 Spec]
   ↓
[PE + 内容 review Spec]
   ↓
[PE 起 v0 + evalset]
   ↓ 评测
[PE 迭代到达标]
   ↓ PR review
[PM + 内容 + PE 三方批准]
   ↓ 灰度上线
[SRE 监控漂移]
   ↓ 反哺
[每周 PE + PM 例会复盘]
```

---

## 3. PR 模板

`.github/PULL_REQUEST_TEMPLATE/prompt_change.md`：

```markdown
## 改动概述
（一句话）

## 改了什么
- prompt 文件: prompts/X/vN.md
- evalset 是否变更: yes/no

## Why
（业务原因 / 失败案例 link）

## 评测结果
- prompt vN-1: 通过率 XX%
- prompt vN:   通过率 YY%
- fixed: [...]
- broken: [...]
- 净增: ±N

## 截图 / Trace 对比
（贴 LangSmith 链接）

## Review checkboxes
- [ ] CHANGELOG 已更新
- [ ] CI 评测通过
- [ ] 通过率不降反升（或有合理理由）
- [ ] 至少 2 人 review
- [ ] PM 批准业务影响（如适用）
```

---

## 4. 权限分层

| 权限 | 谁有 |
|------|------|
| **读 prompt** | 所有 dev |
| **写 dev prompt** | PE 团队 |
| **写 staging prompt** | PE + senior eng review |
| **写 production prompt** | PE + PM + 2 人 review |
| **rollback** | SRE + PE |
| **改 evalset** | PE + 数据团队 |
| **加 attack samples** | PE + security 团队 |

实现：
- Git 用 CODEOWNERS 控制
- LangSmith Hub 有 role
- 自建 DB 用 RBAC

---

## 5. 协作工具栈

### 5.1 PM 友好的"Prompt 试验台"

PM 不写 Python——给他个 UI（Promptfoo / LangSmith Playground / 自建）：

- 改 prompt 即时看输出
- 看 evalset 跑分
- 不直接上线，只是"提案"
- 提案变 PR 进 review 流程

### 5.2 内容团队的 voice guide

内容把 voice 写成文档：

```markdown
# 品牌 voice guide
- 用第二人称 "你"
- 避免 "我们"
- 短句优先
- ...
```

PE 把 voice guide 嵌进 system prompt 维持一致。

### 5.3 SRE 的告警 runbook

```markdown
# Prompt v2.1.0 漂移告警 runbook

1. 检查 monitoring dashboard 看是否 sudden vs gradual
2. 看 trace 找 5 个最新 failure 例子
3. 如果 gradual → 评估是否要 evalset 反哺
4. 如果 sudden:
   - 检查上游数据（输入分布是否变）
   - 检查 model deprecate
   - 必要时 rollback
5. 5min 内决定: rollback / 修复 / 监控
```

---

## 6. 评测的"权威性"

谁的评测算数？

```
人工标注 ≥ LLM judge ≥ 规则评测
```

- 规则评测：CI 跑、快、便宜，但只看 hard constraints
- LLM judge：质量评测，但有 bias
- 人工标注：金标准，但慢 / 贵

**权威性约定**：

- 主观质量看人工抽样
- 客观字段看规则
- LLM judge 当**信号**不当**判决**

---

## 7. Schedule：例会节奏

| 例会 | 频率 | 议程 |
|------|------|------|
| **PE 内部站会** | 每日 | 当前迭代进展 |
| **PE + PM 同步** | 每周 | 业务方面反馈、新需求 |
| **质量复盘** | 每两周 | trace 分析、failure mode、evalset 更新 |
| **模型评审** | 每季度 | 模型选型 / 价格 / 性能 reassess |

---

## 8. 文档管理

```
docs/
├── prompts-handbook.md     # 团队 PE 规范
├── voice-guide.md          # 品牌 voice
├── eval-guide.md           # 评测怎么做
├── safety-guidelines.md    # 安全 / 合规
└── runbooks/
    ├── prompt-rollback.md
    └── drift-alert.md
```

新人入职第一周读完。

---

## 9. 一个反例：协作失败

```
[Day 1] PM 半夜想到点子 → 自己改了 production prompt → 上线
[Day 2] 全公司团队都不知道
[Day 3] 客服收到大量投诉
[Day 4] 调查发现是 PM 改的，但没人记得改了啥
[Day 5] 找不到老版本 prompt，连 git 都没记录
[周一] 修复 + 流程整顿
```

防止：
- production prompt 必须 PR + review
- prompt 修改触发自动通知（Slack 提醒）
- 版本化 + 历史不删

---

## 10. demo：PR 流程脚手架

```python
# scripts/check_prompt_pr.py
"""CI 用：检查 prompt PR 满足规范"""
import sys
import json
from pathlib import Path
import subprocess


def check():
    issues = []
    
    # 1. 检查 CHANGELOG 是否更新
    diff = subprocess.check_output(["git", "diff", "--name-only", "main"]).decode()
    if any(f.startswith("prompts/") and f.endswith(".md") for f in diff.splitlines()):
        if not any("CHANGELOG" in f for f in diff.splitlines()):
            issues.append("修改 prompt 但未更新 CHANGELOG")
    
    # 2. 检查评测结果存在
    if not Path("results/pr_results.json").exists():
        issues.append("未提供评测结果文件")
    else:
        results = json.loads(Path("results/pr_results.json").read_text())
        # 通过率不应该降
        if results["pass_rate"] < results["baseline_pass_rate"]:
            issues.append(f"通过率下降: {results['baseline_pass_rate']} -> {results['pass_rate']}")
        # broken 列表不应该非空
        if results.get("broken"):
            issues.append(f"破坏 {len(results['broken'])} 条 regression: {results['broken']}")
    
    if issues:
        print("PR 检查失败:")
        for i in issues:
            print(f"  ❌ {i}")
        sys.exit(1)
    print("✅ PR 检查通过")


if __name__ == "__main__":
    check()
```

接入 CI workflow。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **PM 直接改 prompt** | 流程化，要 PR |
| **无 voice guide** | 风格漂 |
| **review 只有 PE 自己** | 加 PM / 内容 review |
| **rollback 无 runbook** | 出事手忙脚乱 |
| **改 prompt 没通知团队** | Slack 自动 notify |
| **PE 单点维护** | 至少 2 人 own prompts/ |

---

## 12. 07-production 章总结

| 篇 | 主题 |
|---|------|
| 01 | Versioning |
| 02 | Caching |
| 03 | Templating |
| 04 | A/B & Observability |
| 05 | Team Collaboration（本篇） |

---

## 13. 下一步

- 📖 实战项目 → [08-practice/](../08-practice/)
- 📖 评测先于 prompt → [01-foundations/05-eval-first.md](../01-foundations/05-eval-first.md)

## 参考资料

- LangSmith team collaboration: https://docs.smith.langchain.com
- "Building LLM Teams": various blog posts
