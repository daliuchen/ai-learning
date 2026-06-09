# EKB 43：问答页——流式回答的前端

> **一句话**：问答页的核心体验是**流式**——答案逐字蹦出，而不是转圈几秒后整段出现。后端用 SSE 推送 token，前端用 `EventSource`/fetch stream 接收并实时渲染。本篇给出 FastAPI 的 SSE 端点和 Next.js 的接收渲染。

---

## 1. 为什么要流式

```
❌ 非流式：用户提问 → 转圈 5 秒 → 整段答案突然出现
   → 体感慢，不知道系统在不在干活

✅ 流式：用户提问 → 0.5 秒后第一个字蹦出 → 逐字展开
   → 体感快，首字延迟（TTFT）才是用户感知的「快慢」
```

流式不改变总耗时，但**大幅改善体感**——首字出现就证明系统在工作。对话式产品几乎是标配。

---

## 2. 后端：FastAPI 的 SSE 端点

用 `sse-starlette` 把生成过程逐 token 推出去：

```python
# api/main.py
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from generate.pipeline import answer_question_stream

app = FastAPI()

@app.get("/api/ask")
async def ask(request: Request, q: str, role: str = "all"):
    async def event_generator():
        async for event in answer_question_stream(q, role):
            if await request.is_disconnected():
                break
            yield event          # {"event": "token"/"citations"/"done", "data": ...}
    return EventSourceResponse(event_generator())
```

生成侧用 Pydantic AI 的流式 API，先推答案 token，最后推引用和结束信号：

```python
async def answer_question_stream(question, role):
    chunks = await retrieve(question, roles=[role], k=4)
    if not chunks:
        yield {"event": "token", "data": "未在知识库中找到相关信息。"}
        yield {"event": "done", "data": json.dumps({"found": False})}
        return
    async with answer_agent.run_stream(build_prompt(question, chunks)) as stream:
        async for text in stream.stream_text(delta=True):
            yield {"event": "token", "data": text}        # 逐段文本
        answer = await stream.get_output()
    citations = build_citations(answer, chunks)
    yield {"event": "citations", "data": json.dumps(citations)}
    yield {"event": "done", "data": json.dumps({"found": answer.found})}
```

注意顺序：**先流答案文本，结构化的引用最后一次性给**——因为引用要等模型输出完才确定。

---

## 3. 前端：Next.js 接收并渲染

用 fetch + ReadableStream 读 SSE（比 `EventSource` 灵活，支持自定义 header 传角色/鉴权）：

```tsx
// app/components/Chat.tsx
'use client'
import { useState } from 'react'

export function Chat() {
  const [answer, setAnswer] = useState('')
  const [citations, setCitations] = useState<Citation[]>([])

  async function ask(q: string) {
    setAnswer(''); setCitations([])
    const res = await fetch(`/api/ask?q=${encodeURIComponent(q)}`)
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const line of decoder.decode(value).split('\n\n')) {
        const ev = parseSSE(line)
        if (ev?.event === 'token') setAnswer((a) => a + ev.data)   // 逐字追加
        if (ev?.event === 'citations') setCitations(JSON.parse(ev.data))
      }
    }
  }
  return (
    <div>
      <AnswerView text={answer} />
      <CitationList items={citations} />
    </div>
  )
}
```

`setAnswer((a) => a + ev.data)` 就是流式的精髓——每来一段就追加，React 实时重渲染，字就「蹦」出来了。

---

## 4. 几个体验细节

| 细节 | 做法 |
|------|------|
| 首字前的等待 | 显示「正在检索…」骨架/光标 |
| Markdown 渲染 | 答案可能含列表/加粗，边流边渲染 markdown |
| 中断 | 用户可点「停止」，前端 abort fetch，后端 `is_disconnected` 感知 |
| 错误 | 流中断/超时要有 fallback 提示 |
| 引用先占位 | 答案流完前，引用区显示「整理引用中」 |

流式渲染 markdown 有个坑：半截的 markdown 语法（如未闭合的 `**`）会闪烁。可以用容错的流式 markdown 渲染器，或在 `done` 后做一次最终规范渲染。

---

## 5. 角色怎么传

问答要带提问者角色（权限要用）。**角色绝不能由前端明文指定**（[39 篇](../08-permission/02-acl-model.md)），而是：

```
前端请求带 session token（登录态）
  → 后端从 token 解析出可信的用户角色
  → 再传给 retrieve(question, roles=...)
```

本教学项目可简化为「后端从一个 mock session 取角色」，但要在代码里**明确标注「生产中此处从鉴权中间件取」**，避免给人「角色能前端传」的错误示范。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 非流式整段返回 | 体感慢 | SSE 流式 |
| 引用和 token 混着流 | 引用要等输出完才准 | 文本先流，引用最后给 |
| 前端传角色明文 | 越权 | 角色从服务端 token 解析 |
| 流式 markdown 不容错 | 半截语法闪烁 | 容错渲染或 done 后规范化 |
| 不处理断流/超时 | 卡死无反馈 | fallback 提示 + 可中断 |

---

## 下一步

答案有了，把引用做成能点回原文的卡片：

→ [02-citation-clickback](./02-citation-clickback.md)
