# PE Production 01：Prompt 版本管理

> **一句话**：prompt 应该像代码一样进 git、有 changelog、能 diff、能 rollback。本篇讲三种版本管理方案——文件 + git、LangSmith Hub、自建——以及怎么把"prompt 改动"接入 CI/CD。

---

## 1. 为什么 Prompt 必须版本化

```
[周一 10:00] 工程师 A 改了 prompt，上线，效果好
[周三 14:00] 工程师 B 又改了 prompt，没看 A 的 commit
[周四 09:00] 用户投诉，效果暴跌
[周四 11:00] "谁能告诉我现在线上跑的是哪个版本？" 一片寂静
```

不版本化的代价：

- 改坏了无法回滚
- 多人改 → 互相 override
- 不知道线上跑的是哪一版
- 看不到改动历史
- evalset 跑分对不上版本

---

## 2. 方案 A：文件 + Git（最简单）

```
your-repo/
├── prompts/
│   ├── feedback-classifier/
│   │   ├── v0.1.0.md
│   │   ├── v0.2.0.md
│   │   ├── v1.0.0.md
│   │   ├── current.txt        # 单行: v1.0.0
│   │   └── CHANGELOG.md
│   └── summarizer/
│       └── ...
├── evalset/
│   └── ...
└── src/
    └── prompt_loader.py
```

`prompt_loader.py`：

```python
from pathlib import Path

def load(name: str, version: str | None = None) -> str:
    base = Path("prompts") / name
    if version is None:
        version = (base / "current.txt").read_text().strip()
    return (base / f"{version}.md").read_text()


SYSTEM = load("feedback-classifier")
```

切版本：改 `current.txt` 一行就好。

CHANGELOG.md 长这样：

```markdown
# feedback-classifier CHANGELOG

## v1.0.0 (2026-05-20)
- 加 reasoning 字段
- evalset: v2.0
- 通过率: 92%
- 通过 evaluator: regression / happy / edge 全过

## v0.2.0 (2026-05-15)
- 加反讽处理示例
- evalset: v1.5
- 通过率: 88%

## v0.1.0 (2026-05-10) 初版
- 基础分类
- 通过率: 78%
```

**优点**：零工具栈、PR review 友好。
**缺点**：跨语言项目麻烦、UI 编辑差。

---

## 3. 方案 B：LangSmith Prompt Hub

LangSmith 提供 prompt 集中管理：

```python
from langsmith import Client

client = Client()

# 拉 prompt
prompt = client.pull_prompt("my-classifier/v1.2.0")

# 推新版本
client.push_prompt("my-classifier", prompt_object, commit_message="加 reasoning")

# 用 alias
production_prompt = client.pull_prompt("my-classifier:production")  # alias 指向某版本
```

**优点**：UI 编辑、跨语言、和 LangSmith 评测一体。
**缺点**：依赖外部服务、网络依赖。

详 [01-langchain/02-langsmith/04-prompt-hub](../../../01-langchain/docs/02-langsmith/04-prompt-hub.md)。

---

## 4. 方案 C：数据库 / API（自建）

大公司常自建：

```
[Admin UI] → [PostgreSQL: prompts table] → [App fetches via API]
```

schema：

```sql
CREATE TABLE prompts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB,
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_current BOOLEAN DEFAULT FALSE,
    UNIQUE(name, version)
);
```

适合：
- 多团队、多产品共用
- 非工程师也要改 prompt（PM / 内容运营）
- 需要 RBAC / 审计

---

## 5. SemVer 风格的 prompt 版本

```
v1.2.3
 │ │ └─ patch: 小调整（措辞 / typo）
 │ └─── minor: 加功能 / 新示例
 └───── major: schema 或行为大改
```

什么时候 bump：

| 改动 | 版本 |
|------|------|
| 调措辞 / 改约束 | patch |
| 加 few-shot 例子 / 加字段 | minor |
| 改输出 schema / role | major |
| evalset 大改 | major |

---

## 6. Prompt + evalset 绑定

每个 prompt 版本绑定一个 evalset 版本：

```yaml
# prompts/feedback-classifier/v1.0.0.meta.yaml
prompt: v1.0.0
evalset_version: v2.0
results:
  total_pass_rate: 0.92
  by_tag:
    happy: 30/30
    edge: 12/15
    regression: 8/8
deployed_at: 2026-05-20T10:00:00Z
deployed_by: alice
```

