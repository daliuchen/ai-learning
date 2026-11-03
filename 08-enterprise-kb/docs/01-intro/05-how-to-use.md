# EKB 05：如何使用本手册

> 这本手册是一条连续的主线，不是知识点字典。建议按章节顺序读，因为每一章都建立在上一章的产出上。如果你只想解决某个具体问题，比如检索质量上不去，也可以直接跳到对应章节，下面有一张索引。

---

## 1. 两种读法

### 读法 A：从头跟到尾（推荐）

把它当成一个项目跟着做。每章结束，你手里的系统都比上一章更完整，而且能运行：

```
01 想清楚做什么
02 设计需求和数据模型
03 定下技术栈
04 搭好评估标尺  ← 从这里开始有代码
05 数据进库
06 端到端跑通（MVP！）
07 检索变准
08 加上权限
09 做出能用的界面
10 推上线
```

### 读法 B：带着问题来

如果你已经在做类似的项目，只是卡在某一处，可以直接查：

| 你的问题 | 去哪看 |
|----------|------|
| 该不该上向量库，上哪个 | [向量库选型](../03-selection/02-vector-db-pgvector.md) |
| 怎么衡量 RAG 好不好 | [评估](../04-eval/01-why-eval-first.md)整章 |
| 分块怎么切才对 | [语义分块](../05-ingest/03-semantic-chunking.md) |
| 检索回来一堆不相关的 | [检索增强](../07-retrieval/01-retrieval-is-key.md)整章 |
| 模型老编答案 | [答不出就承认](../06-basic-rag/04-say-i-dont-know.md) |
| 权限怎么做 | [权限](../08-permission/01-permission-is-devil.md)整章 |
| 怎么降本提速 | [生产化](../10-production/01-prompt-caching.md)整章 |

---

## 2. 环境准备

跟着做需要下面这些，第一次配好后全程通用：

```bash
# 1. Python 依赖
pip install -r requirements.txt

# 2. 一个带 pgvector 的 Postgres（最简：用 Docker）
docker run -d --name ekb-pg \
  -e POSTGRES_PASSWORD=ekb -p 5432:5432 \
  pgvector/pgvector:pg17

# 3. 在库里启用扩展
psql postgresql://postgres:ekb@localhost:5432/postgres \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 4. 配一个模型 key（二选一）
export OPENAI_API_KEY=sk-...
# 或
export ANTHROPIC_API_KEY=sk-ant-...
```

前端章节（09）另外需要 Node.js（20 以上，具体以所用 Next.js 版本的要求为准），到那一章再装。

---

## 3. 代码组织约定

整本手册的代码会逐步拼成一个项目，目录结构如下（跟着做时建议照这个建）：

```
ekb/
├── ingest/        # 05 章：解析、分块、写库
│   ├── parse.py
│   ├── chunk.py
│   └── load.py
├── retrieve/      # 06-07 章：检索、混合、rerank
│   ├── vector.py
│   ├── hybrid.py
│   └── rerank.py
├── generate/      # 06 章：Pydantic AI 生成 + 引用
│   └── answer.py
├── permission/    # 08 章：ACL 过滤
│   └── acl.py
├── eval/          # 04 章：测试集 + 打分
│   ├── testset.jsonl
│   └── run_eval.py
├── api/           # 09 章：FastAPI + SSE
│   └── main.py
└── db.py          # 数据库连接 + schema
```

每章的代码片段都会注明属于哪个文件，方便你拼起来。

---

## 4. 阅读约定（和整个系列一致）

- 每篇的结构大致相同：开头导语、概念、最小代码、进阶、生产建议、常见坑表格、下一步。
- 代码尽量能单独运行：import 写全，关键 API 写完整路径。
- 错误示范会在代码注释里标出 `# ❌ 错误` / `# ✅ 正确`。
- 用到前 7 本内容的地方会给链接，方便回看零件细节。

---

## 5. 给不同读者的建议

| 你是 | 建议 |
|------|------|
| 学完前 7 本，想串起来 | 读法 A，从头跟做，重点看零件怎么拼成系统 |
| 在公司真要做知识库 | 读法 A，但把 demo 数据换成你们的真实文档 |
| 已经在做、卡在某处 | 读法 B，直接看对应章节 |
| 只想了解架构 | 读 01 到 03 章，再看各篇开头的导语 |

---

## 下一步

正式开工，先从需求拆解开始，看看一个企业知识库的真实需求是什么：

→ [企业知识库的真实需求](../02-design/01-real-requirements.md)
