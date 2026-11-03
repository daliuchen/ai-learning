# Pydantic AI 02-04：Common Tools 内置常用工具

> Pydantic AI 自带了几样常用工具：DuckDuckGo、Tavily、Exa 搜索，以及把网页抓成 Markdown 的 `web_fetch_tool`。装上对应的 extra，import 进来就能交给 Agent。这篇讲它们各自怎么用、哪些参数该由开发者锁死，以及怎么在不同模型之间回退。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. Common Tools vs Native Tools

这两个名字容易混，先对比一下：

| 维度 | Common Tools（本篇） | Native Tools（第 5 篇） |
|------|---------------------|------------------------|
| 谁来执行 | Pydantic AI 在本地调用第三方 SDK | 模型 provider 自己执行 |
| 来源 | `pydantic_ai.common_tools.*` | `pydantic_ai.native_tools.*` |
| 配置门槛 | 装包 + API key | 模型必须支持 |
| 跨 provider | 任何模型都能用 | 看 provider 是否支持 |
| 代表 | `duckduckgo_search_tool` | `WebSearchTool`（OpenAI/Anthropic 内置） |

common tools 是 Pydantic AI 替你写好的函数工具，native tools 是模型服务自带的能力。

---

## 2. 内置 Common Tools 速查

| 工具 | 包 / extra | 主要用途 |
|------|------------|----------|
| `duckduckgo_search_tool` | `pydantic-ai-slim[duckduckgo]` | 免费网页搜索 |
| `tavily_search_tool` | `pydantic-ai-slim[tavily]` | 神经搜索，效果好（付费） |
| `web_fetch_tool` | `pydantic-ai-slim[web-fetch]` | 抓网页转 Markdown（带 SSRF 防护） |
| `exa_search_tool` 等（已弃用） | `pydantic-ai-slim[exa]` | Exa 搜索；2.64 起改用 harness 的 `ExaSearch` capability |

> 装包：`pip install "pydantic-ai-slim[duckduckgo,tavily,web-fetch]"`

---

## 3. DuckDuckGo Search Tool

最简单的免费搜索，不用任何配置：

```python
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

agent = Agent(
    'openai:gpt-6.1-sol',
    tools=[duckduckgo_search_tool()],
    instructions='Search DuckDuckGo for the query and return the results.',
)

result = agent.run_sync('Top 5 highest-grossing animated films of 2025?')
print(result.output)
```

几点说明：

