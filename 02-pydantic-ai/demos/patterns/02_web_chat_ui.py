"""
02_web_chat_ui.py
=================
FastAPI + Pydantic AI 最小聊天 UI 后端，包含：
  * GET /         —— 一个最简单的 HTML 页面（EventSource 流式渲染）
  * GET /chat     —— SSE 接口，token 增量 + 工具调用事件都流式吐出
  * 一个示例工具 fake_search 演示前端 tool_call/tool_result 渲染

没有 OPENAI_API_KEY 时自动用 TestModel，仍能看到事件流（但内容是占位文本）。

启动：
    pip install fastapi uvicorn[standard]
    uvicorn demos.patterns.02_web_chat_ui:app --reload
    # 浏览器打开 http://127.0.0.1:8000/

或直接：
    python demos/patterns/02_web_chat_ui.py
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

USE_TEST_MODEL = not os.getenv("OPENAI_API_KEY")
MODEL_NAME = "openai:gpt-4o-mini"

agent = Agent(
    MODEL_NAME,
    system_prompt=(
        "你是中文助手。当用户的问题涉及最新信息时，调用 fake_search 工具，"
        "把结果用一两句话总结后回复。"
    ),
)


@agent.tool
async def fake_search(ctx: RunContext, query: str) -> str:
    """伪搜索工具，返回固定结果用于演示工具调用事件。

    Args:
        query: 搜索关键词。
    """
    return f"[fake_search] 关于 {query!r} 的结果：今天是晴天。"


# ---------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    if USE_TEST_MODEL:
        print("[!] OPENAI_API_KEY 未设置，使用 TestModel。")
    yield


app = FastAPI(lifespan=lifespan)


INDEX_HTML = """\
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Pydantic AI Chat</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 720px; margin: 2em auto; padding: 0 1em; }
  #log { white-space: pre-wrap; min-height: 200px; border: 1px solid #ddd; padding: 1em; border-radius: 6px; }
  .tool { color: #888; font-style: italic; }
  .err  { color: #c00; }
  input { width: 80%; padding: 6px; }
  button { padding: 6px 14px; }
</style>
</head>
<body>
  <h1>Pydantic AI 流式聊天 Demo</h1>
  <div id="log"></div>
  <p>
    <input id="q" placeholder="问点什么..." value="搜一下今天天气怎么样">
    <button onclick="ask()">发送</button>
  </p>
<script>
function ask() {
  const log = document.getElementById('log');
  const q = document.getElementById('q').value;
  log.textContent = '';
  const es = new EventSource('/chat?q=' + encodeURIComponent(q));
  es.onmessage = (e) => {
    if (e.data === '[DONE]') { es.close(); return; }
    let payload;
    try { payload = JSON.parse(e.data); } catch { log.textContent += e.data; return; }
    if (payload.type === 'text_delta') {
      log.append(payload.delta);
    } else if (payload.type === 'tool_call') {
      const span = document.createElement('div');
      span.className = 'tool';
      span.textContent = `调用工具: ${payload.name}(${JSON.stringify(payload.args)})`;
      log.appendChild(span);
    } else if (payload.type === 'tool_result') {
      const span = document.createElement('div');
      span.className = 'tool';
      span.textContent = `工具结果: ${payload.content}`;
      log.appendChild(span);
    } else if (payload.type === 'error') {
      const span = document.createElement('div');
      span.className = 'err';
      span.textContent = `错误: ${payload.message}`;
      log.appendChild(span);
    }
  };
  es.onerror = () => es.close();
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/chat")
async def chat(q: str) -> StreamingResponse:
    """SSE 流式接口，吐出 text_delta / tool_call / tool_result 事件。"""

    async def gen():
        # 延迟导入，避免顶层导入失败的时候 fastapi 整个挂掉
        from pydantic_ai.messages import (
            PartStartEvent,
            PartDeltaEvent,
            TextPart,
            TextPartDelta,
            ToolCallPart,
            FunctionToolResultEvent,
        )

        run_agent = agent
        cm = run_agent.override(model=TestModel()) if USE_TEST_MODEL else _nullctx()
        try:
            with cm:
                async with run_agent.iter(q) as run:
                    async for node in run:
                        # 模型生成阶段
                        if Agent.is_model_request_node(node):
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    if isinstance(ev, PartStartEvent):
                                        if isinstance(ev.part, ToolCallPart):
                                            yield _sse({
                                                "type": "tool_call",
                                                "name": ev.part.tool_name,
                                                "args": ev.part.args,
                                            })
                                        elif isinstance(ev.part, TextPart) and ev.part.content:
                                            yield _sse({"type": "text_delta", "delta": ev.part.content})
                                    elif isinstance(ev, PartDeltaEvent):
                                        if isinstance(ev.delta, TextPartDelta) and ev.delta.content_delta:
                                            yield _sse({
                                                "type": "text_delta",
                                                "delta": ev.delta.content_delta,
                                            })
                        # 工具执行阶段
                        elif Agent.is_call_tools_node(node):
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    if isinstance(ev, FunctionToolResultEvent):
                                        yield _sse({
                                            "type": "tool_result",
                                            "content": str(ev.result.content),
                                        })
        except Exception as exc:
            yield _sse({"type": "error", "message": repr(exc)})
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # 告诉 Nginx 别 buffer
        },
    )


@asynccontextmanager
async def _nullctx():
    yield


# ---------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
