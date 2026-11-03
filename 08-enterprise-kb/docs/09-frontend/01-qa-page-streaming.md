# EKB 43：流式回答的问答页

> 问答页的体验好不好，主要看是不是流式：答案一个字一个字地出来，而不是转几秒圈后整段出现。后端用 SSE 推送 token，前端用 `EventSource` 或 fetch stream 接收并实时渲染。这篇给出 FastAPI 的 SSE 端点和 Next.js 这边的接收与渲染。

---

## 1. 为什么要流式

```
❌ 非流式：用户提问 → 转圈 5 秒 → 整段答案突然出现
   → 体感慢，不知道系统在不在干活

✅ 流式：用户提问 → 0.5 秒后第一个字蹦出 → 逐字展开
   → 体感快，首字延迟（TTFT）才是用户感知的「快慢」
```

流式不会缩短总耗时，但体感好得多，第一个字出来，用户就知道系统在干活。对话式产品基本都这么做。

---

## 2. 后端：FastAPI 的 SSE 端点

用 `sse-starlette` 把生成过程按 token 推出去：

```python
# api/main.py
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from generate.pipeline import answer_question_stream

app = FastAPI()

@app.get("/api/ask")
async def ask(request: Request, q: str, role: str = "all"):
    # ⚠️ 教学简化：role 直接取自 query。生产中必须改为从鉴权中间件取（见第 5 节）
    async def event_generator():
        async for event in answer_question_stream(q, role):
            if await request.is_disconnected():
                break
            yield event          # {"event": "token"/"citations"/"done", "data": ...}
    return EventSourceResponse(event_generator())
```

生成这一侧用 Pydantic AI 的流式 API。V2 里有个坑必须知道：`stream_text()` 只能用于纯文本输出。我们的输出类型是结构化的 `Answer`，对它调用 `stream_text()` 会直接抛出 `UserError: stream_text() can only be used with text responses`。结构化输出要用 `stream_output()`，它每次产出一个截至当前已解析出的部分 `Answer`，我们取其中 `text` 字段新增的部分推给前端：

```python
import json
from pydantic_ai import Agent

async def answer_question_stream(question, role):
    chunks = await retrieve(question, roles=[role], k=4)
    if not chunks:
        yield {"event": "token", "data": "未在知识库中找到相关信息。"}
        yield {"event": "done", "data": json.dumps({"found": False})}
        return

    sent = ""                                   # 已经推给前端的文本
    async with answer_agent.run_stream(build_prompt(question, chunks)) as stream:
        async for partial in stream.stream_output():     # partial 是部分填充的 Answer
            delta = partial.text[len(sent):]             # 只推新增的那一截
            if delta:
                sent = partial.text
                yield {"event": "token", "data": delta}
        answer = await stream.get_output()               # 完整、校验过的 Answer
    citations = build_citations(answer, chunks)
    yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}
    yield {"event": "done", "data": json.dumps({"found": answer.found})}
```

几点说明：

- `stream_output()` 默认带一点防抖（`debounce_by=0.1` 秒），会把过密的更新合并，正好省掉前端不必要的渲染。需要拿到每一次更新时（比如写测试），传 `debounce_by=None`。
- 部分 `Answer` 里的 `text` 只会变长，所以用 `partial.text[len(sent):]` 切新增部分是安全的。`text` 还没解析出来时，那次部分结果校验不过，会被直接跳过、不会产出，所以把 `text` 放在 `Answer` 的第一个字段，流式效果最好。
- 只有想让模型输出纯文本、不要结构化引用时，才用 `output_type=str` 加 `stream_text(delta=True)`。我们需要引用和 `found`，所以走结构化输出。
- 流式时 `output_validator` 会对每个部分结果各调用一次，里面的校验要先判断 `ctx.partial_output`（见 [结构化生成](../06-basic-rag/02-structured-output.md)）。
- 用户点「停止」时，前端 abort 这次 fetch，后端通过 `request.is_disconnected()` 退出循环，`async with` 结束时会关掉这次模型流。

顺序上，答案文本先流式输出，结构化的引用最后一次性给出，因为引用要等模型输出完才能确定。

---

## 3. 前端：Next.js 接收并渲染

用 fetch 加 ReadableStream 读取 SSE。它比 `EventSource` 灵活，可以用自定义 header 传鉴权 token（角色由后端从 token 解析，见第 5 节）：

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
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })   // 一个 SSE 事件可能被拆在两次 read 里
      const parts = buf.split('\n\n')
      buf = parts.pop() ?? ''                           // 最后一段可能不完整，留到下次
      for (const line of parts) {
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

流式效果全靠 `setAnswer((a) => a + ev.data)` 这一行：每收到一段就追加，React 实时重新渲染，字就一个个出来了。

---

## 4. 几个体验细节

| 细节 | 做法 |
|------|------|
| 第一个字出来前的等待 | 显示「正在检索…」的骨架或光标 |
| Markdown 渲染 | 答案里可能有列表、加粗，边流式输出边渲染 markdown |
| 中断 | 用户可以点「停止」，前端 abort fetch，后端通过 `is_disconnected` 感知 |
| 错误 | 流中断或超时要有兜底提示 |
| 引用先占位 | 答案没流完之前，引用区显示「整理引用中」 |

流式渲染 markdown 有个坑：半截的 markdown 语法（比如还没闭合的 `**`）会让页面闪烁。可以用能容错的流式 markdown 渲染器，或者在收到 `done` 之后再做一次完整渲染。

---

## 5. 角色怎么传

问答请求要带上提问者的角色，权限过滤要用。角色绝不能由前端明文指定（见 [ACL 模型](../08-permission/02-acl-model.md)），正确的流程是：

```
前端请求带 session token（登录态）
  → 后端从 token 解析出可信的用户角色
  → 再传给 retrieve(question, roles=...)
```

教学项目可以简化成后端从一个 mock session 里取角色，但要在代码里写清楚「生产环境这里从鉴权中间件取」，别给读者留下角色可以由前端传的错误印象。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 非流式，整段返回 | 体感慢 | 用 SSE 流式输出 |
| 引用和 token 混在一起流 | 引用要等输出完才准确 | 先流文本，最后给引用 |
| 前端明文传角色 | 越权 | 从服务端 token 解析角色 |
| 流式 markdown 不容错 | 半截语法导致闪烁 | 用容错渲染器，或 done 后再完整渲染一次 |
| 不处理断流和超时 | 界面卡住、没有反馈 | 给兜底提示，并支持中断 |

---

## 下一步

答案有了，接下来把引用做成能点回原文的卡片：

→ [引用回链](./02-citation-clickback.md)
