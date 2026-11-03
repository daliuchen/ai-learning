import Link from 'next/link'

type Book = {
  no: string
  title: string
  desc: string
  count: number
  href: string
  tags: string[]
}

const BOOKS: Book[] = [
  {
    no: '01',
    title: 'LangChain 全家桶',
    desc: 'LangChain 1.x、LangGraph、LangSmith，从 create_agent 一路讲到部署和观测。',
    count: 34,
    href: '/docs/01-langchain/01-langchain/01-overview',
    tags: ['Agent 框架', 'LangGraph'],
  },
  {
    no: '02',
    title: 'Pydantic AI',
    desc: '类型优先的 Python Agent 框架，V2 的 capability、依赖注入、评测和图。',
    count: 33,
    href: '/docs/02-pydantic-ai/01-basics/01-overview',
    tags: ['Agent 框架', '类型安全'],
  },
  {
    no: '03',
    title: 'MCP 协议',
    desc: '规范、Python SDK、Server 与 Client、OAuth 和远程部署，外加三个实战项目。',
    count: 35,
    href: '/docs/03-mcp/01-basics/01-overview',
    tags: ['协议', '工具接入'],
  },
  {
    no: '04',
    title: 'Prompt Engineering',
    desc: '先有评测再写 prompt：迭代闭环、各家模型的差异、上线后的监控。',
    count: 44,
    href: '/docs/04-prompt-engineering/01-foundations/01-overview',
    tags: ['方法论', '评测'],
  },
  {
    no: '05',
    title: 'OpenAI Agents SDK',
    desc: 'Agent、Runner、Handoffs、Guardrails 和 Sessions，也讲怎么接国产模型。',
    count: 38,
    href: '/docs/05-openai-agents-sdk/01-basics/01-overview',
    tags: ['Agent 框架', 'Handoffs'],
  },
  {
    no: '06',
    title: 'Embedding 与向量检索',
    desc: '把 RAG 拆成切分、向量化、索引、检索、重排、评测六段，逐段讲取舍。',
    count: 44,
    href: '/docs/06-embedding/01-foundations/01-what-is-embedding',
    tags: ['RAG', '向量库'],
  },
  {
    no: '07',
    title: 'Context Engineering',
    desc: '窗口里放什么、放多少：检索注入、记忆、压缩、长上下文与缓存。',
    count: 46,
    href: '/docs/07-context-engineering/01-foundations/01-what-is-context-engineering',
    tags: ['上下文', '记忆'],
  },
  {
    no: '08',
    title: '企业知识库实战',
    desc: '一个带权限、带引用、能评估的知识库，从选型做到上线。',
    count: 52,
    href: '/docs/08-enterprise-kb/01-intro/01-why-this-project',
    tags: ['项目实战', 'pgvector'],
  },
]

type Route = { title: string; note: string; steps: [string, string][] }

