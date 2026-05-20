# Pydantic AI 02-04：Common Tools 内置常用工具

> **一句话**：Pydantic AI 内置了一批"开箱即用"的常用工具——搜索（DuckDuckGo / Tavily / Exa）、网页抓取（web_fetch_tool）等，`pip` 装上 import 一句话就能用。

---

## 1. Common Tools vs Native Tools

很多人会把 "common tools" 和 "native tools" 搞混，先一句话区分：

| 维度 | Common Tools（本篇） | Native Tools（第 5 篇） |
|------|---------------------|------------------------|
| 谁来执行 | **Pydantic AI 本地**调用第三方 SDK | **模型 provider** 自己执行 |
| 来源 | `pydantic_ai.common_tools.*` | `pydantic_ai.native_tools.*` |
| 配置门槛 | 装包 + API key | 模型必须支持 |
| 跨 provider | ✅ 通吃 | ❌ 看 provider |
| 典型代表 | `duckduckgo_search_tool` | `WebSearchTool`（OpenAI/Anthropic 内置） |

简单来说：**common tools 是"Pydantic AI 帮你写好的函数工具"，native tools 是"模型自带的能力"**。

---

## 2. 内置 Common Tools 速查

| 工具 | 包 / extra | 主要用途 |
|------|------------|----------|
| `duckduckgo_search_tool` | `pydantic-ai-slim[duckduckgo]` | 免费网页搜索 |
| `tavily_search_tool` | `pydantic-ai-slim[tavily]` | 神经搜索，效果好（付费） |
| `web_fetch_tool` | `pydantic-ai-slim[web-fetch]` | 抓网页转 Markdown（带 SSRF 防护） |
| `exa_search_tool` 等 | `pydantic-ai-slim[exa]` | Exa 神经搜索套件（含 find_similar / get_contents / answer） |

> 装包：`pip install "pydantic-ai-slim[duckduckgo,tavily,web-fetch]"`

---

## 3. DuckDuckGo Search Tool

最简单的免费搜索，**零配置**：

```python
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

agent = Agent(
    'openai:gpt-4o-mini',
    tools=[duckduckgo_search_tool()],
    instructions='Search DuckDuckGo for the query and return the results.',
)

result = agent.run_sync('Top 5 highest-grossing animated films of 2025?')
print(result.output)
```

**关键点**：

