# Pydantic AI 04-05：clai 命令行与 Harness 工具集

> 这一篇讲两样东西。`clai` 是 Pydantic AI 的命令行工具，不写代码就能和任意 LLM 对话、运行自己写的 Agent，或者开一个网页聊天界面。Harness（`pydantic-ai-harness`）是官方的 capability 库，外加几个现成的完整 agent（Coder、Researcher…），用来给 agent 配上工作区、技能、记忆、子 agent 和持久执行。

以 `pydantic-ai 2.64.0`、`clai 2.64.0`、`pydantic-ai-harness 0.64.0` 为准。`clai` 现在是独立的 pip 包（`pip install clai`）。

---

## 1. 为什么要 CLI

用代码写 Agent 很灵活，但有些场景写代码反而麻烦：

- 只想随手问 Claude 一个问题，连开 Python REPL 都嫌慢
- 想快速对比 OpenAI、Anthropic、Gemini 对同一个问题的回答
- 已经写好了 `agent = Agent(...)`，想直接试一下，不想专门再写一个 CLI 入口
- 在 SSH、容器或 CI 里，需要一个纯命令行的 LLM 交互方式

`clai` 覆盖了这些场景，用起来和 `httpie`、`gh` 这类命令行工具差不多，背后跑的是 Pydantic AI。

---

## 2. 安装

```bash
# 方式 A：用 pip（clai 是独立的包）
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

打印完答案就退出，适合写进 shell 脚本、Makefile 或 git hook：

```bash
# pre-commit.sh 里让 LLM 检查 commit message
clai "下面这段 commit message 有没有 typo？$(cat $1)"
```

### 3.3 切换模型

```bash
clai -m openai:gpt-6.1-sol
clai -m anthropic:claude-sonnet-5-5
clai -m google:gemini-3.8-flash
clai --list-models    # 看支持哪些
```

模型字符串和 Pydantic AI 里 `Agent("openai:gpt-6.1-sol")` 用的是同一种格式。

### 3.4 加载自己的 Agent

这是我用得最多的功能。假设你有：

```python
# my_agent.py
from pydantic_ai import Agent

agent = Agent(
    "openai-chat:qwen-flash",
    instructions="你是 SQL 助手，回答必须是合法 SQL。",
)
```

可以直接运行：

```bash
clai -a my_agent:agent "找出库存最少的 10 个商品"
# 或交互模式
clai -a my_agent:agent
```

`-a module:variable` 的格式和 `uvicorn` 一样。写好的 Agent 直接拿来用，不需要另写 CLI 入口。`-a` 也接受 YAML / JSON 格式的 agent spec 文件。

---

## 4. 交互模式内置命令

| 命令 | 作用 |
|------|------|
| `/exit` | 退出 |
| `/markdown` | 把上一条回复按 markdown 重新渲染 |
| `/multiline` | 进多行输入模式（`Ctrl+D` 提交） |
| `/cp` | 上一条回复复制到剪贴板 |
| `/usage` | 本次会话的累计用量（轮数 / 输入 / 输出 / 请求数 / 工具调用；加 `--json` 出单行 JSON） |

---

## 5. 常用 CLI flag 汇总

| flag | 含义 | 示例 |
|------|------|------|
| `-m, --model` | 指定模型 | `-m anthropic:claude-haiku-5-5` |
| `-a, --agent` | 加载自定义 Agent | `-a my_pkg.agents:billing_agent` |
| `-t, --code-theme` | 代码高亮主题 | `-t monokai` |
| `--no-stream` | 关闭流式 | `--no-stream` |
| `--mcp-config` | 加载 MCP 服务器配置（和 Claude Desktop / Claude Code 同格式） | `--mcp-config mcp.json` |
| `-l, --list-models` | 列出可用模型 | — |
| `--version` | 版本号 | — |
| `prompt`（位置参数） | 一次性问题 | `clai "hi"` |

---

## 6. Web UI 模式

```bash
clai web -m openai:gpt-6.1-sol
# 浏览器打开 http://127.0.0.1:7932
```

加载自己的 Agent、改监听地址：

```bash
clai web -a my_agent:agent \
  -i "你是 SQL 专家" \
  --host 0.0.0.0 --port 8080
