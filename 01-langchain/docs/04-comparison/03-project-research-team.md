# 实战项目 2：多 Agent 研究助手（子图 + Map-Reduce + HITL）

> 这个项目做一个分三段的研究助手：主图负责编排，先由研究子图并行检索，再 fan-out 给多个写作节点并行写各节，最后交给编辑整合。LangGraph 的几项进阶能力都会用到：子图、Send / Map-Reduce、HITL（interrupt）、持久化，以及 LangSmith Trace。

版本：以 `langgraph 1.2.x` 为准。模型通过 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `QWEN_MODEL` 环境变量指向任意 OpenAI 兼容服务，文中用 `MODEL` 表示。

---

## 1. 需求

输入一个研究主题（比如「LangGraph 的最佳实践」），输出一份结构化的研究报告：

```
1. 概述（150 字）
2. 核心特性
   - 特性 A：xxx
   - 特性 B：xxx
3. 与其他框架对比
4. 适用场景
5. 参考资料
```

具体要求：研究团队同时查 web、文档库和 arxiv；写作团队由多个写手并行，每人写一节；最后由编辑 Agent 整合润色。研究完成和初稿完成这两个节点要停下来，等用户审核。整个执行过程可以用 `langgraph dev` 在 LangGraph Studio 里看到。

---

## 2. 架构

```
┌──────────────────────────────────────────────┐
│              主图（StateGraph）               │
└──────────────────────────────────────────────┘
   │              │              │
   ▼              ▼              ▼
[Research Team] [Writing Team] [Edit]
   │                │
   ├── web_search   ├── section_writer (并行 N 个)
   ├── doc_search   │
   └── arxiv_search │
   ↓                ↓
[research_summary] [draft_sections]
```

主 state：

```python
class MainState(TypedDict):
    topic: str
    research: str                       # 研究团队产物
    sections: Annotated[list, add]      # 写作团队产物（并行）
    final: str                          # 编辑产物
    stage: str                          # 流程阶段：init / writing / editing / rejected / done
```

---

## 3. 研究团队（子图 + 并行）

```python
class ResearchState(TypedDict):
    topic: str
    web_results: str
    doc_results: str
    arxiv_results: str
    summary: str

def web_node(s):
    return {"web_results": f"假装从 web 搜到 {s['topic']} 资料"}

def doc_node(s):
    return {"doc_results": f"内部文档 关于 {s['topic']}: ..."}

def arxiv_node(s):
    return {"arxiv_results": f"arxiv: {s['topic']} 论文 3 篇"}

def summarize_research(s):
    text = "\n".join([s["web_results"], s["doc_results"], s["arxiv_results"]])
    out = ChatOpenAI(model=MODEL).invoke(f"整合摘要：{text}").content
    return {"summary": out}

r = StateGraph(ResearchState)
r.add_node("web", web_node)
r.add_node("doc", doc_node)
r.add_node("arxiv", arxiv_node)
r.add_node("summarize", summarize_research)
r.add_edge(START, "web")
r.add_edge(START, "doc")
r.add_edge(START, "arxiv")
r.add_edge("web", "summarize")
r.add_edge("doc", "summarize")
r.add_edge("arxiv", "summarize")
r.add_edge("summarize", END)
research_team = r.compile()
```

三路检索并行跑完，再汇总成一份摘要。

---

## 4. 写作团队（Send 动态 fan-out）

```python
from langgraph.types import Send

SECTIONS = ["概述", "核心特性", "与其他框架对比", "适用场景"]

class SectionInput(TypedDict):
    topic: str
    research: str
    section_title: str

def section_writer(inp: SectionInput):
    msg = f"主题：{inp['topic']}\n研究资料：{inp['research']}\n请写'{inp['section_title']}'一节，120 字以内。"
    out = ChatOpenAI(model=MODEL, temperature=0.4).invoke(msg).content
    return {"sections": [(inp["section_title"], out)]}   # sections 的 reducer 是 add，并行结果自动合并
```

`Send(节点名, 私有输入)` 让同一个节点带着不同的输入并行跑 N 份。`section_writer` 收到的是 `Send` 里给它的 `SectionInput`，不是主 state。如果把它当普通节点、用普通边连过去，就会报 `KeyError: 'section_title'`。`Send` 必须由条件边函数返回：

