"""
05_monitoring.py
================
上报 user feedback + 拉取近期 run 聚合统计（需要 LANGSMITH_API_KEY 和 tracing 开启）。
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import Client

load_dotenv()

MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")

chain = (
    ChatPromptTemplate.from_template("简短回答：{q}")
    | ChatOpenAI(model=MODEL)
    | StrOutputParser()
)


def main():
    run_id = uuid.uuid4()
    ans = chain.invoke({"q": "LCEL 是什么？"}, config={"run_id": run_id})
    print("answer:", ans[:120])

    if not (os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGSMITH_TRACING", "").lower() == "true"):
        print("未配置 LANGSMITH_API_KEY / LANGSMITH_TRACING=true，跳过 feedback 与统计")
        return

    client = Client()
    client.flush() if hasattr(client, "flush") else None  # 确保 trace 已上传再评论
    client.create_feedback(run_id=run_id, key="user_thumbs", score=1, comment="useful",
                           feedback_source_type="api")
    print(f"feedback uploaded for run {run_id}")

    project = os.getenv("LANGSMITH_PROJECT") or "default"
    runs = list(client.list_runs(
        project_name=project,
        start_time=datetime.now(timezone.utc) - timedelta(hours=1),
        is_root=True,
        limit=20,
    ))
    print(f"\n近 1h root runs in '{project}': {len(runs)}")
    if runs:
        print(f"  in_tokens={sum(r.prompt_tokens or 0 for r in runs)} "
              f"out_tokens={sum(r.completion_tokens or 0 for r in runs)}")


if __name__ == "__main__":
    main()