- `duckduckgo_search_tool()` 返回的是 `Tool` 实例，所以放进 `tools=[]`，不是 `toolsets=[]`
- 内部用的是 [`ddgs`](https://github.com/deedy5/ddgs)（包名已从 `duckduckgo_search` 改成 `ddgs`，装 `pydantic-ai-slim[duckduckgo]` 时会一起装上），国内网络可能要配 socks 代理
- 免费、不要 key，但限流很严

---

## 4. Tavily Search Tool

Tavily 是付费的搜索服务，面向 AI 场景设计，结果摘要比 DuckDuckGo 好不少：

```python
import os
from pydantic_ai import Agent
from pydantic_ai.common_tools.tavily import tavily_search_tool

agent = Agent(
    'openai:gpt-6.1-sol',
    tools=[tavily_search_tool(api_key=os.environ['TAVILY_API_KEY'])],
    instructions='Search Tavily and answer with citations.',
)

result = agent.run_sync('Tell me top GenAI news this week, with links.')
print(result.output)
```

### 4.1 锁参数：开发者和模型各管一部分

`tavily_search_tool` 的参数设计值得看一下：创建工具时可以把一部分参数固定下来，剩下的才交给模型调用时填。

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
| `max_results` | 不能，始终由开发者控制 |
| `include_domains` | 传了就锁定，不传 LLM 可控 |
| `exclude_domains` | 同上 |

我很喜欢这种设计：涉及成本、合规的参数由开发者定死，普通参数交给模型。

---

## 5. Web Fetch Tool

把网页抓下来转成 Markdown，自带 SSRF（服务器端请求伪造）防护，不会被诱导去访问内网地址：

```python
from pydantic_ai import Agent
from pydantic_ai.common_tools.web_fetch import web_fetch_tool

agent = Agent(
    'openai:gpt-6.1-sol',
    tools=[web_fetch_tool()],
    instructions='Fetch the page and summarize.',
)

result = agent.run_sync('Summarize https://pydantic.dev/docs/ai')
```

> `web_fetch_tool` 需要 `pydantic-ai-slim[web-fetch]`（会装 `markdownify` 等，没装会报错并提示怎么装）。不少模型自带网页抓取能力，要跨 provider 的话，建议用 `WebFetch` capability，它可以回退到本地实现，见第 8 节。

---

## 6. Exa 搜索

Exa 是一个神经搜索引擎。`pydantic_ai.common_tools.exa` 里的 `exa_search_tool` / `exa_find_similar_tool` / `exa_get_contents_tool` / `exa_answer_tool` 和 `ExaToolset` 在 2.64 里都已标记弃用（会发 `PydanticAIDeprecationWarning`，计划在 v3 删除），官方改推 harness 里的 `ExaSearch` capability：

```python
# pip install "pydantic-ai-harness[exa]"，读 EXA_API_KEY
from pydantic_ai import Agent
from pydantic_ai_harness.exa import ExaSearch

agent = Agent('openai:gpt-6.1-sol', capabilities=[ExaSearch()])
```

`ExaSearch` 给 agent 加两个工具：`web_search` 返回搜索结果和最相关的摘录，`get_page` 取某个 URL 的全文；每个工具的结果里，来源 URL 和标题放在 `ToolReturn` 的 metadata 里，应用可以直接拿来渲染引用。老代码里的 `ExaToolset(api_key=..., include_search=True, include_answer=False, ...)` 还能用，几个工具共用一个 Exa client。

---

## 7. 实战：调研 Agent

把搜索和网页抓取串起来，让 Agent 自己调研一个主题：

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
    'openai:gpt-6.1-sol',
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

模型会自己安排先搜索、再抓取，最后输出结构化的 `Report`。

---

## 8. 跨 provider：用 capability 回退

如果 Agent 要在不同 provider 之间切换，建议用会按 provider 自动适配的 capability（`WebSearch` / `WebFetch` 等）：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch, WebFetch

agent = Agent(
    'anthropic:claude-sonnet-5-5',
    capabilities=[
        WebSearch(local='duckduckgo'),     # 原生优先；不支持原生搜索的模型回退到 DuckDuckGo
        WebFetch(local=True),              # 原生优先；回退到本地 web_fetch_tool
    ],
)
```

行为是这样的：

- 模型支持原生网页搜索（Anthropic、OpenAI Responses、Google 等），就用原生的；
- 不支持的（比如千问的 OpenAI 兼容端点），用你给的 `local`：`'duckduckgo'` / `True` 表示内置的本地工具，也可以传任意函数，如 `WebSearch(local=my_search_fn)`。

要特别注意，不传 `local=` 时它只用**原生工具**，模型不支持就直接报错。另外，如果给 `WebSearch` 设了 `allowed_domains` / `max_uses` 这类原生专属的限制，内置的本地回退会被关掉，因为本地工具没法执行这些限制。想在不支持原生搜索的模型上也守住域名限制，就把限制写进 `native=WebSearchTool(allowed_domains=[...])`，再自己提供一个带同样限制的 `local` 函数。

这样切换模型时只改 model 字符串，业务代码不用动。

---

## 9. Logfire 观测

Pydantic AI 和 [Logfire](https://pydantic.dev/logfire) 集成得很深，工具调用的输入输出、耗时、token 都会自动上报：

```python
import logfire

logfire.configure()
logfire.instrument_pydantic_ai()

agent.run_sync('...')   # 工具调用全程可观测
```

排查「这个工具被调了几次」「哪个工具卡了 5 秒」这类问题，生产环境里用 Logfire 很方便。

---

## 10. 自己写内部的「common tools」

不少团队想给公司内部写一套类似的通用工具。我的建议是写成工厂函数：

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

调用方一行就够：

```python
agent = Agent('openai:gpt-6.1-sol', tools=[jira_search_tool(project='ABC')])
```

用工厂而不用全局函数，原因有几个：不同实例可以传不同的 API key 和配置；可以照搬 `tavily_search_tool` 那种由开发者锁参数的做法；测试时也好替换。

---

## 11. 工厂函数里的参数分工

把 Tavily 的设计搬过来：同一个工厂函数里，创建时固定的参数由开发者控制，不进模型看到的 schema；没固定的留给模型。

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

模型看到的 schema 只有 `{query, status}`，看不到 project 和 max_results，既不会查错项目，成本也可控。

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `ImportError: pydantic_ai.common_tools.duckduckgo` | 没装 extra | `pip install "pydantic-ai-slim[duckduckgo]"` |
| DDG 报 ratelimit / connection error | 国内网络，或请求太频繁 | 配 socks 代理、降低频率，或换 Tavily |
| Tavily 有结果，模型却说「搜不到」 | 模型没认真读结果 | 在 instructions 里要求必须基于搜索结果回答 |
| Tavily 费用失控 | 模型触发了大量搜索 | 创建工具时锁 `max_results=3` |
| `web_fetch_tool` 拿不到 JS 渲染的内容 | 它只是普通 HTTP 请求加 Markdown 转换 | 用原生 `WebFetch`（部分模型支持）或 Playwright |
| 同时挂 common 搜索和原生 WebSearch，行为混乱 | 模型选哪个不可控 | 二选一 |
| 用 `exa_search_tool` 等报 `PydanticAIDeprecationWarning` | 2.64 起这组工具已弃用 | 换成 harness 的 `ExaSearch` capability |
| 搜索结果格式不统一，难解析 | 不同 provider 字段不同 | 让模型输出 `Report(BaseModel)` 这样的结构化结果 |

---

## 13. 生产建议

1. 跨 provider 的项目优先用 capability（`WebSearch(local=...)` / `WebFetch(local=...)`），让框架决定用原生还是本地工具
2. Tavily / Exa 锁住 `max_results` 控制成本
3. 搜索、抓取、总结这条链用 `output_type=BaseModel` 输出结构化结果，方便下游使用
4. 生产环境接上 Logfire，工具调用情况一目了然
5. 内部的通用工具写成工厂函数，参照 `jira_search_tool(api_key=..., project=...)` 的写法

---

## 14. 本章 demo

完整可运行代码：[`demos/tools/04_common_tools.py`](../../demos/tools/04_common_tools.py)

下一篇：[Pydantic AI 02-05：Native Tools 模型内置工具](05-native-tools.md)，讲模型 provider 自带的原生工具。