- `duckduckgo_search_tool()` 返回的是一个 `Tool` 实例，所以传给 `tools=[]` 而不是 `toolsets=[]`
- 内部用 [`ddgs`](https://github.com/deedy5/ddgs)，国内网络可能要 socks 代理
- 免费、不需 key，但 rate limit 严格

---

## 4. Tavily Search Tool

Tavily 是付费的"AI 友好"搜索，结果摘要质量比 DDG 好不少：

```python
import os
from pydantic_ai import Agent
from pydantic_ai.common_tools.tavily import tavily_search_tool

agent = Agent(
    'openai:gpt-4o-mini',
    tools=[tavily_search_tool(api_key=os.environ['TAVILY_API_KEY'])],
    instructions='Search Tavily and answer with citations.',
)

result = agent.run_sync('Tell me top GenAI news this week, with links.')
print(result.output)
```

### 4.1 锁参数：开发者 vs LLM 分权

`tavily_search_tool` 的参数有意思——你可以**在工具创建时锁死一部分**，剩下的让 LLM 自由调：

```python
tavily_search_tool(
    api_key=...,
    max_results=5,                          # ❶ 开发者锁定，LLM 看不到
    include_domains=['arxiv.org'],          # ❷ 同上，固定只搜 arxiv
    # exclude_domains 不传 → LLM 可在每次调用时设
)
```

| 参数 | 锁定后 LLM 是否能改 |
|------|---------------------|
| `max_results` | ❌ 永远开发者控制 |
| `include_domains` | 传了就锁定，不传 LLM 可控 |
| `exclude_domains` | 同上 |

这种设计很优雅：**敏感参数（成本、合规）开发者卡死，普通参数交给模型**。

---

## 5. Web Fetch Tool

抓网页转 Markdown，自带 SSRF（服务器端请求伪造）防护，**不会被注入访问内网**：

```python
from pydantic_ai import Agent
from pydantic_ai.common_tools.web_fetch import web_fetch_tool

agent = Agent(
    'openai:gpt-4o-mini',
    tools=[web_fetch_tool()],
    instructions='Fetch the page and summarize.',
)

result = agent.run_sync('Summarize https://ai.pydantic.dev')
```

> 提示：现代模型如 GPT-5 / Claude Sonnet 4.5 已有 native `WebFetch`，跨 provider 时建议用 `WebFetch` capability（自动 fallback 到 `web_fetch_tool`），见下面 §8。

---

## 6. Exa 搜索套件

Exa 是神经搜索引擎，提供多个工具：

```python
from pydantic_ai import Agent
from pydantic_ai.common_tools.exa import (
    exa_search_tool,
    exa_find_similar_tool,
    exa_get_contents_tool,
    exa_answer_tool,
    ExaToolset,
)

# 单个工具
agent = Agent('openai:gpt-4o-mini', tools=[exa_search_tool(api_key=...)])

# 或一次性套餐
agent = Agent(
    'openai:gpt-4o-mini',
    toolsets=[ExaToolset(api_key=..., include=['search', 'get_contents', 'answer'])],
)
```

`ExaToolset` 比单独 import 4 个工具节省成本：共享一个 HTTP client + 一份缓存。

---

## 7. 一个"调研 Agent"实战

把搜索 + 网页抓取串起来，让 Agent 自动调研一个主题：

```python
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.common_tools.web_fetch import web_fetch_tool

class Report(BaseModel):
    """Research report"""
    summary: str
    key_points: list[str]
    sources: list[str]

agent = Agent(
    'openai:gpt-4o-mini',
    tools=[duckduckgo_search_tool(), web_fetch_tool()],
    output_type=Report,
    instructions=(
        "You are a research assistant. "
        "Step 1: search DuckDuckGo. "
        "Step 2: fetch the top 2-3 pages. "
        "Step 3: synthesize a structured report with sources."
    ),
)

report = agent.run_sync('Latest progress on Pydantic AI v2').output
print(report.summary)
print(report.key_points)
print(report.sources)
```

模型会**自己规划**先搜索后抓取的步骤，最后输出 Pydantic 结构化 `Report`。

---

## 8. 进阶：跨 provider 用 capability fallback

如果你的 Agent 要在 OpenAI / Anthropic / 本地模型之间切换，建议用 `capabilities`：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch, WebFetch

agent = Agent(
    'openai:gpt-4o-mini',
    capabilities=[WebSearch(), WebFetch()],
)
```

行为：

- 模型支持 native web search（如 Anthropic Sonnet 4.5）→ 用 native
- 不支持 → 自动用 `duckduckgo_search_tool` / `web_fetch_tool` 兜底

**业务代码零改动**，只需切 model 字符串。

---

## 9. Logfire 观测

Pydantic AI 和 [Logfire](https://pydantic.dev/logfire) 深度集成，工具调用的 input/output / 耗时 / token 全部自动上报：

```python
import logfire

logfire.configure()
logfire.instrument_pydantic_ai()

agent.run_sync('...')   # 工具调用全程可观测
```

生产环境推荐用 Logfire 排查"工具被调了几次 / 哪个工具卡了 5s"这类问题。

---

## 10. 自己实现"内置工具"模式

很多团队会想自己写一个 toolset 当公司内部"common tools"。最佳实践是**写工厂函数**：

```python
# my_company/common_tools/jira.py
from pydantic_ai import Tool

def jira_search_tool(api_key: str | None = None, project: str | None = None) -> Tool:
    api_key = api_key or os.environ['JIRA_API_KEY']

    def search(query: str) -> list[dict]:
        """Search Jira issues.

        Args:
            query: JQL or natural language query.
        """
        # ... 调 Jira API ...
        return [{'key': 'ABC-1', 'title': 'login bug'}]

    return Tool(search, takes_ctx=False, name='jira_search')
```

调用方一行：

```python
agent = Agent('openai:gpt-4o-mini', tools=[jira_search_tool(project='ABC')])
```

**为什么用工厂而非全局函数**：

1. 不同实例可以传不同 API key / 配置
2. 类似 `tavily_search_tool` 的"开发者锁参数"模式天然支持
3. 测试好替换

---

## 10.5 工厂函数的"开发者锁参 vs LLM 自由调"模式

把 Tavily 的设计搬过来——同一个工厂函数，固定参数 = 开发者控制（不进 LLM schema），不固定 = 留给 LLM：

```python
def jira_search_tool(
    *,
    api_key: str | None = None,
    project: str | None = None,    # ← 开发者锁定
    max_results: int = 5,          # ← 开发者锁定
) -> Tool:
    api_key = api_key or os.environ['JIRA_API_KEY']

    def search(query: str, status: str = 'open') -> list[dict]:
        """Search Jira issues for current project.

        Args:
            query: JQL or natural language.
            status: 'open' / 'closed' / 'all'.
        """
        # 真实调用里把 project / max_results 拼进 API request
        return [
            {'key': f'{project}-{i}', 'title': f'about {query}', 'status': status}
            for i in range(1, max_results + 1)
        ]

    return Tool(search, takes_ctx=False, name='jira_search')
```

调用方：

```python
# 项目 A
agent_a = Agent('...', tools=[jira_search_tool(project='ABC', max_results=3)])
# 项目 B 同一工厂，固定不同参数
agent_b = Agent('...', tools=[jira_search_tool(project='XYZ', max_results=10)])
```

模型看到的 schema 只有 `{query, status}`，根本看不到 project / max_results——**调用安全 + 成本可控**。

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `ImportError: pydantic_ai.common_tools.duckduckgo` | 没装 extra | `pip install "pydantic-ai-slim[duckduckgo]"` |
| DDG 报 ratelimit / connection error | 国内网络 / 频繁请求 | 加 socks / 减少频率 / 切 Tavily |
| Tavily 返回但模型说"我搜不到" | 模型没耐心读完结果 | 在 instructions 里强调"必须基于搜索结果回答" |
| Tavily 费用爆炸 | LLM 触发了大量搜索 | 工具创建时锁 `max_results=3` |
| `web_fetch_tool` 拿不到 JS 渲染内容 | 它是裸 HTTP + Markdown 转换 | 用 native `WebFetch`（部分模型支持） / Playwright |
| 同时用搜索 + native WebSearch 工具行为冲突 | 模型选谁不可控 | 二选一，要么 native，要么 common |
| Exa 套件每次重新建 HTTP client | 直接 import 4 个工具是这样 | 用 `ExaToolset` 共享 client |
| 搜索结果格式不统一难解析 | 不同 provider 字段不同 | 在 prompt 里规定模型要返回 `Report(BaseModel)` |

---

## 12. 生产建议

1. **跨 provider 项目优先用 capability**（`WebSearch` / `WebFetch`），让框架自己选 native or common
2. **Tavily / Exa 锁 `max_results` 控制成本**
3. **搜索 → 抓取 → 总结**这套用 `output_type=BaseModel` 结构化输出，给下游用
4. **生产开 Logfire**，工具调用可观测
5. **私有"common tools" 写工厂函数**：`jira_search_tool(api_key=..., project=...)` 风格

---

## 13. 本章 demo

完整可运行代码：[`demos/tools/04_common_tools.py`](../../demos/tools/04_common_tools.py)

下一篇：[05-native-tools.md](05-native-tools.md) — 模型 provider 自带的 native 工具。
