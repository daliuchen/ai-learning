"""
11_functional.py
================
Functional API：@entrypoint + @task 演示
"""
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.func import entrypoint, task

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini", temperature=0.4)


@task
def joke(topic: str) -> str:
    return model.invoke(f"讲一个关于 {topic} 的短笑话").content


@task
def poem(topic: str) -> str:
    return model.invoke(f"写一首关于 {topic} 的两行诗").content


@task
def combine(joke_text: str, poem_text: str) -> str:
    return f"--- 笑话 ---\n{joke_text}\n\n--- 诗 ---\n{poem_text}"


@entrypoint(checkpointer=MemorySaver())
def workflow(topic: str) -> str:
    f1 = joke(topic)
    f2 = poem(topic)
    return combine(f1.result(), f2.result()).result()


def main():
    cfg = {"configurable": {"thread_id": "t-cat"}}
    print("=== invoke ===")
    print(workflow.invoke("猫", config=cfg))

    print("\n=== stream updates ===")
    for ev in workflow.stream("狗", config={"configurable": {"thread_id": "t-dog"}}, stream_mode="updates"):
        print(ev)


if __name__ == "__main__":
    main()
