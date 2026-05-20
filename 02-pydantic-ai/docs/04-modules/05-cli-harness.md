# Pydantic AI 04-05：clai 命令行与 Harness 工具集

> **一句话**：`clai` 是 Pydantic AI 自带的命令行工具，让你不写一行代码就能跟任意 LLM 对话或运行自家 Agent；**Harness** 则是另一头玩法——把 Pydantic AI 当成给 LLM coding agent 用的"工具集"，让模型自己写 Pydantic AI 代码。

---

## 1. 为什么要 CLI

写代码做 Agent 当然爽，但有几个常见场景代码反而是累赘：

- "我就想拉 Claude 问一句"，开 Python REPL 都嫌慢
- 想快速对比 OpenAI / Anthropic / Gemini 同一个问题的回答
- 想把已经写好的 `agent = Agent(...)` 直接用起来，不想为了试一下再写一个 CLI 入口
- 在 SSH 上、容器里、CI 里需要一个**纯命令行**的 LLM 交互方式

`clai` 把这些场景一锅端：和 `httpie` / `gh` 一样的"程序员命令行体验"，背后就是 Pydantic AI。

---

## 2. 安装

```bash
# 方式 A：用 pip
pip install "pydantic-ai[cli]"
# 或者只装 CLI 包
pip install clai

# 方式 B：用 uv（不会污染全局 Python）
uv tool install clai

# 方式 C：一次性试一下不安装
uvx clai
```

验证安装：

```bash
clai --version
```

设置 API Key（按你想用的模型选一个）：

```bash
export OPENAI_API_KEY=sk-...
# 或
export ANTHROPIC_API_KEY=sk-ant-...
# 或
export GOOGLE_API_KEY=...
```

---

## 3. 三种用法：交互 / 一次性 / 加载 Agent

### 3.1 交互模式（默认）

```bash
clai
# 进入 prompt，输入问题回车，模型流式返回
```

退出按 `Ctrl+D` 或输入 `/exit`。

### 3.2 一次性模式（带 prompt）

```bash
clai "用一句话解释 Python 的 GIL"
```

直接打印答案就退出，**适合写进 shell 脚本 / Makefile / git hook**：

```bash
# pre-commit.sh 里让 LLM 检查 commit message
clai "下面这段 commit message 有没有 typo？$(cat $1)"
```

### 3.3 切换模型

```bash
clai -m openai:gpt-4o
clai -m anthropic:claude-3-5-sonnet-latest
clai -m google:gemini-1.5-flash
clai --list-models    # 看支持哪些
```

模型字符串就是 Pydantic AI 里 `Agent("openai:gpt-4o")` 用的同一种格式。

### 3.4 加载自己的 Agent

最有用的一个功能。假设你有：

```python
# my_agent.py
from pydantic_ai import Agent

agent = Agent(
    "openai:gpt-4o-mini",
    system_prompt="你是 SQL 助手，回答必须是合法 SQL。",
)
```

直接：

```bash
clai -a my_agent:agent "找出库存最少的 10 个商品"
# 或交互模式
clai -a my_agent:agent
```

`-a module:variable` 的格式跟 `uvicorn` 一模一样，**你已经写好的 Agent 直接复用，不需要再写 CLI 入口**。

---

## 4. 交互模式内置命令

| 命令 | 作用 |
|------|------|
| `/exit` | 退出 |
| `/markdown` | 把上一条回复按 markdown 重新渲染 |
| `/multiline` | 进多行输入模式（`Ctrl+D` 提交） |
| `/cp` | 上一条回复复制到剪贴板 |

---

## 5. 常用 CLI flag 汇总

| flag | 含义 | 示例 |
|------|------|------|
| `-m, --model` | 指定模型 | `-m anthropic:claude-3-5-haiku-latest` |
| `-a, --agent` | 加载自定义 Agent | `-a my_pkg.agents:billing_agent` |
| `-t, --code-theme` | 代码高亮主题 | `-t monokai` |
| `--no-stream` | 关闭流式 | `--no-stream` |
| `-l, --list-models` | 列出可用模型 | — |
| `--version` | 版本号 | — |
| `prompt`（位置参数） | 一次性问题 | `clai "hi"` |

