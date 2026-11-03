# PE Production 05：Prompt 的团队协作与 Review 流程

> 产品上线以后，想动 prompt 的人往往不止工程师：PM 想改文案，运营想试个 A/B，内容团队要统一语气。人一多，没有规矩就容易出事故。这篇给一套多角色协作的做法，包括分工、review 流程、权限和审计。

---

## 1. 团队里的角色

| 角色 | 职责 |
|------|------|
| Prompt Engineer | 主负责人，设计 prompt、评测集，负责迭代 |
| PM / 产品 | 提需求、定验收标准、做最终的业务决策 |
| 内容 / 文案 | 把控语气和风格 |
| 数据 / ML | 评测、漂移监控、模型选型 |
| SRE / 运维 | 监控告警、上线流程 |
| 法务 / 合规 | 安全和拒答边界 |

不是每家公司都配得齐这些角色，但至少要明确谁是 Prompt Engineer、谁是 PM。

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
| 读 prompt | 所有开发 |
| 改 dev 环境的 prompt | PE 团队 |
| 改 staging 环境的 prompt | PE，加资深工程师 review |
| 改 production 环境的 prompt | PE + PM，再加 2 人 review |
| 回滚 | SRE + PE |
| 改评测集 | PE + 数据团队 |
| 加攻击样本 | PE + 安全团队 |

实现方式取决于你用什么管理 prompt：Git 用 CODEOWNERS，LangSmith Hub 有角色设置，自建数据库就做 RBAC。

---

## 5. 各角色的工具

### 5.1 给 PM 一个试验台

PM 一般不写 Python，给他一个界面就好（Promptfoo、LangSmith Playground 或自建）。在里面改 prompt 能马上看到输出，也能看评测集跑分，但改动不会直接上线，只算一个提案，提案转成 PR 后再走 review 流程。

### 5.2 内容团队的 voice guide

内容团队把品牌语气写成文档：

```markdown
# 品牌 voice guide
- 用第二人称 "你"
- 避免 "我们"
- 短句优先
- ...
```

PE 把它嵌进 system prompt，保证输出风格一致。

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

## 6. 谁的评测说了算

```
人工标注 ≥ LLM judge ≥ 规则评测
```

规则评测在 CI 里跑，快且便宜，但只能检查硬性约束。LLM judge 能评质量，但有偏差。人工标注是金标准，代价是慢和贵。

我们团队的约定是：主观质量看人工抽样，客观字段看规则，LLM judge 的结果只当参考信号，不当最终判决。

---

## 7. 例会节奏

| 例会 | 频率 | 议程 |
|------|------|------|
| PE 内部站会 | 每日 | 当前迭代进展 |
| PE + PM 同步 | 每周 | 业务反馈、新需求 |
| 质量复盘 | 每两周 | trace 分析、失败模式、评测集更新 |
| 模型评审 | 每季度 | 重新评估模型选型、价格、性能 |

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

## 9. 反例：一次协作事故

```
[Day 1] PM 半夜想到点子 → 自己改了 production prompt → 上线
[Day 2] 全公司团队都不知道
[Day 3] 客服收到大量投诉
[Day 4] 调查发现是 PM 改的，但没人记得改了什么
[Day 5] 找不到老版本 prompt，连 git 都没记录
[周一] 修复 + 流程整顿
```

要避免这种事，需要三条规矩：production 的 prompt 改动必须走 PR 和 review；prompt 一有修改就自动通知（比如发 Slack）；所有版本都留档，历史不删。

---

## 10. demo：PR 检查脚本

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

把它接进 CI workflow 即可。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| PM 直接改线上 prompt | 定好流程，必须走 PR |
| 没有 voice guide | 风格会漂 |
| review 只有 PE 自己 | 拉上 PM 和内容团队一起 review |
| 回滚没有 runbook | 出了事手忙脚乱 |
| 改 prompt 不通知团队 | 用 Slack 自动通知 |
| 只有一个 PE 在维护 | `prompts/` 目录至少要有 2 个 owner |

---

## 12. 本章回顾

| 篇 | 主题 |
|---|------|
| 01 | Versioning |
| 02 | Caching |
| 03 | Templating |
| 04 | A/B & Observability |
| 05 | Team Collaboration（本篇） |

---

## 13. 下一步

- 实战项目：[08-practice/](../08-practice/)
- [先建评测，再写 Prompt](../01-foundations/05-eval-first.md)

## 参考资料

- LangSmith: https://docs.smith.langchain.com
