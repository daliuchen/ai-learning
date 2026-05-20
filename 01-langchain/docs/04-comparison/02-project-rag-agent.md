# 实战项目 1：RAG 知识库问答 Agent（基于 LangGraph）

> **一句话**：把前面所学整合成一个完整可上线的 RAG Agent，具备多轮记忆 + 工具调用 + 反思重检索 + Human-in-the-loop + LangSmith 可观测。

---

## 1. 需求

我们要做一个内部知识库问答 Agent：

- 用户对话式问问题
- Agent 先检索内部文档
- 若答案不确定 → 调用 web 搜索做补充
- 用户问敏感问题（删数据/发邮件） → 暂停等人审
- 全部追踪到 LangSmith，可回看
- 支持多用户、多会话

技术栈：

```
LangChain (loader/splitter/embeddings/retriever)
  + LangGraph (state machine, HITL, checkpointer)
  + LangSmith (tracing, eval)
```

---

## 2. 架构图

```
                    ┌──────────┐
                    │  agent   │ ← LLM 决定下一步
                    └──────────┘
                  ↑    ↓
   ┌──────────────────────────────────┐
   │                                  │
[retrieve]   [web_search]  [send_email + HITL审]
   │             │              │
   └─────────────┴──────────────┘
                  ↓
              messages ← LLM 看结果再决定
```

State：

```python
class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

工具：

- `kb_search(query)`：内部向量库检索
- `web_search(query)`：互联网搜索（Tavily）
- `send_email(to, subject, body)`：邮件（要审批）

---

## 3. 准备：构建知识库

复用 LangChain 13 章的代码：

```python
# 离线构建（一次性）
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

def build_kb():
    docs = DirectoryLoader("./docs", glob="**/*.md",
        loader_cls=lambda p: TextLoader(p, encoding="utf-8")).load()
    chunks = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80).split_documents(docs)
    Chroma.from_documents(chunks, OpenAIEmbeddings(model="text-embedding-3-small"),
        persist_directory="./rag_db", collection_name="kb")
```

---

## 4. 工具定义

```python
from langchain_core.tools import tool
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

emb = OpenAIEmbeddings(model="text-embedding-3-small")
vs = Chroma(persist_directory="./rag_db", collection_name="kb", embedding_function=emb)

@tool
def kb_search(query: str) -> str:
    """从内部知识库搜索资料。"""
    docs = vs.similarity_search(query, k=4)
    if not docs:
        return "未在内部知识库找到相关资料"
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))

