# Pydantic AI 02：安装、依赖与环境

> 安装只有两种选择：`pydantic-ai` 主包装上就能用，自带 OpenAI / Anthropic / Google 三家 provider 和少量集成；想精简或者要别家 provider，就用 `pydantic-ai-slim` 加 `[extras]` 按需取。V2 起主包变小了，Groq、Mistral、Bedrock 这些要自己加 extra。

以 `pydantic-ai 2.64.0` 为准，要求 Python 3.11+。

---

## 1. 三种安装方式

### 1.1 主包（最省事）

```bash
pip install pydantic-ai
# 或
uv add pydantic-ai
```

这个包等价于 `pydantic-ai-slim[anthropic,cli,evals,google,logfire,mcp,openai,web]`（以 2.64.0 的元数据为准），也就是：

- 三家 provider：OpenAI、Anthropic、Google
- `cli`（`clai` 命令依赖）、`mcp`（MCP 客户端，基于 FastMCP）、`evals`、`logfire`、`web`（内置网页聊天 UI）

学习、写 demo、对比多家模型时用它就行。如果在意镜像体积或 Serverless 冷启动，或者只用一家 provider，就别装主包。

> V1 → V2 的变化：V1 的 `pydantic-ai` 主包把所有 provider 都装上；V2 只带上面三家。如果你的代码用 `groq:` / `mistral:` / `bedrock:` 等字符串，升级后要自己加 extra，例如 `pip install "pydantic-ai[groq]"`。

### 1.2 精简版（生产推荐）

只装你要用的 provider：

```bash
pip install "pydantic-ai-slim[openai]"
# 多 provider + 可观测
pip install "pydantic-ai-slim[openai,anthropic,logfire]"
```

镜像和包体积能小很多，import 也更快。本手册的 demo 只需要 `pydantic-ai-slim[openai]`（外加各章用到的少量 extra）。

### 1.3 用 uv（推荐）

```bash
uv add "pydantic-ai-slim[openai]"
```

`uv` 比 `pip` 快得多，Pydantic 官方文档的示例也用它。

---

## 2. extras 列表（节选）

`pydantic-ai-slim` 的可选组，按用途分类（完整列表见 `pip show` 或官方安装页）：

| 类别 | extra | 内容 / 何时用 |
|------|-------|----------------|
| Provider | `openai` `anthropic` `google` | 三家前沿 provider（主包默认带） |
| Provider | `bedrock` `groq` `mistral` `cohere` `huggingface` `openrouter` `xai` `cerebras` `voyageai` `snowflake` `zai` ... | 对应 provider SDK |
| 协议 / 界面 | `mcp` | MCP 客户端（FastMCP 4） |
| 协议 / 界面 | `ag-ui` `ui` `web` | AG-UI / Vercel AI 适配、内置网页聊天 UI |
| 观测 / 评测 | `logfire` `evals` | Logfire；`pydantic-evals` |
| 持久执行 | `temporal` `dbos` `prefect` | 持久执行引擎集成 |
| 工具 / 集成 | `duckduckgo` `tavily` `exa` `web-fetch` `retries` | 常用搜索工具、抓取、tenacity 重试传输 |
| 其它 | `cli` `realtime` `sentence-transformers` `spec` | CLI 依赖、实时语音、本地 embedding、Agent 配置文件 |

可以组合着装：

```bash
pip install "pydantic-ai-slim[openai,anthropic,logfire,mcp,retries]"
```

---

## 3. 配套独立包

下面这些是独立的 pip 包（部分也能以 extra 的形式带上）：

```bash
pip install pydantic-evals            # 评测（与 pydantic-ai 同版本号）
pip install pydantic-graph            # 状态机（pydantic-ai 已依赖它；也可单独用）
pip install pydantic-ai-harness       # 官方 capability / harness 库（0.x，独立发版）
pip install "pydantic-ai-harness[coder,skills]"   # harness 的各个能力有自己的 extra
pip install logfire                   # 可观测
pip install clai                      # 命令行（clai 现在是独立的包）
pip install fastmcp                   # 自己写 MCP server 时用（FastMCP 4）
```

