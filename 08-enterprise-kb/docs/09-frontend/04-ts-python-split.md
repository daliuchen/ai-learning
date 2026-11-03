# EKB 46：TypeScript 前端与 Python 后端的拆分

> 产品层用 TypeScript 和 Next.js，AI 层用 Python 和 FastAPI，两者之间靠一份清楚的 API 契约连接。这样拆是为了让每一层都用上各自最成熟的生态。这篇讲职责边界、契约怎么设计，以及本地怎么把两个服务跑起来。

---

## 1. 职责边界

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

TS 负责用户看到什么、怎么交互；Python 负责 AI 怎么推理、数据怎么算。业务逻辑都放在 Python，前端尽量薄。

---

## 2. 为什么这么拆

| 这一层 | 选这门语言的原因 |
|--------|----------------|
| AI 逻辑 → Python | embedding、rerank、Pydantic AI 的生态都在 Python，还能直接复用前几本手册的代码 |
| 前端 → TS | 流式 UI、组件化、类型安全，React 生态最成熟 |

硬要统一成一门语言的话，全用 Python 前端体验会吃亏，全用 TS 则 AI 生态吃亏。两门语言各管一层，在实际生产中很常见，这种工程模式本身也值得学（见 [技术栈与选型理由](../01-intro/03-tech-stack-overview.md)）。

---

## 3. API 契约

前后端能解耦，靠的是一份清楚的 API 契约。本项目只有几个端点：

```
GET  /api/ask?q=...           → SSE 流（token / citations / done）
POST /api/feedback            → 记录反馈
GET  /api/docs                → 文档列表
POST /api/docs                → 上传文档
PUT  /api/docs/{id}/roles     → 改可见角色
```

契约上要注意两点。一是类型对齐：Python 用 Pydantic 定义响应模型，前端用 TS interface 对应，两边的 `Citation`、`Answer` 结构必须一致。二是尽量自动生成：FastAPI 自带 OpenAPI schema，可以用工具从它生成 TS 类型，免得手写两套还对不上。

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

## 4. 鉴权和角色怎么跨层传递

权限是否正确，取决于角色是否来自可信来源（见 [ACL 模型](../08-permission/02-acl-model.md)）。拆成两层之后，链路是这样的：

```
1. 用户登录 → TS 层拿到 session token（含身份）
2. TS 调 Python API 时，带上 token（header）
3. Python 层验证 token，解析出可信的 role
4. role 进入 retrieve(question, roles=...) 做权限过滤
```

绝不能让前端把 `role=hr` 直接作为查询参数传过来。那相当于把权限交给了客户端，改个参数就越权了。整条链路的安全靠的是 token 在服务端验证。

---

## 5. 本地跑起来

两个服务，两个端口，前端把请求代理到后端：

```bash
# 终端 1：Python AI 服务
uvicorn api.main:app --port 8000 --reload

# 终端 2：Next.js 前端
cd web && npm run dev          # 默认 3000
```

用 Next.js 的 rewrites 把 `/api/*` 代理到 `localhost:8000`：

```js
// next.config.js
module.exports = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' }]
  },
}
```

这样前端代码里写 `/api/ask`，请求会自动转发到 Python 服务，开发起来和单体应用一样顺手。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 业务逻辑写进前端 | 难复用、难测，也不安全 | 逻辑都放在 Python |
| 前后端类型各写一套 | 对不上，运行时出错 | 从 OpenAPI 生成 TS 类型 |
| 角色作为查询参数传 | 越权 | 在服务端验证 token |
| 两层之间没有契约 | 改一边，另一边就崩 | 定好 API 契约 |
| SSE 没配代理 | 跨域或者连不上 | 用 rewrites 代理 |

---

## 下一步

系统功能齐了，进入最后一章：把它推向生产。先从降本开始：

→ [Prompt Caching](../10-production/01-prompt-caching.md)