跑评测时**永远**记下：

- prompt version
- evalset version
- 模型版本（"gpt-4o-2024-08-06"，不是 "gpt-4o"）
- 评测时间
- 通过率分布

---

## 7. CI 跑评测

`.github/workflows/prompt-eval.yml`：

```yaml
name: prompt eval

on:
  pull_request:
    paths:
      - 'prompts/**'
      - 'evalset/**'

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - name: Run prompt eval
        run: |
          for prompt in prompts/*/v_pr.md; do
            python eval_runner.py "$prompt" evalset/all.jsonl
          done
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Check pass rate
        run: |
          rate=$(jq .pass_rate results.json)
          if (( $(echo "$rate < 0.85" | bc -l) )); then
            echo "Pass rate $rate < 85%, blocking merge"
            exit 1
          fi
```

PR 改 prompt → 自动跑 evalset → 不达标阻止 merge。

---

## 8. 灰度发布

不要一上来 100% 切：

```python
def get_prompt_version(user_id: str) -> str:
    # 5% 用 v1.0.0，95% 用 v0.9.0
    if hash(user_id) % 100 < 5:
        return "v1.0.0"
    return "v0.9.0"
```

监控两个版本的：
- 通过率（如果有 ground truth）
- 用户感知（看反馈 / dispute）
- 延迟 / cost
- 错误率

OK 后逐步扩 → 50% → 100%。

---

## 9. Rollback

发现新版本有问题：

```bash
echo "v0.9.0" > prompts/feedback-classifier/current.txt
git commit -am "rollback to v0.9.0 due to ..."
```

LangSmith：改 `production` alias 指回老版本。

自建 DB：UPDATE `is_current=TRUE` 到老版本。

**关键**：rollback 必须**秒级**。如果切版本要发版、要重启服务——是设计问题。

---

## 10. 完整 demo

```python
# demos/production/01_prompt_loader.py
"""文件 + git 风格 prompt loader"""
from pathlib import Path
import json


PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptStore:
    def __init__(self, base: Path = PROMPTS_DIR):
        self.base = base
    
    def load(self, name: str, version: str | None = None) -> dict:
        family = self.base / name
        if version is None:
            version = (family / "current.txt").read_text().strip()
        content = (family / f"{version}.md").read_text()
        meta = json.loads((family / f"{version}.meta.json").read_text()) \
            if (family / f"{version}.meta.json").exists() else {}
        return {
            "name": name,
            "version": version,
            "content": content,
            "meta": meta,
        }
    
    def list_versions(self, name: str) -> list[str]:
        family = self.base / name
        return sorted(p.stem for p in family.glob("v*.md"))


if __name__ == "__main__":
    store = PromptStore()
    prompt = store.load("feedback-classifier")
    print(f"loaded {prompt['name']} {prompt['version']}")
    print(f"content (first 200 chars): {prompt['content'][:200]}")
```

---

## 11. 团队协作约定

| 约定 | 含义 |
|------|------|
| **PR 改 prompt 必须改 CHANGELOG** | 不写就打回 |
| **PR 改 prompt 必须跑 evalset** | CI 把关 |
| **改 prompt 必须有 issue / PRD link** | 不能"我觉得这样更好" |
| **重大改动 review 2+ 人** | 防止 single-point |
| **prompt 改和代码改分 PR** | 各自评测 |
| **prompt 历史不删** | 即使版本废弃也留着 |

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **prompt 写死在代码** | 每次改都发版 |
| **prompt 不带版本号** | 不知道线上哪版 |
| **改 prompt 不跑评测** | 改坏不知道 |
| **不写 CHANGELOG** | 三个月后回来抓瞎 |
| **rollback 要重启服务** | 太慢 |
| **prompt 没和 evalset 绑定** | 评测结果无意义 |
| **多人改 prompt 没 lock** | 互相 override |

---

## 13. 下一步

- 📖 Prompt Caching → [02-caching.md](./02-caching.md)
- 📖 Templating → [03-templating.md](./03-templating.md)
- 📖 A/B 与可观测 → [04-ab-observability.md](./04-ab-observability.md)

## 参考资料

- LangSmith Prompt Hub: https://docs.smith.langchain.com/prompt_engineering
- Promptfoo: https://www.promptfoo.dev