@tool
def web_search(query: str) -> str:
    """互联网搜索（兜底，当内部知识库没有时再用）。"""
    from langchain_community.tools.tavily_search import TavilySearchResults
    return TavilySearchResults(max_results=3).invoke(query)

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件。⚠️ 需要人工审批。"""
    # 真实场景接 SMTP
    return f"邮件已发给 {to}, 主题: {subject}"
```

---

## 5. 主 Graph

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt

SAFE_TOOLS = [kb_search, web_search]
DANGEROUS_TOOLS = [send_email]
ALL_TOOLS = SAFE_TOOLS + DANGEROUS_TOOLS

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(ALL_TOOLS)

SYSTEM = """你是公司知识库助手。回答用户问题的步骤：
1. 优先用 kb_search 查内部知识库
2. 若内部无资料，再 web_search
3. 整合后用中文回答，引用编号 [n]
4. 涉及发邮件的请求，使用 send_email 工具，会自动暂停等待人工审批
"""

def agent(state: State):
    msgs = [SystemMessage(content=SYSTEM), *state["messages"]]
    return {"messages": [model.invoke(msgs)]}

safe_tool_node = ToolNode(SAFE_TOOLS)
dangerous_tool_node = ToolNode(DANGEROUS_TOOLS)

def review(state: State):
    last = state["messages"][-1]
    calls = [c for c in last.tool_calls if c["name"] in {"send_email"}]
    if not calls:
        return {}
    decision = interrupt({
        "type": "approval",
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in calls],
    })
    if decision != "yes":
        return {"messages": [
            ToolMessage(content="人工拒绝执行", tool_call_id=c["id"]) for c in calls
        ]}
    return {}

def router(state: State) -> str:
    last = state["messages"][-1]
    if not last.tool_calls:
        return END
    if any(c["name"] in {"send_email"} for c in last.tool_calls):
        return "review"
    return "safe_tools"

def post_review(state: State) -> str:
    last = state["messages"][-1]
    # 如果 review 已经塞了 ToolMessage（拒绝），跳过 dangerous 节点
    if isinstance(last, ToolMessage):
        return "agent"
    return "dangerous_tools"

def build_app(memory):
    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("safe_tools", safe_tool_node)
    g.add_node("review", review)
    g.add_node("dangerous_tools", dangerous_tool_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", router,
        {"safe_tools": "safe_tools", "review": "review", END: END})
    g.add_edge("safe_tools", "agent")
    g.add_conditional_edges("review", post_review,
        {"dangerous_tools": "dangerous_tools", "agent": "agent"})
    g.add_edge("dangerous_tools", "agent")
    return g.compile(checkpointer=memory)
```

---

## 6. 完整 demo

[`demos/langgraph/project_rag_agent.py`](../../demos/langgraph/project_rag_agent.py)

```python
"""跑：python demos/langgraph/project_rag_agent.py"""
import os
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
# 上面所有定义

load_dotenv()

with SqliteSaver.from_conn_string("./rag_agent.db") as memory:
    app = build_app(memory)
    cfg = {"configurable": {"thread_id": "user-001"}}

    # 1) 普通问答
    out = app.invoke({"messages": [("human", "LCEL 是什么？")]}, config=cfg)
    print(out["messages"][-1].content)

    # 2) 多轮
    out = app.invoke({"messages": [("human", "它和老版本 chain 有什么区别？")]}, config=cfg)
    print(out["messages"][-1].content)

    # 3) 需要审批的工具调用
    out = app.invoke({"messages": [("human", "给 boss@x.com 发一封请假邮件")]}, config=cfg)
    if "__interrupt__" in out:
        print("待审：", out["__interrupt__"][0].value)
        final = app.invoke(Command(resume="yes"), config=cfg)
        print(final["messages"][-1].content)
```

---

## 7. 部署到 LangGraph Platform

项目结构：

```
my-rag-agent/
├── langgraph.json
├── requirements.txt
└── src/
    ├── graph.py    # 上面 build_app() 改造成 graph = ...
    ├── tools.py
    └── kb.py       # 知识库初始化脚本
```

`langgraph.json`：

```json
{
  "dependencies": ["."],
  "graphs": {"rag_agent": "./src/graph.py:graph"},
  "env": ".env",
  "python_version": "3.11"
}
```

`langgraph dev` 本地起，`langgraph deploy` 上线。

---

## 8. 可观测：LangSmith

环境变量：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=rag-agent-prod
```

跑完每条对话到 LangSmith 项目页：

- 看到完整 trace 树
- 按 `metadata.user_id` 过滤
- 把回答差的样本入 dataset 做 eval

---

## 9. 评估

```python
from langsmith.evaluation import evaluate

def target(inputs):
    cfg = {"configurable": {"thread_id": f"eval-{uuid4()}"}}
    out = app.invoke({"messages": [("human", inputs["question"])]}, config=cfg)
    return {"answer": out["messages"][-1].content}

evaluate(target, data="rag-agent-eval", evaluators=[faithfulness, relevance])
```

迭代节奏：周一发版 → 周三跑 eval → 周五审核 dataset 新增 → 下周再迭代。

---

## 10. 工程清单

- [ ] kb_search top_k 调优（4 / 6 / 8 A/B）
- [ ] 给 retriever 加 BM25 + 向量 ensemble + Reranker（13 章方案）
- [ ] HITL 接前端 + 通知（钉钉 / Slack）
- [ ] LangSmith feedback 接 👍 / 👎 UI
- [ ] 部署版本号写进 metadata
- [ ] 失败回退到 fallback 模型（gpt-4o → claude）
- [ ] 限速 + 重试
- [ ] 知识库增量更新机制（监听 Git push 触发重建）

---

## 11. 本章 demo

[`demos/langgraph/project_rag_agent.py`](../../demos/langgraph/project_rag_agent.py)