```python
def after_research_review(s: MainState):
    if s["stage"] != "writing":
        return "research"                       # 研究被驳回，重做
    return [Send("section_writer", {"topic": s["topic"], "research": s["research"], "section_title": sec})
            for sec in SECTIONS]
```

---

## 5. HITL 审批点（interrupt）

```python
from langgraph.types import interrupt

def review_research(s: MainState):
    ans = interrupt({"stage": "research_done", "summary": s["research"]})   # 在这里暂停整张图
    return {"stage": "writing" if ans == "yes" else "init"}

def review_draft(s: MainState):
    preview = {t: c[:60] for t, c in s["sections"]}
    ans = interrupt({"stage": "draft_done", "preview": preview})
    return {"stage": "editing" if ans == "yes" else "rejected"}
```

`interrupt` 需要 checkpointer。恢复时调用 `app.invoke(Command(resume="yes"), config)`，`interrupt(...)` 的返回值就是 `"yes"`。恢复时这个节点会从头重新执行，所以 `interrupt` 之前不要放有副作用的代码。

---

## 6. 编辑节点 + 完整 graph

```python
def edit(s: MainState):
    ordered = sorted(s["sections"], key=lambda x: SECTIONS.index(x[0]))
    draft = "\n\n".join(f"## {t}\n{c}" for t, c in ordered)
    polished = ChatOpenAI(model=MODEL).invoke(f"对以下报告整体润色，保持 markdown 标题不变：\n{draft}").content
    return {"final": polished, "stage": "done"}

def call_research(s: MainState):
    out = research_team.invoke({"topic": s["topic"]})   # 子图 schema 不同，包一层函数转换
    return {"research": out["summary"]}

g = StateGraph(MainState)
g.add_node("research", call_research)
g.add_node("review_research", review_research)
g.add_node("section_writer", section_writer)
g.add_node("review_draft", review_draft)
g.add_node("edit", edit)

g.add_edge(START, "research")
g.add_edge("research", "review_research")
g.add_conditional_edges("review_research", after_research_review, ["section_writer", "research"])
g.add_edge("section_writer", "review_draft")    # 所有并行 worker 结束后才进入审稿
g.add_conditional_edges("review_draft", lambda s: "edit" if s["stage"] == "editing" else END,
                        {"edit": "edit", END: END})
g.add_edge("edit", END)
```

这里不需要 supervisor 节点：它要做的只是看 research、sections、final 有没有值，依次往下转发，直接用边表达同样的顺序更简单。如果你的流程确实需要由模型决定下一步派哪个 agent，也别再用 `langgraph-supervisor` 库了：它的仓库已经归档，官方建议改成把子 agent 包装成工具、由主 agent 调用，做法见 LangGraph 第 08 篇。

---

## 7. 运行

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

with SqliteSaver.from_conn_string("./research.db") as memory:
    app = g.compile(checkpointer=memory)
    cfg = {"configurable": {"thread_id": "research-001"}}

    out = app.invoke({"topic": "LangGraph", "sections": [], "stage": "init"}, config=cfg)
    print("【审核 research】", out["__interrupt__"][0].value["summary"][:120])

    out = app.invoke(Command(resume="yes"), config=cfg)          # 批准研究 → 并行写作 → 暂停审稿
    print("【审核 draft】", out["__interrupt__"][0].value)

    final = app.invoke(Command(resume="yes"), config=cfg)        # 批准初稿 → 编辑润色
    print(final["final"])
```

在 langgraph 1.x 里，`invoke` 遇到 `interrupt` 会正常返回，中断信息放在结果的 `__interrupt__` 键里。

---

## 8. 接 LangSmith / Studio

设上 `LANGSMITH_TRACING=true` 就能看到包括子图在内的完整 trace。用 `langgraph dev` 打开 Studio 调试时，还可以中途改某个节点的 state，再 resume。

---

## 9. 工程清单

- [ ] 检索工具接入真实的 Tavily / arxiv API
- [ ] research summary 太长时做摘要
- [ ] 章节数量根据 topic 动态决定
- [ ] HITL 接入 Slack bot，不用打开浏览器就能审批
- [ ] 限速：max_concurrency
- [ ] 评估：用 LangSmith dataset，定期跑回归
- [ ] 生产环境换成 Postgres Checkpointer

---

## 10. 本章 demo

[`demos/langgraph/project_research_team.py`](../../demos/langgraph/project_research_team.py)
