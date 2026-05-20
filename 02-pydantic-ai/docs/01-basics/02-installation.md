# Pydantic AI 02：安装、依赖与环境

> **一句话**：`pydantic-ai` 是元包，把所有 provider 全装一遍；想精简，用 `pydantic-ai-slim` + `[extras]` 按需取用。

---

## 1. 三种安装姿势

### 1.1 全家桶（最省事）

```bash
pip install pydantic-ai
# 或
uv add pydantic-ai
```

这个包**等价于** `pydantic-ai-slim[openai,anthropic,google,groq,mistral,cohere,bedrock,huggingface,logfire,mcp,evals,graph,...]`。

适合：

- 学习阶段、demo、对比多家模型
- 不在意几十 MB 的额外依赖

不适合：

- Docker 镜像要求 < 200MB
- Serverless / Lambda 冷启动敏感
- 明确只用某一家 Provider

### 1.2 精简版（生产推荐）

只装你要用的 Provider：

```bash
pip install "pydantic-ai-slim[openai]"
# 多 Provider
pip install "pydantic-ai-slim[openai,anthropic,logfire]"
```

镜像/包体积能瘦下来 **50% 以上**，且 import 时间更短。

### 1.3 用 uv（推荐）

```bash
uv add "pydantic-ai-slim[openai]"
```

`uv` 比 `pip` 快几十倍，Pydantic 团队官方文档也用它做示例。

---

## 2. 完整 extras 列表

`pydantic-ai-slim` 支持的所有可选组：

| extra | 装了啥 | 何时用 |
|-------|--------|--------|
| `openai` | openai SDK | OpenAI / OpenAI 兼容（DeepSeek / Together / OpenRouter） |
| `anthropic` | anthropic SDK | Claude 系列 |
| `google` | google-genai | Gemini API（GLA + Vertex） |
| `groq` | groq SDK | Groq 超快推理 |
| `mistral` | mistralai SDK | Mistral 模型 |
| `cohere` | cohere SDK | Cohere Command 系列 |
| `bedrock` | boto3 + aws | AWS Bedrock 上的各种模型 |
| `huggingface` | huggingface_hub | HF Inference / TGI |
| `logfire` | logfire | 可观测性 |
| `mcp` | mcp | Model Context Protocol 客户端/服务端 |
| `evals` | pydantic-evals | 评测框架 |
| `cli` | rich + prompt-toolkit | `pai` 命令行工具 |
| `vertexai` | google-cloud-aiplatform | Vertex AI |

组合用：

```bash
pip install "pydantic-ai-slim[openai,anthropic,google,logfire,mcp]"
```

---

## 3. 配套独立包

下面这些是**独立 pip 包**，不是 `pydantic-ai-slim` 的 extras：

```bash
pip install pydantic-evals     # 评测
pip install pydantic-graph     # 状态机
pip install logfire            # 可观测（其实 pydantic-ai 已带）
pip install mcp                # MCP 原生协议库
```

为什么独立？因为它们可以**不依赖 pydantic-ai 单独使用**。比如你用 LangChain 但想白嫖 Logfire，直接 `pip install logfire` 即可。

---

## 4. Python 版本要求

| 项 | 要求 |
|----|------|
| Python | **>= 3.10** |
| Pydantic | **>= 2.6** |
| OS | Linux / macOS / Windows / WSL |

⚠️ Python 3.9 不行（用了 `X \| Y` 类型语法、`match` 等 3.10 特性）。

```python
# 检查
import sys
assert sys.version_info >= (3, 10), "Pydantic AI 需要 Python 3.10+"
```

---

## 5. 验证安装

最短验证脚本（不需要 API key）：

```python
# verify.py
import pydantic
import pydantic_ai
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

print(f"pydantic        : {pydantic.VERSION}")
print(f"pydantic-ai     : {pydantic_ai.__version__}")

agent = Agent(TestModel())
result = agent.run_sync("ping")
print(f"TestModel reply : {result.output}")
```

跑通就说明环境 OK。注意：

- `TestModel()` 是内置 mock，不会真的调用网络
- `result.output` 是 `TestModel` 编造的字符串，仅用于打通流程

---

## 6. 环境变量与 .env

Pydantic AI 走标准 SDK 的环境变量：

