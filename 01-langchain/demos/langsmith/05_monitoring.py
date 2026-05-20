"""
05_monitoring.py
================
上报 user feedback + 拉取近期 run 聚合统计
"""
import os
import uuid
from datetime import datetime, timedelta

from dotenv import load_dotenv

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import Client

load_dotenv()
if not os.getenv("LANGSMITH_API_KEY"):
    raise SystemExit("请先配置 LANGSMITH_API_KEY")

client = Client()

chain = (
    ChatPromptTemplate.from_template("简短回答：{q}")
    | ChatOpenAI(model="gpt-4o-mini")
    | StrOutputParser()
)


def main():
    run_id = str(uuid.uuid4())
    ans = chain.invoke({"q": "LCEL 是什么？"}, config={"run_id": run_id})
    print("answer:", ans[:120])
    client.create_feedback(
        run_id=run_id,
        key="user_thumbs",
        score=1,
        comment="useful",
        feedback_source_type="api",
    )
    print(f"feedback uploaded for run {run_id}")

    project = os.getenv("LANGSMITH_PROJECT") or "default"
    runs = list(client.list_runs(
        project_name=project,
        start_time=datetime.utcnow() - timedelta(hours=1),
        is_root=True,
        limit=20,
    ))
    print(f"\n近 1h root runs in '{project}': {len(runs)}")
    if runs:
        tot_in = sum(r.prompt_tokens or 0 for r in runs)
        tot_out = sum(r.completion_tokens or 0 for r in runs)
        print(f"  in_tokens={tot_in} out_tokens={tot_out}")


if __name__ == "__main__":
    main()
