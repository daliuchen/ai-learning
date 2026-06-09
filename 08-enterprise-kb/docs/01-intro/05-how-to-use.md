# EKB 05：如何使用本手册

> **一句话**：这本手册是**一条主线**，不是知识点字典——建议按章节顺序读，因为每一章都在上一章的产出上往前焊。但如果你只想解决某个具体问题（比如「检索质量上不去」），也可以直奔对应章节，本篇给你一张索引。

---

## 1. 两种读法

### 读法 A：从头跟到尾（推荐）

把它当成一个项目跟做。每章结束你都会得到一个**比上一章更完整、可运行**的系统：

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

已经在做类似项目、卡在某一处，直接查：

| 你的问题 | 直奔 |
|----------|------|
| 该不该上向量库 / 上哪个 | [03-selection/02-vector-db-pgvector](../03-selection/02-vector-db-pgvector.md) |
| 怎么衡量 RAG 好不好 | [04-eval](../04-eval/01-why-eval-first.md) 整章 |
| 分块怎么切才对 | [05-ingest/03-semantic-chunking](../05-ingest/03-semantic-chunking.md) |
| 检索回来一堆不相关 | [07-retrieval](../07-retrieval/01-retrieval-is-key.md) 整章 |
| 模型老编答案 | [06-basic-rag/04-say-i-dont-know](../06-basic-rag/04-say-i-dont-know.md) |
| 权限怎么做 | [08-permission](../08-permission/01-permission-is-devil.md) 整章 |
| 怎么降本提速 | [10-production](../10-production/01-prompt-caching.md) 整章 |

---

## 2. 环境准备

跟做需要这些（首次配一遍，全程通用）：

```bash
# 1. Python 依赖
pip install -r requirements.txt

# 2. 一个带 pgvector 的 Postgres（最简：用 Docker）
docker run -d --name ekb-pg \
  -e POSTGRES_PASSWORD=ekb -p 5432:5432 \
  pgvector/pgvector:pg16

# 3. 在库里启用扩展
psql postgresql://postgres:ekb@localhost:5432/postgres \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 4. 配一个模型 key（二选一）
export OPENAI_API_KEY=sk-...
# 或
export ANTHROPIC_API_KEY=sk-ant-...
```

前端章节（09）另需 Node.js 18+，到时再说。

---

## 3. 代码组织约定

整本手册的代码会逐步拼成一个项目，目录长这样（跟做时建议照建）：

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

每章的代码片段会标注它属于哪个文件，方便你拼。

---

## 4. 阅读约定（和全集合一致）

- **每篇结构统一**：一句话总结 → 概念 → 最小代码 → 进阶 → 生产建议 → 常见坑（表格）→ 下一步
- **代码尽量可独立跑**：import 完整，关键 API 写全路径
- **错误示范有标注**：`# ❌ 错误` / `# ✅ 正确`
- **跨手册引用**：用到前 7 本的地方会给链接，方便回看零件细节

---

## 5. 给不同读者的建议

| 你是 | 建议 |
|------|------|
| 学完前 7 本，想串起来 | 读法 A，从头跟做，重点体会「零件如何拼成系统」 |
| 在公司真要做知识库 | 读法 A，但 demo 数据换成你们真实文档 |
| 已在做、卡在某处 | 读法 B，直奔对应章 |
| 只想了解架构 | 读 01-03 章 + 各章开头的「一句话」即可 |

---

## 下一步

正式开工。从需求拆解开始——一个企业知识库的真实需求到底是什么：

→ [02-design/01-real-requirements](../02-design/01-real-requirements.md)