const ROUTES: Route[] = [
  {
    title: '第一次做 LLM 应用',
    note: '走完能搭出一个带检索的问答小应用。',
    steps: [
      ['LangChain 概览', '/docs/01-langchain/01-langchain/01-overview'],
      ['工具', '/docs/01-langchain/01-langchain/07-tools'],
      ['Agent', '/docs/01-langchain/01-langchain/08-agents'],
      ['RAG', '/docs/01-langchain/01-langchain/13-rag'],
    ],
  },
  {
    title: '已经会调 API，想选 Agent 框架',
    note: '三家各看入门和多 agent 两篇，再看对比。',
    steps: [
      ['Pydantic AI 概览', '/docs/02-pydantic-ai/01-basics/01-overview'],
      ['OpenAI Agents SDK 概览', '/docs/05-openai-agents-sdk/01-basics/01-overview'],
      ['LangGraph 入门', '/docs/01-langchain/03-langgraph/01-introduction'],
      ['框架对比', '/docs/01-langchain/04-comparison/01-frameworks'],
    ],
  },
  {
    title: '把内部系统接给 AI',
    note: '写一个 MCP Server，让 Claude Code、Cursor 和各家框架都能用。',
    steps: [
      ['MCP 是什么', '/docs/03-mcp/01-basics/01-overview'],
      ['三大原语', '/docs/03-mcp/01-basics/03-primitives'],
      ['第一个 Server', '/docs/03-mcp/01-basics/06-first-server'],
      ['内部知识库实战', '/docs/03-mcp/07-practice/01-project-internal-kb'],
    ],
  },
  {
    title: 'RAG 效果上不去',
    note: '从切分查到重排，每一段都有可量化的改法。',
    steps: [
      ['切分决定上限', '/docs/06-embedding/04-chunking/01-why-chunking'],
      ['混合检索', '/docs/06-embedding/05-retrieval/02-bm25-fusion'],
      ['重排', '/docs/06-embedding/05-retrieval/05-rerank-pipeline'],
      ['量化提升', '/docs/08-enterprise-kb/07-retrieval/06-quantify-gains'],
    ],
  },
  {
    title: 'Prompt 改来改去没把握',
    note: '先建评测集，再一次只改一处。',
    steps: [
      ['PE 是什么', '/docs/04-prompt-engineering/01-foundations/01-overview'],
      ['评测先于 prompt', '/docs/04-prompt-engineering/01-foundations/05-eval-first'],
      ['迭代闭环', '/docs/04-prompt-engineering/02-process/04-iteration-loop'],
      ['实战：分类器', '/docs/04-prompt-engineering/08-practice/01-build-classifier'],
    ],
  },
  {
    title: '长会话和 Agent 越跑越贵',
    note: '管住窗口里的每一段内容。',
    steps: [
      ['什么是上下文工程', '/docs/07-context-engineering/01-foundations/01-what-is-context-engineering'],
      ['历史压缩', '/docs/07-context-engineering/05-compaction/02-summarization'],
      ['长上下文还是 RAG', '/docs/07-context-engineering/07-long-context/02-long-context-vs-rag'],
      ['带记忆的客服实战', '/docs/07-context-engineering/09-practice/01-memory-customer-agent'],
    ],
  },
]

const TOTAL = BOOKS.reduce((n, b) => n + b.count, 0)

export default function Home() {
  return (
    <div className="al-home">
      <section className="al-hero">
        <p className="al-eyebrow">大模型应用开发 · 中文手册</p>
        <h1 className="al-title">AI Learning</h1>
        <p className="al-lead">
          从调通第一个模型到把系统送上线。每一本都对照官方文档写，代码能跑，坑写在明处。
        </p>
        <div className="al-stats">
          <span><b>{BOOKS.length}</b> 本手册</span>
          <span><b>{TOTAL}</b> 篇文章</span>
          <span>2026 年 11 月按最新版本更新</span>
        </div>
        <div className="al-cta">
          <Link className="al-btn al-btn-primary" href={BOOKS[0].href}>从 LangChain 开始</Link>
          <a className="al-btn" href="#books">浏览全部手册</a>
        </div>
      </section>

      <section id="books" className="al-section">
        <h2 className="al-h2">手册</h2>
        <div className="al-grid">
          {BOOKS.map((b) => (
            <Link key={b.no} href={b.href} className="al-card">
              <div className="al-card-top">
                <span className="al-no">{b.no}</span>
                <span className="al-count">{b.count} 篇</span>
              </div>
              <h3 className="al-card-title">{b.title}</h3>
              <p className="al-card-desc">{b.desc}</p>
              <div className="al-tags">
                {b.tags.map((t) => (
                  <span key={t} className="al-tag">{t}</span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section className="al-section">
        <h2 className="al-h2">按问题找路线</h2>
        <div className="al-routes">
          {ROUTES.map((r) => (
            <div key={r.title} className="al-route">
              <h3 className="al-route-title">{r.title}</h3>
              <p className="al-route-note">{r.note}</p>
              <ol className="al-steps">
                {r.steps.map(([label, href]) => (
                  <li key={href}>
                    <Link href={href}>{label}</Link>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      </section>

      <section className="al-section">
        <div className="al-follow-card">
          <img src="/qrcode.jpg" alt="公众号 Ethan 的 LLM 工程手册" width={96} height={96} />
          <div>
            <strong>Ethan 的 LLM 工程手册</strong>
            <p>新章节会先发在这个公众号，扫码关注。</p>
          </div>
        </div>
      </section>
    </div>
  )
}