```

`clai web` 的完整 flag（`-a/-m/-t/-i/--html-source/--host/--port/--allowed-host`）看 `clai web --help`。

在内网开一个，非技术同事就能直接在网页上和你的 Agent 聊天，不用写前端。不过官方把它定位成本地开发和调试工具，上生产请用 UI 事件流适配器（Vercel AI / AG-UI，见[Web Chat UI 与 UI 事件流](../05-patterns/02-web-chat-ui.md)）。代码里等价于 `agent.to_web()`，且审批类工具会渲染成批准 / 拒绝按钮（注意审批不是授权边界）。

---

## 7. 配置：API Key / 默认模型

`clai` 目前没有配置文件，全部走环境变量：

```bash
# .env 或 ~/.zshrc
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# 想长期默认用某个模型？给自己起个 alias
alias claude='clai -m anthropic:claude-sonnet-5-5'
alias gpt='clai -m openai:gpt-6.1-sol'
```

---

## 8. 从 Agent 反向启动 CLI

反过来，Agent 也可以直接启动一个 CLI：

```python
# main.py
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="...")
agent.to_cli_sync()           # 阻塞，进入 clai 风格交互
```

或者带历史消息：

```python
history = [...]  # 之前的对话
agent.to_cli_sync(message_history=history)
```

写完 Agent 后加这一行，`python main.py` 就能直接聊。

---

## 9. Harness 是什么

CLI 是给人用来和 LLM 交互的；Harness 则是让 agent 能长时间做复杂工作的那一层配套。`pydantic-ai-harness` 是 Pydantic AI 官方的 capability 库，也带了几个完整的 agent（独立的包，0.x 版本）。官方文档是这样介绍的：

> 每个 Pydantic AI agent 本来就有一层轻量的 harness（带类型的循环、任意模型、你自己的工具、结构化输出）。让 agent 去做复杂、长时间的工作（修一个代码库、研究一个问题、无人值守跑几个小时），它还需要：一个能动手的工作区、一份随时更新的计划、跨会话的记忆、能分派任务的子 agent、第十个小时还撑得住的上下文管理、扛得住重启的持久执行。harness 包提供这些。

harness 里的一切都是同一个原语：**capability**（见[Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)）。完整的 agent（Coder、Researcher）也只是若干 capability 的组合，官方的说法是"组装进去的方式就是拆出来的方式"。包里有 80 多个 capability。

```bash
pip install "pydantic-ai-harness[coder]"            # 各能力有自己的 extra：coder / skills / code-mode / dynamic-workflow / modal / e2b / ...
```

LangChain 对应物是 Deep Agents；对照关系见 §11。

---

## 10. Workspace 与 Coder：一行拿到一个编码 agent

### 10.1 Workspace：agent 动手的地方

工作区是 agent 读写文件、执行命令的环境，可以是本机的一个目录，也可以是云上的沙箱。它是核心包里的概念，工具通过 `ctx.workspace` 使用它，不需要关心它具体在哪：

```python
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import LocalWorkspace
from pydantic_ai.workspaces import WorkspaceError

agent = Agent("openai-chat:qwen-flash", capabilities=[LocalWorkspace("/tmp/workdir")])

@agent.tool
async def execute(ctx: RunContext, command: list[str]) -> str:
    """Run a command in the project directory."""
    try:
        result = await ctx.workspace.run(command, timeout=60)
    except WorkspaceError as error:
        raise ModelRetry(str(error))
    return result.stdout if result.exit_code == 0 else f"[exit {result.exit_code}] {result.stderr}"
```

换成沙箱只改一行（Modal、E2B、Sprites、Daytona、Docker、bubblewrap、SSH 等十来种），工具代码不用动。需要知道的几点：

- `LocalWorkspace` 不做任何隔离：命令以你的身份在你的机器上执行，你能访问的它都能访问（POSIX 系统可用）；
- 命令默认只继承 `PATH`、`HOME`、`LANG` 等少数环境变量。需要 token 就显式传 `env={...}`，不要直接传 `os.environ`，那样等于把 LLM 的 API key 也交给了模型执行的命令；
- 工作区比运行活得久：把消息历史传给下一次运行，它会在同一个工作区里继续；也可以 `reviewer.run(..., workspace=ReadOnlyWorkspace(result.workspace))` 交给另一个 agent 只读使用；
- 一次运行只有一个工作区。需要文件系统的 capability 不会替你选工作区，没挂的话运行一开始就报错，并告诉你该挂什么。

### 10.2 Coder

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import LocalWorkspace
from pydantic_ai_harness.coder import Coder

agent = Agent("anthropic:claude-opus-5-5", capabilities=[LocalWorkspace("."), Coder()])
result = agent.run_sync("Find out why tests/test_parser.py fails and fix the bug it caught.")
```

不写代码也能用：`uvx --with "pydantic-ai-harness[coder]" clai -a pydantic_ai_harness.coder:coder_agent -m <model>`（`coder_agent` 是上面这个组合去掉模型）。

模型拿到的工具：

