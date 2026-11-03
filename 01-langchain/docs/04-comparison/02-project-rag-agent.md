# 实战项目 1：RAG 知识库问答 Agent（create_agent + LangGraph）

> 这个项目把前面学的东西串起来，做一个 RAG Agent：支持多轮记忆和工具调用，内部资料不够时会再去检索，危险操作要人工审批，全程接入 LangSmith 方便排查。demo 用内存向量库，上线时要换成能持久化的向量库。

---

## 1. 需求

要做的是一个内部知识库问答 Agent。用户用对话的方式提问，Agent 先查内部文档；内部资料答不了，再用 web 搜索补充。遇到删数据、发邮件这类敏感操作，先暂停，等人审批后再执行。所有调用都追踪到 LangSmith，事后可以回看。系统要支持多用户、多会话。

技术栈：

```
LangChain 1.x (create_agent / 中间件 / loader / splitter / embeddings)
  + LangGraph 1.x (checkpointer、interrupt 持久化，create_agent 底层就是它)
  + LangSmith (tracing, eval)
```

---

## 2. 架构图

```
                 create_agent（模型 + 工具循环）
                 ┌───────────────────────────┐
  用户 ───────►  │  model ──► tools ──► model │ ──► 最终回答
                 └───────────────────────────┘
                     │        │         │
                kb_search  web_search  send_email
                (放行)      (放行)     (HumanInTheLoopMiddleware 拦截 → 等人审批)
                     ▲
        checkpointer(SqliteSaver) 按 thread_id 存对话与暂停点
```

> 「安全工具直接跑、危险工具先审批」如果用 LangGraph 手写，要 `StateGraph + ToolNode + review 节点 + 条件边`，要写几十行代码。用 `langchain.agents.create_agent` 加内置的 `HumanInTheLoopMiddleware`，几行配置就能做到，循环、`ToolNode`、中断和恢复都交给框架。只有需要深度定制图结构时，才需要下沉到 LangGraph，研究助手那个项目就是这种情况。

工具：

- `kb_search(query)`：内部向量库检索
- `web_search(query)`：互联网搜索（Tavily，可选）
- `send_email(to, subject, body)`：邮件（要审批）

---

## 3. 准备：构建知识库

沿用 LangChain 第 13 篇的思路：Loader → Splitter → Embedding → VectorStore。demo 用 `InMemoryVectorStore`，不依赖额外服务；生产环境换成 Chroma、pgvector 这类能持久化的向量库。

```python
from pathlib import Path
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

def build_kb():
    docs = [Document(page_content=p.read_text(encoding="utf-8"), metadata={"source": str(p)})
            for p in Path(".").glob("docs/01-langchain/0[15]-*.md")]
    chunks = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80).split_documents(docs)
    # 通义 text-embedding-v4：一次最多 10 条、不接受 token 数组 → chunk_size=10 + 关掉长度检查
    emb = OpenAIEmbeddings(model="text-embedding-v4", chunk_size=10, check_embedding_ctx_length=False)
    return InMemoryVectorStore.from_documents(chunks, emb)
```

> `InMemoryVectorStore.similarity_search` 依赖 `numpy`，需要 `pip install numpy`。装 Chroma 或 FAISS 时会顺带装上。

---

## 4. 工具定义

```python
from langchain_core.tools import tool

vs = build_kb()

@tool
def kb_search(query: str) -> str:
    """从内部知识库搜索资料。优先使用。"""
    docs = vs.similarity_search(query, k=4)
    if not docs:
        return "未在内部知识库找到相关资料"
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))

@tool
def web_search(query: str) -> str:
    """互联网搜索（兜底，当内部知识库没有时再用）。"""
    from langchain_tavily import TavilySearch   # pip install langchain-tavily；需要 TAVILY_API_KEY
    return str(TavilySearch(max_results=3).invoke({"query": query}))

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件。需要人工审批。"""
    return f"邮件已发给 {to}, 主题: {subject}"   # 真实场景接 SMTP
```

---

## 5. 组装 Agent：create_agent + 人工审批中间件

```python
import os
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")   # 走千问兼容接口，OPENAI_BASE_URL 指向百炼

SYSTEM = """你是公司知识库助手。回答用户问题的步骤：
1. 优先用 kb_search 查内部知识库
2. 若内部无资料，再 web_search
3. 整合后用中文回答，引用编号 [n]
4. 涉及发邮件的请求，使用 send_email 工具，会自动暂停等待人工审批
"""

def build_app(checkpointer):
    return create_agent(
        ChatOpenAI(model=MODEL, temperature=0),
        tools=[kb_search, web_search, send_email],
        system_prompt=SYSTEM,                                   # 旧版的 state_modifier / prompt= 已不存在
        middleware=[HumanInTheLoopMiddleware(interrupt_on={"send_email": True})],
        checkpointer=checkpointer,                              # HITL 必须有 checkpointer
    )
```

几个要点：

- `interrupt_on={"send_email": True}`：只有列出的工具会被拦截，其余直接放行。值也可以写成 `{"allowed_decisions": ["approve", "reject"]}`，限制审批人能做的决定（可选 approve / edit / reject / respond，传 `True` 时四种都允许），例如 `interrupt_on={"send_email": {"allowed_decisions": ["approve", "reject"]}}`。
- 被拦截时，agent 在执行 `send_email` 之前暂停，`invoke` 正常返回，待审批的内容放在 `result["__interrupt__"]` 里。
- 工具内部抛异常时默认会直接中断运行。想把错误反馈给模型、让它自己重试，需要另外挂 `ToolErrorMiddleware`。