---

## 6. Web UI 模式（隐藏神器）

```bash
clai web -m openai:gpt-4o-mini
# 浏览器打开 http://127.0.0.1:7932
```

更强的版本：

```bash
clai web -a my_agent:agent \
  --instructions "你是 SQL 专家" \
  --host 0.0.0.0 --port 8080
```

公司内网开一个、给非技术同事用作"和我们 Agent 聊天的 web 工具"——完全不用写前端。

---

## 7. 配置：API Key / 默认模型

`clai` 没有显式配置文件（截至当前版本），都走环境变量：

```bash
# .env 或 ~/.zshrc
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# 想长期默认用某个模型？给自己起个 alias
alias claude='clai -m anthropic:claude-3-5-sonnet-latest'
alias gpt='clai -m openai:gpt-4o'
```

---

## 8. 从 Agent 反向启动 CLI

不止 CLI 能加载 Agent，**Agent 也能直接进 CLI**：

```python
# main.py
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="...")
agent.to_cli_sync()           # 阻塞，进入 clai 风格交互
```

或者带历史消息：

```python
history = [...]  # 之前的对话
agent.to_cli_sync(message_history=history)
```

这两个 API 让"我写完 Agent 直接 `python main.py` 就能聊"成为一行代码的事。

---

## 9. Harness 是什么

CLI 是"人对 LLM"，Harness 是"LLM 对 LLM"。

具体说：**Pydantic AI Harness 是给 coding agent（Claude Code / Cursor / Cline 等）准备的一组"如何写 Pydantic AI 代码"的能力包**。让 LLM 自己读这些 capability，就能写出符合 Pydantic AI 风格的 Agent / tool / Graph，不需要你一句一句教。

可以理解成：

- 普通的 LLM 知道 "Python 怎么写"
- 加了 Harness 后的 LLM 知道 "**Pydantic AI 风格**怎么写"——包括 `output_type` 该怎么挑、`@agent.tool` vs `@agent.tool_plain` 怎么选、Graph 节点怎么连

官方在 README 里把它叫 "**capability library**"，你可以从里面挑现成的，也可以自己加。

> 注：Harness 的具体 API（如包名 / 文件位置）还在快速迭代，**请以 https://github.com/pydantic/pydantic-ai 当前版本的 README 为准**。

---

## 10. Harness 怎么用（典型工作流）

最常见的两个集成路径：

### 10.1 在 Claude Code / Cursor 里

1. 在项目根放一个 `CLAUDE.md` 或 `.cursorrules`
2. 把 Pydantic AI Harness 的内容（system prompt + 代码片段）塞进去
3. 之后让 coding agent 写代码，它会自动用 Pydantic AI 风格

### 10.2 自己 dogfood：用 Pydantic AI 写 coding agent

```python
from pydantic_ai import Agent

coder = Agent(
    "anthropic:claude-3-5-sonnet-latest",
    system_prompt=(
        "你是 Pydantic AI 资深开发者。用户提需求，你直接产出可运行的"
        "Pydantic AI 代码，符合以下规范：\n"
        "1. 用 Agent(...)，model 字符串\n"
        "2. 输出类型用 Pydantic Model\n"
        "3. 工具用 @agent.tool_plain（不依赖 ctx）或 @agent.tool（需要 ctx）\n"
        "..."
    ),
)
```

这就是最朴素的 "Pydantic AI Harness"：把规范塞进 system prompt，让 LLM 按这套规范写代码。生产环境你会再加：

- 工具：`read_file` / `write_file` / `run_pytest`
- 验证：每次写完 `subprocess.run(["python","-c","import ast; ast.parse(...)"])`
- 评测：用 pydantic-evals 跑一组"能写出 X 的"测试

---

## 11. CLI vs Harness 对照

