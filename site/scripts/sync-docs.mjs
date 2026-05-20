/**
 * sync-docs.mjs (collection mode)
 * --------------------------------
 * 站点位于 ai-learning/site/，扫描所有同级的 NN-xxx 手册目录：
 *   ai-learning/01-langchain/{docs,demos}
 *   ai-learning/02-pydantic-ai/{docs,demos}
 *   ai-learning/03-xxx/{docs,demos}
 *
 * 输出到：
 *   site/pages/docs/<manual-slug>/...
 *   site/public/demos/<manual-slug>/...
 *
 * 自动生成 _meta.ts、修复 markdown 相对链接、把 demo 链接展开成代码块。
 */
import { promises as fs, existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SITE_ROOT = path.resolve(__dirname, '..')
const COLLECTION_ROOT = path.resolve(SITE_ROOT, '..')
const DST_DOCS = path.join(SITE_ROOT, 'pages', 'docs')
const DST_DEMOS = path.join(SITE_ROOT, 'public', 'demos')

const MANUAL_TITLES = {
  '01-langchain': '🦜 LangChain 全家桶',
  '02-pydantic-ai': '🤖 Pydantic AI',
}

const SECTION_TITLES = {
  // LangChain
  '01-langchain': 'LangChain 框架',
  '02-langsmith': 'LangSmith',
  '03-langgraph': 'LangGraph',
  '04-comparison': '对比与实战',
  // Pydantic AI
  '01-basics': '基础入门',
  '02-tools': '工具系统',
  '03-advanced': '进阶能力',
  '04-modules': '配套模块',
  '05-patterns': '模式与协作',
  '06-practice': '实战与对比',
}

async function rmrf(p) {
  await fs.rm(p, { recursive: true, force: true })
}

async function readH1(file) {
  const c = await fs.readFile(file, 'utf8')
  const m = c.match(/^#\s+(.+?)\s*$/m)
  return m ? m[1].trim() : null
}

function langOfExt(p) {
  if (p.endsWith('.py')) return 'python'
  if (p.endsWith('.ts') || p.endsWith('.tsx')) return 'ts'
  if (p.endsWith('.js') || p.endsWith('.mjs')) return 'js'
  if (p.endsWith('.json')) return 'json'
  return 'text'
}

/** 把 [...](../../demos/foo.py) 形式的链接展开成嵌入代码块 */
function inlineDemoCode(content, manualRoot, manualSlug) {
  return content.replace(
    /^(?<prefix>[ \t]*[^\n]*?)\[(?<label>[^\]]+)\]\(\.\.\/\.\.\/(?<rel>demos\/[^)]+\.(?:py|json|mjs|ts|tsx))\)(?<suffix>[^\n]*)$/gm,
    (_match, _p1, _p2, _p3, _p4, _offset, _s, groups) => {
      const { prefix, label, rel, suffix } = groups
      const absPath = path.join(manualRoot, rel)
      if (!existsSync(absPath)) return _match
      const code = readFileSync(absPath, 'utf8').replace(/\s+$/g, '')
      const lang = langOfExt(rel)
      const filename = rel.split('/').slice(-1)[0]
      const publicHref = `/demos/${manualSlug}/${rel.replace(/^demos\//, '')}`
      return [
        `${prefix}[${label}](${publicHref})${suffix}`.trimEnd(),
        '',
        `\`\`\`${lang} filename="${filename}" showLineNumbers copy`,
        code,
        '```',
      ].join('\n')
    },
  )
}

function fixLinks(content, manualSlug, manualRoot) {
  content = inlineDemoCode(content, manualRoot, manualSlug)
  content = content.replace(
    /\(\.\.\/\.\.\/demos\//g,
    `(/demos/${manualSlug}/`,
  )
  // ../03-langgraph/01-introduction.md → /docs/<manual>/03-langgraph/01-introduction
  content = content.replace(
    /\]\(\.\.\/(\d+-[^/)]+)\/([^)]+)\.md\)/g,
    `](/docs/${manualSlug}/$1/$2)`,
  )
  // 同目录 04-output-parsers.md → ./04-output-parsers
  content = content.replace(
    /\]\((\d+-[a-zA-Z0-9-_]+)\.md\)/g,
    '](./$1)',
  )
  content = content.replace(
    /\]\(([a-zA-Z0-9-_]+)\.md\)/g,
    '](./$1)',
  )
  return content
}