| 环境变量 | 对应 Provider |
|---------|---------------|
| `OPENAI_API_KEY` | OpenAI / OpenAIChatModel |
| `ANTHROPIC_API_KEY` | Anthropic |
| `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | Google GLA |
| `GROQ_API_KEY` | Groq |
| `MISTRAL_API_KEY` | Mistral |
| `CO_API_KEY` | Cohere |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | Bedrock |
| `HF_TOKEN` | Hugging Face |
| `LOGFIRE_TOKEN` | Logfire |

推荐用 `python-dotenv` 管理：

```bash
pip install python-dotenv
```

`.env`：

```ini
OPENAI_API_KEY=sk-xxxx
ANTHROPIC_API_KEY=sk-ant-xxxx
LOGFIRE_TOKEN=lf_xxxx
```

代码顶部：

```python
from dotenv import load_dotenv
load_dotenv()  # 必须在 import pydantic_ai 之前或在 Agent 实例化之前

from pydantic_ai import Agent
agent = Agent("openai:gpt-4o-mini")
```

也可以直接显式传：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIChatModel("gpt-4o-mini", provider=OpenAIProvider(api_key="sk-xxx"))
```

显式传的好处是**多账号切换、单元测试**。

---

## 7. 推荐的 requirements.txt

生产项目最小依赖：

```text
pydantic-ai-slim[openai,anthropic,logfire]>=0.0.20
pydantic>=2.6.0
python-dotenv>=1.0.0
```

完整学习项目（本仓库的版本）：

```text
pydantic-ai>=0.0.20
pydantic>=2.6.0
pydantic-evals>=0.0.20
pydantic-graph>=0.0.20
logfire>=2.0.0
mcp>=1.0.0
python-dotenv>=1.0.0
httpx>=0.27.0
```

---

## 8. 升级与版本对齐

Pydantic AI 还在 0.x 阶段，**小版本可能有 break change**。养成习惯：

```bash
pip install --upgrade pydantic-ai
pip show pydantic-ai | grep Version
```

升级前先看 [CHANGELOG](https://github.com/pydantic/pydantic-ai/releases)。

⚠️ 一个常见的 break：`output_type` 之前叫 `result_type`，0.0.13 之后统一改名。如果你看的是老博客示例，记得改过来：

```python
# ❌ 旧写法（0.0.13 之前）
agent = Agent("openai:gpt-4o", result_type=Invoice)

# ✅ 新写法
agent = Agent("openai:gpt-4o", output_type=Invoice)
```

---

## 9. 常见安装问题

| 现象 | 原因 | 解法 |
|------|------|------|
| `ImportError: cannot import name 'Agent' from 'pydantic_ai'` | 装成了 `pydantic_ai_xxx` 三方包 | `pip install pydantic-ai`（中划线） |
| `RuntimeError: pydantic-ai-slim does not include openai` | slim 没装 extras | `pip install "pydantic-ai-slim[openai]"` |
| `OpenAIError: api_key client option must be set` | 没加载 .env | `load_dotenv()` 加在最顶部 |
| `ssl: certificate verify failed`（公司网） | 自签证书 | 设 `SSL_CERT_FILE` 或用公司提供的 trust 包 |
| `metadata-generation-failed: pydantic-core` | Python < 3.10 或 Rust 工具链缺失 | 升 Python；或 `pip install --upgrade pip wheel` |
| `httpx.RemoteProtocolError` | 国内网络墙 OpenAI | 走代理 / 用 OpenRouter / 用国产模型 |
| `pip install pydantic-ai` 装到老 pydantic 1.x | 之前的虚拟环境污染 | `pip install -U pydantic-ai` 或新建 venv |

---

## 10. vs LangChain

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 主包是否包含所有 Provider | `pydantic-ai` 包含 / `slim` 不含 | 全部拆 partner 包 |
| 拆包策略 | 一个 slim + extras | 几十个独立包 |
| 安装心智 | "要不要 slim" | "要装哪几个 partner" |
| 升级心智 | 一个版本号 | 多个版本号要对齐 |

LangChain 的拆包是"一个 partner 一个包"，安装命令更长：

```bash
pip install langchain langchain-openai langchain-anthropic langgraph langsmith
```

Pydantic AI 用 `extras` 统一收口：

```bash
pip install "pydantic-ai-slim[openai,anthropic,logfire]"
```

---

## 11. 本章 demo

完整可运行代码：[`demos/basics/02_installation.py`](../../demos/basics/02_installation.py)

跑通后下一章：[03-first-agent.md](03-first-agent.md) —— 详细拆解 Agent。