| 工具 | 行为 |
|------|------|
| `read_file(path, offset, limit)` | 带行号读，默认 2000 行、最多 5 万字符，截断时告诉它下一段从哪开始 |
| `write_file(path, content)` | 新建或覆盖 |
| `edit_file(path, old_text, new_text)` | 精确替换，每处必须只匹配一次 |
| `list_files(path, glob)` | 按 `.gitignore` 列文件 |
| `grep(pattern, ...)` | ripgrep 搜索 |
| `shell(command, mode, timeout)` | 不受限的命令，可后台跑 |
| `delegate_task(agent_name, task)` | 把独立的子任务交给它自己的一次全新运行 |

`Coder()` 不是黑盒，它按顺序组合了 9 个零件：带默认指令的 `Capability`、`FileSystem`（五个文件工具）、`Shell`、`RepoContext`（自动加载仓库里的 `CLAUDE.md` / `AGENTS.md`）、`SubAgents(include_self=True)`（`delegate_task`）、`ClearToolResults(max_fraction=0.7)`（上下文占用高时清旧工具结果）、工具输出上限（6.4 万字符）、`RepairToolArguments`（坏 JSON 参数先修再校验）、提示词缓存。要改任何设置（比如给 shell 加命令白名单）就用零件重新组装。

给 Coder 一个"写 fib.py 打印前 10 项并运行确认"这样的任务，它会用 `write_file` 写文件、用 `shell` 运行。跑完后工作区里除了 `fib.py`，还会多出 harness 自己的 `.pydantic-ai-harness/` 状态目录。

文件工具默认限定在工作区目录内，`.git`、`.env`、密钥文件只读。但 shell 不受这些规则约束，能执行任意命令就能绕过文件规则（Deep Agents 的权限规则管不住 `execute`，也是同样的情况）。委派给自己时子运行从零开始，父 agent 要把需要的信息写进任务描述；`Coder` 必须绑在 `Agent(capabilities=[...])` 上，传给 `run()` 会报错。

### 10.3 Skills：建在按需 capability 上

```text
.agents/skills/
  code-review/
    SKILL.md
```

```python
from pydantic_ai_harness import Skills

agent = Agent("openai-chat:qwen-flash", capabilities=[LocalWorkspace("."), Skills(".agents/skills")])
```

每次运行开始时，`Skills` 从工作区读配置的技能库，扫描直接子目录里的 `SKILL.md`，把每个技能变成一个以它命名的按需 capability：模型一开始只看到名字和描述，需要时用 `load_capability` 加载正文。

几个使用约束：

- 它不会自动搜索 `.agents`、`.claude` 或家目录，要加载哪些库得明确传进去。传多个目录时要配 `missing_directories="skip"` / `duplicate_names="keep_first"`。
- `include` / `exclude` 不能当权限边界用。`SKILL.md` 正文会变成模型的指令，只加载你信任的来源。
- `Skills` 只加载 `SKILL.md`，不读、不执行 `scripts/` 等附带文件。模型有 `FileSystem` / `Shell` 时可以自己去读、去跑。
- `allowed-tools`、`model`、`hooks` 等行为类字段只接受、不实现。

### 10.4 其它常用 capability

| capability | 解决什么 |
|------------|---------|
| `Planning` | 防止长时间运行时跑偏：计划由模型通过工具维护，每轮作为临时提醒附在请求末尾（不破坏缓存） |
| `Memory` | 跨对话记忆：一本 Markdown 笔记，每次只注入有限摘录，以用户角色注入（降低权威性） |
| `InputGuardrail` / `OutputGuardrail` / `ToolGuardrail` | 在输入、每个工具调用及结果、最终输出三处跑你的检查 |
| 压缩策略 | 历史超窗口：清旧工具结果 / 按 token 摘要……且保证工具调用与结果成对 |
| `SpendLimits` | 跨运行、跨租户的花费上限（`UsageLimits` 只管一次运行） |
| `AskUser` | 任务有歧义时让模型问 1~10 个选择题 |
| `StepPersistence` | 每个工具调用结算后存快照，可续跑、分叉；自带内存 / 文件 / SQLite / PostgreSQL / MongoDB 后端，还会记录工具的副作用 |
| `SubAgents` / `CodeMode` / `DynamicWorkflow` | 多 agent，以及用代码代替工具调用（见[多 Agent 协作模式](../05-patterns/01-multi-agent.md)） |

---

## 11. CLI vs Harness 对照，以及与 Deep Agents

| 维度 | clai | Harness |
|------|------|---------|
| 谁在用 | 人 | agent |
| 输入 | 自然语言问题 | 一个任务 + 一个工作区 |
| 输出 | 回复 | 对工作区的实际改动 + 回复 |
| 配置在哪 | 命令行 flag / env | `capabilities=[...]` |
| 常见场景 | 日常问答、运行已有 Agent、`clai web` | 编码 / 研究 agent、长时间无人值守任务 |

