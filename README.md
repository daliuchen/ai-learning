# AI Learning：大模型应用开发中文手册

8 本手册，326 篇文章，覆盖 Agent 框架、MCP、Prompt 工程、RAG 和上下文工程。每本都对照官方文档写，内容以 2026 年 11 月的版本为准。

在线阅读：<https://ai-learning-pied-zeta.vercel.app>

## 手册

| 编号 | 手册 | 讲什么 | 篇数 |
|------|------|--------|------|
| 01 | [LangChain 全家桶](01-langchain/README.md) | LangChain 1.x、LangGraph、LangSmith，从 `create_agent` 讲到部署和观测，最后和其他框架做对比 | 34 |
| 02 | [Pydantic AI](02-pydantic-ai/README.md) | 类型优先的 Python Agent 框架：V2 的 capability、依赖注入、评测、图和 harness | 33 |
| 03 | [MCP 协议](03-mcp/README.md) | 基于 2026-07-28 规范和 Python SDK 2.x：Server、Client、鉴权、远程部署，以及三个实战项目 | 35 |
| 04 | [Prompt Engineering](04-prompt-engineering/README.md) | 先有评测再写 prompt：迭代流程、常用技巧、各家模型的差异、上线后的版本管理和监控 | 44 |
| 05 | [OpenAI Agents SDK](05-openai-agents-sdk/README.md) | Agent、Runner、Handoffs、Guardrails、Sessions 和 tracing，也讲怎么接国产模型 | 38 |
| 06 | [Embedding 与向量检索](06-embedding/README.md) | 把 RAG 拆成切分、向量化、索引、检索、重排、评测六段，逐段讲取舍 | 44 |
| 07 | [Context Engineering](07-context-engineering/README.md) | 窗口里放什么、放多少：检索注入、记忆、压缩、多 agent 的上下文、长上下文与缓存 | 46 |
| 08 | [企业知识库实战](08-enterprise-kb/README.md) | 一个带权限、带引用、能评估的知识库，从选型做到上线，把前几本的内容串起来 | 52 |

刚开始接触 LLM 应用开发，可以从 01 读起；已经会调 API、在挑 Agent 框架，看 01、02、05 的概览和对比篇；做 RAG 的看 06 和 08；在线站点首页按问题列了几条阅读路线。

## 怎么跑示例

每本手册是独立的目录，有自己的 `README.md` 和 `requirements.txt`，依赖不互通，建议每本单独建一个虚拟环境。

```bash
cd 01-langchain
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入模型的 key
```

01、02、03 三本带 `demos/` 目录，里面是可以直接运行的脚本；其余几本的示例代码都在正文里，复制下来就能运行。示例用的模型各本不同：01–03、06、08 默认走通义千问的 OpenAI 兼容接口，04、05、07 正文以 GPT、Claude 为主。换模型一般只需改模型名、base_url 和 key，各本 README 里有说明。

## 目录结构

```
ai-learning/
├── 01-langchain/ … 08-enterprise-kb/   # 8 本手册，每本一个目录
└── site/                               # 在线站点（Nextra）
```

每本手册内部：

```
0X-xxx/
├── README.md          # 手册入口和章节目录
├── requirements.txt
├── .env.example       # 07、08 两本没有，按 README 设置环境变量即可
├── docs/              # 正文，按章节分目录
└── demos/             # 只有 01、02、03 有
```

## 本地预览站点

```bash
cd site
npm install
npm run dev   # 会先把各手册的 docs/ 同步到 site/pages/docs/
```

## 反馈

发现错误或者有想看的主题，欢迎在 GitHub 仓库提 issue。新章节也会同步发在公众号「Ethan 的 LLM 工程手册」。

本仓库内容为个人学习整理，非官方教程；各框架的商标和文档版权归原作者所有。
