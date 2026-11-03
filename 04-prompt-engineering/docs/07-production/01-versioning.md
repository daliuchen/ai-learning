# PE Production 01：Prompt 版本管理

> prompt 应该和代码一样对待：进 git，有 changelog，能 diff，能回滚。这篇比较三种版本管理方案（文件 + git、LangSmith Hub、自建服务），再讲怎么把 prompt 改动接进 CI/CD。

---

## 1. 为什么 prompt 必须版本化

```
[周一 10:00] 工程师 A 改了 prompt，上线，效果好
[周三 14:00] 工程师 B 又改了 prompt，没看 A 的 commit
[周四 09:00] 用户投诉，效果暴跌
[周四 11:00] "谁能告诉我现在线上跑的是哪个版本？" 一片寂静
```

不做版本管理，会遇到这些问题：

- 改坏了没法回滚
- 多人同时改，互相覆盖
- 不知道线上跑的是哪一版
- 看不到改动历史
- 评测集的跑分对不上 prompt 版本

---

## 2. 方案 A：文件 + Git

这是最简单的方案。

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

切版本只要改 `current.txt` 里的一行。

CHANGELOG.md 大概长这样：

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

优点是不需要任何额外工具，PR review 也方便。缺点是跨语言的项目不好共享，也没有可视化的编辑界面。

---

## 3. 方案 B：LangSmith Prompt Hub

LangSmith 提供集中式的 prompt 管理：

```python
from langsmith import Client

client = Client()

# 拉 prompt：按 commit hash 钉死某个版本
prompt = client.pull_prompt("my-classifier:abc12345")

# 推新版本
client.push_prompt("my-classifier", object=prompt_object, commit_description="加 reasoning")

# 用 tag
production_prompt = client.pull_prompt("my-classifier:production")  # production tag 指向某个 commit
```

优点是有 UI 可以编辑，跨语言可用，和 LangSmith 的评测打通。缺点是依赖外部服务和网络。

详见 [LangSmith 04：Prompt Hub 与 Playground](../../../01-langchain/docs/02-langsmith/04-prompt-hub.md)。

---

## 4. 方案 C：自建数据库 / API

规模大的公司常常自己搭：

```
[Admin UI] → [PostgreSQL: prompts table] → [App fetches via API]
```

表结构：

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

适合这些情况：多个团队、多个产品共用 prompt；PM、内容运营这些非工程师也要改 prompt；需要权限控制和审计。

---

## 5. 用 SemVer 给 prompt 编号

```
v1.2.3
 │ │ └─ patch: 小调整（措辞 / typo）
 │ └─── minor: 加功能 / 新示例
 └───── major: schema 或行为大改
```

什么改动升哪一位：

| 改动 | 版本 |
|------|------|
| 调措辞 / 改约束 | patch |
| 加 few-shot 例子 / 加字段 | minor |
| 改输出 schema / role | major |
| evalset 大改 | major |

---

## 6. prompt 和评测集绑定

每个 prompt 版本对应一个评测集版本：

```yaml
# prompts/feedback-classifier/v1.0.0.meta.yaml
prompt: v1.0.0
evalset_version: v2.0
results:
  total_pass_rate: 0.92
  by_tag:
    happy: 30/30
    edge: 11/15
    regression: 8/8
deployed_at: 2026-05-20T10:00:00Z
deployed_by: alice
```

每次跑评测都要记下这几项：

- prompt 版本
- 评测集版本
- 模型版本，要记带日期的具体 snapshot 名，而不是会随时间指向新版本的别名
- 评测时间
- 通过率分布

---

## 7. 在 CI 里跑评测

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

PR 一改 prompt 就自动跑评测集，不达标就不让合并。

---

## 8. 灰度发布

新版本不要一上来就全量切换：

```python
import hashlib

def get_prompt_version(user_id: str) -> str:
    # 5% 用 v1.0.0，95% 用 v0.9.0
    # 别用内置 hash()：字符串的 hash 每个进程随机加盐，同一用户换个进程就可能分到另一组
    bucket = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100
    if bucket < 5:
        return "v1.0.0"
    return "v0.9.0"
```

两个版本并行时盯这几个指标：

- 通过率（有 ground truth 的话）
- 用户反馈和申诉
- 延迟和成本
- 错误率

没问题再逐步放量到 50%、100%。

---

## 9. 回滚

发现新版本有问题时：

```bash
echo "v0.9.0" > prompts/feedback-classifier/current.txt
git commit -am "rollback to v0.9.0 due to ..."
```

用 LangSmith 的话，把 `production` tag 指回老版本的 commit。

自建数据库的话，把老版本的 `is_current` 改回 TRUE。

**回滚必须能在秒级完成。** 如果切个版本还要发版、重启服务，那是设计出了问题。

---

## 10. 完整 demo

```python
"""文件 + git 风格 prompt loader"""
from pathlib import Path

import yaml  # pip install pyyaml


PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptStore:
    def __init__(self, base: Path = PROMPTS_DIR):
        self.base = base
    
    def load(self, name: str, version: str | None = None) -> dict:
        family = self.base / name
        if version is None:
            version = (family / "current.txt").read_text().strip()
        content = (family / f"{version}.md").read_text()
        meta_file = family / f"{version}.meta.yaml"   # 第 6 节的元数据文件
        meta = yaml.safe_load(meta_file.read_text()) if meta_file.exists() else {}
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

## 11. 团队约定

| 约定 | 含义 |
|------|------|
| 改 prompt 的 PR 必须同时改 CHANGELOG | 不写就打回 |
| 改 prompt 的 PR 必须跑评测集 | 由 CI 把关 |
| 改 prompt 要关联 issue 或需求文档 | 不能只凭"我觉得这样更好" |
| 重大改动至少 2 人 review | 避免一个人说了算 |
| prompt 改动和代码改动分开提 PR | 方便各自评测 |
| prompt 历史版本不删 | 版本废弃了也留着 |

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| prompt 写死在代码里 | 每次改都要发版 |
| prompt 没有版本号 | 不知道线上是哪一版 |
| 改 prompt 不跑评测 | 改坏了也不知道 |
| 不写 CHANGELOG | 三个月后回头看完全想不起来 |
| 回滚要重启服务 | 太慢 |
| prompt 没和评测集绑定 | 评测结果说明不了问题 |
| 多人改 prompt 没有锁 | 互相覆盖 |

---

## 13. 下一步

- [Prompt Caching 实战](./02-caching.md)
- [Prompt Templating（Jinja 与框架内置模板）](./03-templating.md)
- [A/B 与可观测](./04-ab-observability.md)

## 参考资料

- LangSmith Prompt Hub: https://docs.smith.langchain.com/prompt_engineering
- Promptfoo: https://www.promptfoo.dev