拆成独立包，一是它们可以脱离 pydantic-ai 单独用，二是 harness 这类变化快的东西需要自己的发版节奏。比如你用 LangChain 但想接 Logfire，直接 `pip install logfire` 就行。

`fastmcp` 是第三方包，后面写 MCP server 的例子都用 `from fastmcp import FastMCP`。官方 SDK 2.x 里对应的是 `mcp.server.MCPServer`，见 MCP 手册的[写第一个 MCP Server 和 Client](../../../03-mcp/docs/01-basics/06-first-server.md)。

---

## 4. Python 版本要求

| 项 | 要求 |
|----|------|
| Python | >= 3.11（V2；V1 时代是 3.9/3.10） |
| Pydantic | 2.x 最新（本手册用 2.14） |
| OS | Linux / macOS / Windows / WSL |

```python
# 检查
import sys
assert sys.version_info >= (3, 11), "Pydantic AI V2 需要 Python 3.11+"
```

---

## 5. 验证安装

最短验证脚本（不需要 API key）：

```python
# verify.py
from importlib.metadata import version
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

print("pydantic-ai :", version("pydantic-ai"))   # 或 version("pydantic-ai-slim")

agent = Agent(TestModel())          # 等价于 Agent("test")
result = agent.run_sync("ping")
print("TestModel reply :", result.output)
```

能跑通就说明环境没问题。几点说明：

- `TestModel()` 是内置 mock，不会真的调用网络；`Agent('test')` 是它的简写
- `result.output` 是 `TestModel` 编造的字符串，只用来走通流程
- 第一次运行时终端可能打印一个带 ASCII 图案的横幅（提示接入 Logfire）；设 `PYDANTIC_AI_NO_BANNER=1` 可关掉
- 读包版本用 `importlib.metadata.version(...)`，比 `module.__version__` 可靠

---

## 6. 环境变量与 .env

Pydantic AI 走标准 SDK 的环境变量：

| 环境变量 | 对应 Provider |
|---------|---------------|
| `OPENAI_API_KEY`（+ 可选 `OPENAI_BASE_URL`） | OpenAI / 任何 OpenAI 兼容端点（千问、DeepSeek……） |
| `ANTHROPIC_API_KEY` | Anthropic |
| `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | Google GLA |
| `GROQ_API_KEY` | Groq |
| `MISTRAL_API_KEY` | Mistral |
| `CO_API_KEY` | Cohere |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | Bedrock |
| `HF_TOKEN` | Hugging Face |
| `LOGFIRE_TOKEN` | Logfire |

本手册的 demo 用千问。阿里云百炼提供 OpenAI 兼容端点，只需要这两个变量：

```ini
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

代码里写 `"openai-chat:qwen-flash"` 就行（`openai-chat:` = Chat Completions API；`openai:` 在 V2 默认走 Responses API，千问的兼容端点目前只保证 Chat Completions）。

建议用 `python-dotenv` 管理：

```bash
pip install python-dotenv
```

代码顶部：

```python
from dotenv import load_dotenv
load_dotenv()  # 在创建 Agent 之前调用

from pydantic_ai import Agent
agent = Agent("openai-chat:qwen-flash")
```

也可以显式传参，多账号切换或写单元测试时更清楚：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel(
    "qwen-flash",
    provider=OpenAIProvider(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key="sk-xxx"),
)
```

> 没有 key 也能练：`Agent("test")` 用的是内置的离线测试模型。另外，V2 里 `Agent("openai:...")` 在创建时就会检查 key（缺失会立刻抛 `UserError`）；想延迟到第一次运行再检查，传 `defer_model_check=True`。

---

## 7. 推荐的 requirements.txt

生产项目最小依赖：

```text
pydantic-ai-slim[openai,logfire]>=2.64.0
python-dotenv>=1.0.0
```

完整学习项目（见本手册根目录 `requirements.txt`）：

```text
pydantic-ai>=2.64.0
pydantic-evals>=2.64.0
pydantic-graph>=2.64.0
pydantic-ai-harness>=0.64.0
logfire>=4.0
fastmcp>=4.0
clai>=2.64.0
python-dotenv>=1.0
```

---

## 8. 升级与版本对齐

Pydantic AI 现在是 2.x（稳定版，有版本策略：主版本内保持兼容、弃用走警告），但 `pydantic-ai-harness` 仍是 0.x，小版本之间可能有不兼容改动。升级前后看一眼版本：

```bash
pip install --upgrade pydantic-ai
pip show pydantic-ai | grep Version
```

升级前先看 [CHANGELOG](https://github.com/pydantic/pydantic-ai/releases)。官方建议的路线是先升到最新的 1.x（≥ 1.100），把弃用警告都处理掉，再升 V2；V1 的消息历史在 V2 里仍然能反序列化。

老博客示例里常见的几种写法，升级后要改：

```python
# ❌ 0.0.x 时代
agent = Agent("openai:gpt-4o", result_type=Invoice)       # result_type 早已改名
result.data                                                # 早已改名为 result.output
result.usage()                                             # V2：usage 是属性

