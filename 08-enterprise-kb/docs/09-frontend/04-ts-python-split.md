# EKB 46：TS 壳 + Python 脑——前后端怎么拆

> **一句话**：产品层用 TypeScript/Next.js，AI 层用 Python/FastAPI，中间用一份清晰的 API 契约连接。这个拆分不是为了炫技，而是让每一层用各自最强的生态。本篇讲清职责边界、契约设计、以及本地怎么把两个服务跑起来。

---

## 1. 职责边界：谁管什么

```
┌─────────── TS 壳（Next.js）───────────┐
│ · 页面/路由/组件                        │
│ · 流式渲染、引用卡片、文档后台 UI         │
│ · 用户登录态 / session                  │
│ · 调用 AI 服务的 API                     │
└────────────────┬──────────────────────┘
                 │ HTTP / SSE（API 契约）
┌────────────────▼──────────────────────┐
│         Python 脑（FastAPI）            │
│ · 检索（向量/BM25/混合/rerank）           │
│ · 生成（Pydantic AI 结构化）             │
│ · 权限过滤、ingest、评估                  │
│ · 一切 AI/数据逻辑                       │
└────────────────────────────────────────┘
```

一句话分工：**TS 管「人看到什么、怎么交互」，Python 管「AI 怎么想、数据怎么算」。** 业务逻辑全在 Python，前端尽量薄。

---

## 2. 为什么这么拆

| 这一层 | 用它的语言因为 |
|--------|----------------|
| AI 逻辑 → Python | embedding/rerank/Pydantic AI 生态都在 Python，且能直接复用前 6 本手册的代码 |
| 前端 → TS | 流式 UI、组件、类型安全的前端体验，React 生态最成熟 |

如果硬要统一语言：全 Python 前端体验吃亏，全 TS 则 AI 生态吃亏。**各用所长**是真实生产里的常见架构——它本身就是个值得学的工程模式（[03 篇](../01-intro/03-tech-stack-overview.md)）。

---

## 3. API 契约：两层之间的合同

前后端解耦的关键是**清晰的 API 契约**。本项目就几个端点：

```
GET  /api/ask?q=...           → SSE 流（token / citations / done）
POST /api/feedback            → 记录反馈
GET  /api/docs                → 文档列表
POST /api/docs                → 上传文档
PUT  /api/docs/{id}/roles     → 改可见角色
```

契约要点：
- **类型对齐**：Python 用 Pydantic 定义响应模型，前端用 TS interface 对应。两边的 `Citation`、`Answer` 结构必须一致。
- **可以自动生成**：FastAPI 自带 OpenAPI schema，可以用工具生成 TS 类型，避免手写两套、对不上。

```python
# 后端 Pydantic 模型即契约
class Citation(BaseModel):
    doc_id: int
    title: str
    source_url: str | None
    sections: list[str]
```

```ts
// 前端对应（可由 OpenAPI 自动生成）
interface Citation {
  doc_id: number; title: string;
  source_url: string | null; sections: string[];
}
```

---

## 4. 鉴权与角色：跨层的关键

权限的正确性依赖「角色由可信来源决定」（[39 篇](../08-permission/02-acl-model.md)）。两层拆分后，这条线是这样的：

```
1. 用户登录 → TS 层拿到 session token（含身份）
2. TS 调 Python API 时，带上 token（header）
3. Python 层验证 token，解析出可信的 role
4. role 进入 retrieve(question, roles=...) 做权限过滤
```

**绝不能**让前端把 `role=hr` 直接当查询参数传——那等于把权限交给客户端，改个参数就越权。token 在服务端验证，是这条链的安全锚点。

---

## 5. 本地怎么跑起来

两个服务，两个端口，前端代理到后端：

```bash
# 终端 1：Python AI 服务
uvicorn api.main:app --port 8000 --reload

# 终端 2：Next.js 前端
cd web && npm run dev          # 默认 3000
```

前端把 `/api/*` 代理到 `localhost:8000`（Next.js rewrites）：

```js
// next.config.js
module.exports = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' }]
  },
}
```

这样前端代码里写 `/api/ask`，请求自动转发到 Python 服务，开发体验和单体一样顺。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 业务逻辑写进前端 | 难复用、难测、不安全 | 逻辑全在 Python |
| 前后端类型各写一套 | 对不上、运行时错 | 从 OpenAPI 生成 TS |
| 角色当查询参数传 | 越权 | token 服务端验证 |
| 两层耦合无契约 | 改一边崩一边 | 明确 API 契约 |
| SSE 没配代理 | 跨域/连不上 | rewrites 代理 |

---

## 下一步

系统功能完整了，进入最后一章——把它推向生产。先从降本开始：

→ [10-production/01-prompt-caching](../10-production/01-prompt-caching.md)