async function copyDocs(src, dst, manualSlug, manualRoot) {
  await fs.mkdir(dst, { recursive: true })
  for (const entry of await fs.readdir(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name)
    if (entry.isDirectory()) {
      await copyDocs(s, path.join(dst, entry.name), manualSlug, manualRoot)
      continue
    }
    if (entry.name.endsWith('.md')) {
      const d = path.join(dst, entry.name)
      const raw = await fs.readFile(s, 'utf8')
      const fixed = fixLinks(raw, manualSlug, manualRoot)
      await fs.writeFile(d, fixed, 'utf8')
    }
  }
}

function shortTitle(rawTitle, slug) {
  let t = rawTitle.replace(
    /^(?:Lang(?:Chain|Smith|Graph)|Pydantic\s*AI|实战项目)\s*\d+\s*[：:]\s*/i,
    '',
  )
  const m = slug.match(/^(\d+)[-_]/)
  return m ? `${m[1]} · ${t}` : t
}

async function genMeta(dir, depth = 0) {
  // depth=0 表示 pages/docs/ 顶层（列各手册），用 MANUAL_TITLES
  // depth>=1 表示手册内部目录（章节、子文件），用 SECTION_TITLES
  const entries = await fs.readdir(dir, { withFileTypes: true })
  const sorted = entries
    .filter((e) => !e.name.startsWith('_'))
    .sort((a, b) => a.name.localeCompare(b.name))

  const meta = {}
  for (const e of sorted) {
    if (e.isDirectory()) {
      if (depth === 0) {
        meta[e.name] = MANUAL_TITLES[e.name] || e.name
      } else {
        meta[e.name] = SECTION_TITLES[e.name] || e.name
      }
      await genMeta(path.join(dir, e.name), depth + 1)
    } else if (e.name.endsWith('.md') || e.name.endsWith('.mdx')) {
      const slug = e.name.replace(/\.(md|mdx)$/, '')
      const raw = (await readH1(path.join(dir, e.name))) || slug
      meta[slug] = shortTitle(raw, slug)
    }
  }
  await fs.writeFile(
    path.join(dir, '_meta.ts'),
    `export default ${JSON.stringify(meta, null, 2)}\n`,
  )
}

async function copyDemos(src, dst) {
  await fs.mkdir(dst, { recursive: true })
  for (const entry of await fs.readdir(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name)
    const d = path.join(dst, entry.name)
    if (entry.isDirectory()) {
      await copyDemos(s, d)
      continue
    }
    await fs.copyFile(s, d)
  }
}

async function discoverManuals() {
  const entries = await fs.readdir(COLLECTION_ROOT, { withFileTypes: true })
  const manuals = []
  for (const e of entries) {
    if (!e.isDirectory()) continue
    if (!/^\d{2}-/.test(e.name)) continue
    const manualPath = path.join(COLLECTION_ROOT, e.name)
    const docsPath = path.join(manualPath, 'docs')
    if (!existsSync(docsPath)) continue
    manuals.push({
      slug: e.name,
      title: MANUAL_TITLES[e.name] || e.name,
      root: manualPath,
      docs: docsPath,
      demos: path.join(manualPath, 'demos'),
    })
  }
  return manuals.sort((a, b) => a.slug.localeCompare(b.slug))
}

async function main() {
  console.log('🧹 cleaning…')
  await rmrf(DST_DOCS)
  await rmrf(DST_DEMOS)

  const manuals = await discoverManuals()
  console.log(`📚 discovered ${manuals.length} manual(s):`)
  for (const m of manuals) console.log(`   - ${m.slug}  ${m.title}`)

  for (const m of manuals) {
    const dstDocs = path.join(DST_DOCS, m.slug)
    const dstDemos = path.join(DST_DEMOS, m.slug)
    console.log(`📝 ${m.slug} docs → ${dstDocs}`)
    await copyDocs(m.docs, dstDocs, m.slug, m.root)
    if (existsSync(m.demos)) {
      console.log(`🐍 ${m.slug} demos → ${dstDemos}`)
      await copyDemos(m.demos, dstDemos)
    }
  }

  console.log('🛠  generating _meta.ts…')
  await genMeta(DST_DOCS)

  console.log('✅ sync done')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
