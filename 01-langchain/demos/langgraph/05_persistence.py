"""
05_persistence.py
=================
Checkpointer + Store：多轮 + 跨会话偏好。
"""
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.store.memory import InMemoryStore

load_dotenv()


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


model = ChatOpenAI(model="gpt-4o-mini", temperature=0)


def chat(state: State, *, store, config):
    user = config["configurable"]["user_id"]
    item = store.get(("users", user), "favorite_color")
    pref = item.value["value"] if item else "无"
    msgs = [("system", f"用户喜欢的颜色: {pref}")] + state["messages"]
    return {"messages": [model.invoke(msgs)]}


def build_app():
    g = StateGraph(State)
    g.add_node("chat", chat)
    g.add_edge(START, "chat")
    g.add_edge("chat", END)
    return g


def main():
    with SqliteSaver.from_conn_string(":memory:") as memory:
        store = InMemoryStore()
        store.put(("users", "u1"), "favorite_color", {"value": "蓝色"})

        app = build_app().compile(checkpointer=memory, store=store)
        cfg = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

        for q in [
            "推荐一双适合我喜欢颜色的鞋",
            "刚才推荐的鞋是什么颜色？",
        ]:
            ans = app.invoke({"messages": [("human", q)]}, config=cfg)["messages"][-1].content
            print(f"\nQ: {q}\nA: {ans}")

        print("\n=== 历史 checkpoints ===")
        for s in app.get_state_history(cfg):
            print(s.config["configurable"]["checkpoint_id"], "next=", s.next)


if __name__ == "__main__":
    main()
