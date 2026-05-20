# 实战项目 2：多 Agent 研究助手（Supervisor + 子图 + Map-Reduce）

> **一句话**：构建一个三层架构的研究助手——顶层 Supervisor → 研究团队 / 写作团队 → 各团队内并行 Agent。涵盖本系列所有 LangGraph 进阶能力：Supervisor、子图、Send/Map-Reduce、HITL、Streaming、LangSmith Trace。

---

## 1. 需求

输入一个研究主题（如"LangGraph 的最佳实践"），输出一份结构化研究报告：

```
1. 概述（150 字）
2. 核心特性
   - 特性 A：xxx
   - 特性 B：xxx
3. 与其他框架对比
4. 适用场景
5. 参考资料
```

要求：

- 研究团队**并行**做多种检索（web + 文档库 + arxiv）
- 写作团队**多个作家**各写一节，**并行**进行
- 最后**编辑** Agent 整合并润色
- 用户可以在"研究完成"和"初稿完成"两个节点**审核**
- 全程可在 LangGraph Studio 看到执行

---

## 2. 架构

```
┌──────────────────────────────────────────────┐
│              Top Supervisor                  │
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
    next: str                           # supervisor 路由
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
    out = ChatOpenAI(model="gpt-4o-mini").invoke(f"整合摘要：{text}").content
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

并行三种检索，最后汇总。

---

## 4. 写作团队（Send 动态 fan-out）

```python
SECTIONS = ["概述", "核心特性", "与其他框架对比", "适用场景"]

class SectionState(TypedDict):
    topic: str
    research: str
    section_title: str

def section_writer(s: SectionState):
    msg = f"主题：{s['topic']}\n研究资料：{s['research']}\n请写'{s['section_title']}'一节，300 字以内。"
    out = ChatOpenAI(model="gpt-4o-mini").invoke(msg).content
    return {"sections": [(s["section_title"], out)]}

def fan_out_sections(state):
    return [
        Send("section_writer", {
            "topic": state["topic"],
            "research": state["research"],
            "section_title": sec,
        }) for sec in SECTIONS
    ]
```

每个 section 一个 worker 并行写。

---

## 5. 编辑 + Supervisor

```python
def edit(state):
    ordered = sorted(state["sections"], key=lambda x: SECTIONS.index(x[0]))
    draft = "\n\n".join(f"## {t}\n{c}" for t, c in ordered)
    polished = ChatOpenAI(model="gpt-4o-mini").invoke(
        f"对以下报告进行整体润色：\n{draft}"
    ).content
    return {"final": polished}

def supervisor(state):
    if not state.get("research"): return {"next": "research"}
    if not state.get("sections"): return {"next": "write"}
    if not state.get("final"):    return {"next": "edit"}
    return {"next": "done"}

def route(state):
    n = state["next"]
    if n == "research": return "research"
    if n == "write":    return "fan_out"   # Send
    if n == "edit":     return "edit"
    return END
```

---

## 6. HITL 审批点

```python
from langgraph.types import interrupt

def review_research(state):
    answer = interrupt({"stage": "research_done", "summary": state["research"]})
    if answer != "yes":
        return {"research": ""}    # 清空，supervisor 会重试
    return {}

def review_draft(state):
    answer = interrupt({"stage": "draft_done", "draft": state["sections"]})
    if answer != "yes":
        return {"sections": []}
    return {}
```

把这两个 review 节点插在团队后面。

---

## 7. 完整 graph

```python
def call_research(state):
    out = research_team.invoke({"topic": state["topic"]})
    return {"research": out["summary"]}

main = StateGraph(MainState)
main.add_node("supervisor", supervisor)
main.add_node("research", call_research)
main.add_node("review_research", review_research)
main.add_node("section_writer", section_writer)   # 用 Send 调用
main.add_node("review_draft", review_draft)
main.add_node("edit", edit)

main.add_edge(START, "supervisor")
main.add_conditional_edges("supervisor", route, {
    "research": "research",
    "fan_out": "review_research",     # 先审 research，再 fan_out
    "edit": "review_draft",            # 先审 draft
    END: END,
})
main.add_edge("research", "supervisor")
main.add_conditional_edges("review_research", lambda s: "fan_out" if s.get("research") else "supervisor",
    {"fan_out": "section_writer", "supervisor": "supervisor"})

# Send 是从 conditional_edges 触发的，我们用 supervisor → review → "fan_out" 节点 → 把 Send 当 path
# 这里用一个伪 dispatcher node
def dispatcher(state):
    return state
main.add_node("fan_out_dispatch", dispatcher)
main.add_conditional_edges("section_writer", lambda s: "review_draft", {"review_draft": "review_draft"})
main.add_conditional_edges("review_draft", lambda s: "edit" if s.get("sections") else "supervisor",
    {"edit": "edit", "supervisor": "supervisor"})
main.add_edge("edit", "supervisor")

# Send 路径：从 review_research 出来后，要 fan_out → section_writer
# LangGraph 推荐：用一个 conditional_edges 输出 Send 列表
main.add_conditional_edges("review_research", fan_out_sections, ["section_writer"])
```

> 实际 demo 代码做了细节简化与组织，参考 `demos/langgraph/project_research_team.py`。

---

## 8. 运行 + Streaming

```python
with SqliteSaver.from_conn_string("./research.db") as memory:
    app = main.compile(checkpointer=memory)
    cfg = {"configurable": {"thread_id": "research-001"}}

    print("\n>>> 第一次启动")
    out = app.invoke({"topic": "LangGraph 最佳实践", "sections": []}, config=cfg)
    if "__interrupt__" in out:
        print("【审核 research】", out["__interrupt__"][0].value)
    final = app.invoke(Command(resume="yes"), config=cfg)
    if "__interrupt__" in final:
        print("【审核 draft】", final["__interrupt__"][0].value)
    final = app.invoke(Command(resume="yes"), config=cfg)
    print("\n=== 最终报告 ===\n", final["final"])
```

---

## 9. 接 LangSmith / Studio

- 加 `LANGSMITH_TRACING=true` 即可看到完整 trace（含子图）
- `langgraph dev` 用 Studio 调试：可以中途修改某节点 state 再 resume

---

## 10. 工程清单

- [ ] tools 真接 Tavily / arxiv API
- [ ] research summary 太长时做摘要
- [ ] section 数量动态（从 topic 推断）
- [ ] HITL 接 Slack bot（不开浏览器审）
- [ ] 限速：max_concurrency
- [ ] 评估：用 LangSmith dataset，定期跑回归
- [ ] Postgres Checkpointer 上生产

---

## 11. 总结

完整跑通这个项目，你就把本系列三大块技术：

1. **LangChain**：模型、工具、检索
2. **LangSmith**：可观测、评估
3. **LangGraph**：状态机、HITL、多 Agent、子图、Map-Reduce

全部应用了一遍。可以把这个 demo 当 starter，按业务场景改造成自己的 Agent 系统。

---

## 12. 本章 demo

[`demos/langgraph/project_research_team.py`](../../demos/langgraph/project_research_team.py)