---

## 6. 完整 demo

完整代码：[`demos/langgraph/project_rag_agent.py`](../../demos/langgraph/project_rag_agent.py)（在 `01-langchain` 目录下运行）

```python
from langgraph.types import Command

with SqliteSaver.from_conn_string("./rag_agent.db") as memory:
    app = build_app(memory)
    cfg = {"configurable": {"thread_id": "user-001"}}

    # 1) 普通问答
    out = app.invoke({"messages": [("human", "LCEL 是什么？")]}, config=cfg)
    print(out["messages"][-1].content)

    # 2) 多轮：同一个 thread_id 自动带上历史
    out = app.invoke({"messages": [("human", "它和老版本 chain 有什么区别？")]}, config=cfg)
    print(out["messages"][-1].content)

    # 3) 需要审批的工具调用
    out = app.invoke({"messages": [("human", "给 boss@x.com 发一封请假邮件")]}, config=cfg)
    if "__interrupt__" in out:
        req = out["__interrupt__"][0].value["action_requests"][0]
        print("待审：", req["name"], req["args"])
        # 批准；也可以 {"type": "reject", "message": "..."} 拒绝，或 {"type": "edit", "edited_action": {...}} 改参数后执行
        final = app.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)
        print(final["messages"][-1].content)
```

---

## 7. 部署到 LangSmith Deployment（原 LangGraph Platform）

项目结构：

```
my-rag-agent/
├── langgraph.json
├── requirements.txt
└── src/
    ├── graph.py    # graph = create_agent(...)；部署平台自带持久化，不用自己传 checkpointer
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

本地用 `langgraph dev` 启动，配合 Studio 调试。上线时把仓库推到 GitHub，在 LangSmith 的 Deployments 里关联仓库部署，详见 LangGraph 第 12 篇。

---

## 8. 可观测：LangSmith

环境变量：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=rag-agent-prod
```

每条对话跑完后，在 LangSmith 项目页能看到完整的 trace 树，可以把回答差的样本加进 dataset 做评估。想按用户过滤，要在调用时把用户 ID 写进 run 的 metadata，例如 `config={"configurable": {"thread_id": ...}, "metadata": {"user_id": "u-001"}}`，之后就能在项目页按 `metadata.user_id` 筛选；上面的 demo 没传这个字段。

---

## 9. 评估

```python
from uuid import uuid4
from langsmith import evaluate   # 旧路径 langsmith.evaluation 仍可用
from pydantic import BaseModel, Field

class Score(BaseModel):
    score: int = Field(description="0-10")
    reasoning: str

judge = ChatOpenAI(model=MODEL, temperature=0).with_structured_output(Score)

def target(inputs):
    cfg = {"configurable": {"thread_id": f"eval-{uuid4()}"}}
    out = app.invoke({"messages": [("human", inputs["question"])]}, config=cfg)
    contexts = [m.content for m in out["messages"] if m.type == "tool"]   # 检索工具返回的资料
    return {"answer": out["messages"][-1].content, "contexts": contexts}

def faithfulness(outputs: dict) -> dict:
    """答案是否只依据检索到的资料，有没有编造"""
    s = judge.invoke(f"资料：\n{outputs['contexts']}\n\n回答：{outputs['answer']}\n\n"
                     "回答里的事实是否都能在资料中找到依据？0-10 分。")
    return {"key": "faithfulness", "score": s.score / 10, "comment": s.reasoning}

def relevance(inputs: dict, outputs: dict) -> dict:
    """答案是否回答了问题"""
    s = judge.invoke(f"问题：{inputs['question']}\n回答：{outputs['answer']}\n\n回答是否切题？0-10 分。")
    return {"key": "relevance", "score": s.score / 10, "comment": s.reasoning}

evaluate(target, data="rag-agent-eval", evaluators=[faithfulness, relevance])
```

两个评估器都是 LLM-as-judge 的最小写法（参见 LangSmith 第 3 篇的 RAG Eval 模板），也可以换成 `openevals` 里现成的 RAG 评估器。`rag-agent-eval` 数据集要提前在 LangSmith 里建好，每条 example 的 inputs 带 `question`。

eval 建议定期跑：每次改 prompt、换模型或调整检索参数之后跑一遍，并把线上收到差评的问题陆续补进数据集。

---

## 10. 工程清单

- [ ] kb_search top_k 调优（4 / 6 / 8 A/B）
- [ ] 给 retriever 加 BM25 + 向量 ensemble + Reranker（LangChain 第 13 篇的方案）
- [ ] HITL 接前端 + 通知（钉钉 / Slack）
- [ ] 前端的点赞 / 点踩按钮接入 LangSmith feedback
- [ ] 部署版本号写进 metadata
- [ ] 失败回退到 fallback 模型（`ModelFallbackMiddleware`）
- [ ] 限速 + 重试（`ModelRetryMiddleware` / `ToolRetryMiddleware`，`langchain 1.4` 内置）
- [ ] 知识库增量更新机制（监听 Git push 触发重建）