和 LangChain 的 Deep Agents 对照：虚拟文件系统对应 `FileSystem`，`execute` 对应 `Shell`，`task` 对应 `delegate_task`，`AGENTS.md` 记忆对应 `RepoContext`，自动摘要对应 `ClearToolResults` / 压缩。Pydantic AI 的做法是核心包只放需要框架深度配合的部分，其余都放进 harness 包，而且每一块都能单独选用。

---

## 12. 实战例子：用 clai 解决真实任务

把日常的 git 操作接上 LLM：

```bash
# 1. 让 LLM 写 commit message
git diff --cached | clai "根据下面 diff 写一句简短中文 commit message"

# 2. 让 LLM review PR 描述
gh pr view 123 --json body | jq -r .body | clai "总结这个 PR 的关键变更"

# 3. 让 SQL Agent 直接出 SQL
clai -a my_agents:sql_agent "上个月 GMV top 10 商品"
```

CLI 最顺手的地方就在这里：把 LLM 当成一个 unix 工具，用管道串起来。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `clai: command not found` | pip 装了但路径没在 PATH | `which clai` 检查，或换 `uv tool install clai` |
| 没设 API key 报 401 | 模型对应的 env 没设 | 看错误里的 env 变量名（OPENAI_API_KEY / ANTHROPIC_API_KEY…） |
| `-a module:agent` 报 ImportError | 包路径错，或当前目录不对 | `pip install -e .` 或加 `PYTHONPATH=.` 启动 |
| 加载的 Agent 跑不出工具调用 | Agent 用了 deps 但 CLI 不知道怎么提供 | `clai` 简单场景适合无 deps 的 Agent，复杂 deps 自己写脚本 |
| 流式输出乱码 | 终端编码不是 UTF-8 | 设置 `LANG=en_US.UTF-8` 或加 `--no-stream` |
| markdown 不渲染 | 终端不支持 | `/markdown` 命令重渲；或开 `clai web` |
| `ModuleNotFoundError: pydantic_ai_harness.coder` 缺依赖 | harness 的各能力是 extra | `pip install "pydantic-ai-harness[coder]"` |
| 给 Coder 没挂工作区，运行一开始就报错 | 需要文件系统的 capability 从不替你选工作区 | `capabilities=[LocalWorkspace(dir), Coder()]` |
| Coder 的 shell 把仓库外的东西也改了 | `LocalWorkspace` 不是沙箱，shell 不受文件规则约束 | 生产换 Docker / E2B / Modal 等沙箱 capability |
| 想给 Web UI 加自定义 UI | 默认 HTML 不够 | `clai web --html-source path/to/index.html` |

---

## 14. 何时用 / 不用

| 场景 | clai | Harness |
|------|------|---------|
| 个人日常问 LLM 问题 | 适合 | — |
| Shell 脚本里用管道接 LLM | 适合 | — |
| 把内部 Agent 给同事用 | 适合，`clai web -a ...` | — |
| 要 Agent 出现在 web 产品里 | 不适合，用 FastAPI 自己包 | — |
| 做一个能改代码 / 跑命令的 agent | — | 适合，`LocalWorkspace + Coder()`（生产换沙箱） |
| 要长时间无人值守、可续跑的 agent | — | 适合，`StepPersistence` / 持久执行 + 压缩 + 计划 |
| 想让 IDE 里的 coding agent 熟悉 Pydantic AI | — | 官方有 *Coding Agent Skills*（安装 Pydantic AI skill），以及 `pydantic_ai_docs` capability |

---

## 15. 本章 demo

完整可运行代码：[`demos/modules/05_cli_harness.py`](../../demos/modules/05_cli_harness.py)

包含：

1. Demo A：定义一个 `agent = Agent(...)`，演示 `agent.to_cli_sync()` 直接进交互
2. Demo B：用 `subprocess` 调用 `clai -a 05_cli_harness:agent "..."` 加载这个 Agent，走一次性模式
3. Demo C：官方 harness 的 `Coder` + `LocalWorkspace`（临时目录）：让 agent 写 `fib.py` 并运行；没 key 时用 `TestModel` 演示工具注册
4. 没有 API Key 或没装 clai 时，全部回退到 `TestModel`，并提示怎样用真实模型运行

模块篇到这里就是全部 5 篇：

- [MCP（Model Context Protocol）集成](01-mcp.md)
- [Pydantic Evals 评测框架](02-evals.md)
- [Pydantic Graph 状态机](03-graph.md)
- [Logfire 可观测性集成](04-logfire.md)
- clai 命令行与 Harness 工具集（本篇）