| 维度 | clai | Harness |
|------|------|---------|
| 谁在用 | 人 | LLM |
| 输入 | 自然语言问题 | "请帮我写一段 Pydantic AI 代码" |
| 输出 | 回复 / 代码 | Pydantic AI 风格的源码 |
| 配置在哪 | 命令行 flag / env | system prompt / `CLAUDE.md` |
| 典型场景 | 日常问答、运行已有 Agent | 让 coding agent 自动写 Agent |

---

## 12. 实战例子：用 clai 解决真实任务

把日常 git workflow 都接上 LLM：

```bash
# 1. 让 LLM 写 commit message
git diff --cached | clai "根据下面 diff 写一句简短中文 commit message"

# 2. 让 LLM review PR 描述
gh pr view 123 --json body | jq -r .body | clai "总结这个 PR 的关键变更"

# 3. 让 SQL Agent 直接出 SQL
clai -a my_agents:sql_agent "上个月 GMV top 10 商品"
```

这就是 CLI 的杀手锏：**把 LLM 当 unix 工具用**，pipe 来 pipe 去。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `clai: command not found` | pip 装了但路径没在 PATH | `which clai` 检查，或换 `uv tool install clai` |
| 没设 API key 报 401 | 模型对应的 env 没设 | 看错误里的 env 变量名（OPENAI_API_KEY / ANTHROPIC_API_KEY…） |
| `-a module:agent` 报 ImportError | 包路径错或 cwd 不对 | `pip install -e .` 或加 `PYTHONPATH=.` 启动 |
| 加载的 Agent 跑不出工具调用 | Agent 用了 deps 但 CLI 不知道怎么提供 | `clai` 简单场景适合无 deps 的 Agent，复杂 deps 自己写脚本 |
| 流式输出乱码 | 终端编码不是 UTF-8 | 设置 `LANG=en_US.UTF-8` 或加 `--no-stream` |
| markdown 不渲染 | 终端不支持 | `/markdown` 命令重渲；或开 `clai web` |
| Harness 写的代码用了过时 API | LLM 训练截止前的 Pydantic AI | system prompt 里贴最新版本的 import / signature |
| Harness 容易写出错代码 | 缺少校验回路 | 给 coding agent 加 `run_python` / `mypy` 工具，让它自己 lint |
| 想给 Web UI 加自定义 UI | 默认 HTML 不够 | `clai web --html-source path/to/index.html` |

---

## 14. 何时用 / 不用

| 场景 | clai | Harness |
|------|------|---------|
| 个人日常拉 LLM 问问题 | ✅ | — |
| Shell 脚本里 pipe LLM | ✅ | — |
| 把内部 Agent 暴露给同事用 | ✅ `clai web -a ...` | — |
| 要 Agent 出现在 web 产品里 | ❌ 用 FastAPI 自己包 | — |
| 让 IDE 里的 coding agent 写 Pydantic AI | — | ✅ 把 Harness 塞 `CLAUDE.md` |
| 想自动生成 Agent / Graph 代码 | — | ✅ Harness + coding agent |

---

## 15. 本章 demo

完整可运行代码：[`demos/modules/05_cli_harness.py`](../../demos/modules/05_cli_harness.py)

包含：

1. **Demo A**：定义一个 `agent = Agent(...)`，演示 `agent.to_cli_sync()` 直接进交互
2. **Demo B**：用 `subprocess` 调用 `clai -a` 加载这个 Agent，验证一次性模式输出
3. **Demo C**：极简 Harness——用 Pydantic AI Agent 写一个"输入需求 → 产出 Pydantic AI 代码"的 coding agent
4. 没 API Key / 没装 clai 时，全部 fallback 到 `TestModel`，告诉你怎么真跑

跑通后这套"模块篇" 5 篇就完整了。回到目录：

- [01-mcp.md](01-mcp.md) — MCP 集成
- [02-evals.md](02-evals.md) — 评测
- [03-graph.md](03-graph.md) — 状态机
- [04-logfire.md](04-logfire.md) — 可观测性
- **05-cli-harness.md** — CLI & Harness（本篇）