# ✅ 现在
agent = Agent("openai:gpt-6.1-sol", output_type=Invoice)
result.output
result.usage
```

另外 V2 默认行为的几处变化：

- 模型在同一次回复里既调了普通工具又交了最终输出时，默认策略从 `'early'` 改为 `'graceful'`：工具按模型发出的顺序执行，排在输出工具之前的普通工具现在会被执行（副作用会发生），以前是跳过；
- 不带类型参数的 `Agent(...)` 推断为 `Agent[object, str]`，不再是 `Agent[None, str]`；
- `RetryPromptPart` 被废弃，见 [07 篇](07-messages-history.md)。

---

## 9. 常见安装问题

| 现象 | 原因 | 解法 |
|------|------|------|
| `ImportError: cannot import name 'Agent' from 'pydantic_ai'` | 装成了 `pydantic_ai_xxx` 三方包 | `pip install pydantic-ai`（中划线） |
| `UserError: Set the OPENAI_API_KEY ...` | 没加载 .env / V2 在创建 Agent 时就检查 key | `load_dotenv()` 放在最顶部；或 `defer_model_check=True`；或先用 `Agent('test')` |
| `ImportError: ... groq`（用 `groq:` 字符串时） | V2 主包不再带 Groq 等 provider | `pip install "pydantic-ai[groq]"` |
| 找不到 `clai` 命令 | `clai` 现在是独立的包 | `pip install clai`（或 `uvx clai`） |
| `ssl: certificate verify failed`（公司网） | 自签证书 | Pydantic AI 自己的请求用系统信任库验证证书；确认公司根证书装进了系统信任库，或设 `SSL_CERT_FILE` |
| `metadata-generation-failed: pydantic-core` | Python < 3.11 或 Rust 工具链缺失 | 升 Python；或 `pip install --upgrade pip wheel` |
| `httpx.RemoteProtocolError` / `ModelConnectionError` | 国内网络访问国外模型 | 走代理 / 用 OpenRouter / 用国产模型（千问等） |
| `from mcp.server.fastmcp import FastMCP` 报错 | `mcp` 2.x 把 `FastMCP` 改名为 `MCPServer` | 用 `from fastmcp import FastMCP`（FastMCP 4），或锁 `mcp<2` |

---

## 10. vs LangChain

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 主包是否包含所有 Provider | `pydantic-ai` 带三家 / `slim` 不带 | 全部拆 partner 包 |
| 拆包策略 | 一个 slim + extras | 几十个独立包 |
| 安装时要想的 | 要不要 slim、要哪些 extra | 要装哪几个 partner |
| 升级时要想的 | 主包与 evals / graph 同版本号，harness 独立 | 多个版本号要对齐 |

LangChain 是一个 partner 一个包，安装命令更长：

```bash
pip install langchain langchain-openai langchain-anthropic langgraph langsmith
```

Pydantic AI 统一用 `extras`：

```bash
pip install "pydantic-ai-slim[openai,anthropic,logfire]"
```

---

## 11. 本章 demo

完整可运行代码：[`demos/basics/02_installation.py`](../../demos/basics/02_installation.py)（打印各包版本 + TestModel ping + 真实模型 ping）

下一章：[Pydantic AI 03：第一个 Agent](03-first-agent.md)，把 Agent 拆开细看。
