# 实战 4：Computer Use Agent（操作浏览器）

> **一句话**：用 `ComputerTool` + `computer-use-preview` 模型，让 AI 自主操作浏览器——截图 → 看 → 决定点击 / 输入 / 滚动 → 重复。能做的事比想象多（订机票、抓数据、跑测试）。

---

## 1. 风险预警

⚠️ Computer Use 是高权限工具：

- AI 可以**任意**操作浏览器
- 如果被 prompt injection → 可能执行恶意操作（登录 / 转账）
- **必须**在沙箱 / 隔离环境跑
- **必须**有人监督关键操作
- **不要**让它访问你的个人账号

---

## 2. 设置

```bash
pip install "openai-agents[browser]"
# 装 Playwright
pip install playwright
playwright install chromium
```

---

## 3. 最简示例

```python
import asyncio
from agents import Agent, Runner, ComputerTool
from agents.computer import LocalPlaywrightComputer


async def main():
    async with LocalPlaywrightComputer() as computer:
        agent = Agent(
            name="WebAgent",
            instructions="""你能操作浏览器。
- 谨慎、一步一步
- 不要登录 / 输密码
- 看不清就放大或滚动
""",
            tools=[ComputerTool(computer=computer)],
            model="computer-use-preview",
        )

        result = await Runner.run(
            agent,
            "打开 hacker news，告诉我前 3 条标题",
            max_turns=20,
        )
        print(result.final_output)


asyncio.run(main())
```

---

## 4. 怎么工作的

Computer Use 循环：

```
1. Agent 拿 screenshot（computer.screenshot()）
2. 喂给 model
3. Model 输出动作（click / type / scroll / key）
4. SDK 执行动作
5. 拿新 screenshot
6. 重复直到 final_output
```

每个 turn 都是"看 → 想 → 做"。

---

## 5. LocalPlaywrightComputer 配置

```python
async with LocalPlaywrightComputer(
    browser_type="chromium",       # chromium / firefox / webkit
    headless=False,                # 看到浏览器跑
    viewport={"width": 1280, "height": 800},
    user_agent="...",
) as computer:
    ...
```

`headless=False` debug 用，生产用 True。

---

## 6. 实战：自动订餐

```python
async def order_food():
    async with LocalPlaywrightComputer() as computer:
        agent = Agent(
            name="OrderBot",
            instructions="""帮我在外卖网站点餐。

约束：
- 价格不超过 50
- 选评分 4.5+ 的
- 不要登录我的账号，只下单到购物车，等我确认
""",
            tools=[ComputerTool(computer=computer)],
            model="computer-use-preview",
        )

        # 先手动打开网站 + 登录
        await computer.page.goto("https://example-food-app.com")
        # ... 手动登录
        # 然后让 agent 接管

        result = await Runner.run(
            agent,
            "点一份午餐",
            max_turns=30,
        )
        print(result.final_output)
```

⚠️ 这个例子有风险——别真用在你的账号上。

---

## 7. 自动化测试场景（安全）

```python
async def test_app_flow():
    """端到端测试：注册 → 登录 → 完成任务"""
    async with LocalPlaywrightComputer() as computer:
        await computer.page.goto("https://my-test-app.com")

        agent = Agent(
            name="TestRunner",
            instructions="""你是 QA tester。

任务：测试用户注册流程。
1. 点 "Sign Up"
2. 用 fake 邮箱 test@example.com / 密码 Test123!
3. 完成验证
4. 截图保存
5. 报告流程是否正常
""",
            tools=[ComputerTool(computer=computer)],
            model="computer-use-preview",
        )

        result = await Runner.run(agent, "执行注册测试", max_turns=20)

        # 保留 trace 用于回归
        return result
```

QA 自动化是 Computer Use 最合适的场景之一。

---

## 8. 数据抓取（read-only）

```python
async def scrape_listings(query: str):
    async with LocalPlaywrightComputer() as computer:
        agent = Agent(
            name="Scraper",
            instructions=f"""搜索 "{query}"，提取前 10 条结果：标题 / 价格 / URL。

只看不动：
- 不点广告
- 不下单
- 不填表单
""",
            tools=[ComputerTool(computer=computer)],
            output_type=ScrapingResult,
            model="computer-use-preview",
        )

        result = await Runner.run(
            agent,
            f"去 example-shop.com 搜 {query}",
            max_turns=15,
        )
        return result.final_output
```

---

## 9. 跟 function_tool 混用

```python
@function_tool
def save_screenshot(path: str) -> str:
    """保存截图到本地（实际由 computer 自己截）"""
    return f"saved to {path}"


@function_tool
async def parse_html(html: str) -> dict:
    """解析 HTML 提取结构化数据"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    return {"title": soup.title.string if soup.title else None}


agent = Agent(
    name="Web",
    tools=[
        ComputerTool(computer=computer),
        save_screenshot,
        parse_html,
    ],
)
```

Computer Tool 负责 UI 操作，function_tool 负责其它处理。

---

## 10. 远程 Computer

不一定本地 Playwright，也可以远程（Browserbase / Anthropic Computer Use API 等）：

```python
# 假设有 RemoteComputer 实现
async with RemoteComputer(api_key="...") as computer:
    agent = Agent(tools=[ComputerTool(computer=computer)])
```

适合：

- CI 里跑（无 GUI 机器）
- 多并发（开多个远程实例）
- 隔离（不污染本地）

---

## 11. 限制 & 安全

### 限制

- 慢：每 turn 截图 + LLM call，5-10 秒一步
- 贵：computer-use-preview 比一般模型贵
- 不稳：UI 变化 / 加载慢易出错
- 上下文：多张 screenshot 烧上下文

### 安全 best practices

```python
# 1. URL 白名单
ALLOWED_HOSTS = {"example.com", "internal.company.com"}


async def safe_run():
    async with LocalPlaywrightComputer() as computer:
        # 监听导航
        async def block_unknown(route, req):
            from urllib.parse import urlparse
            host = urlparse(req.url).hostname
            if host not in ALLOWED_HOSTS:
                await route.abort()
            else:
                await route.continue_()

        await computer.page.route("**/*", block_unknown)

        # 跑 agent
        ...


# 2. Tool 内禁止特定操作
@function_tool
async def click_dangerous(coords: list[int]) -> str:
    # 拦截危险按钮（"立即支付"等）
    ...


# 3. max_turns 控制
result = await Runner.run(agent, "...", max_turns=10)  # 不放任
```

---

## 12. 完整 demo

```python
# demos/practice/04_computer_use.py
import asyncio
from agents import Agent, Runner, ComputerTool
from agents.computer import LocalPlaywrightComputer


async def main():
    async with LocalPlaywrightComputer(headless=False) as computer:
        await computer.page.goto("https://news.ycombinator.com")

        agent = Agent(
            name="HNAgent",
            instructions="""你浏览 Hacker News。
- 看截图
- 滚动 / 点击只在必要时
- 提取信息后输出，别瞎逛
""",
            tools=[ComputerTool(computer=computer)],
            model="computer-use-preview",
        )

        result = await Runner.run(
            agent,
            "告诉我当前第 1-3 条帖子的标题和评论数",
            max_turns=10,
        )
        print(result.final_output)


asyncio.run(main())
```

---

## 13. 何时不用 Computer Use

- 网站有 API → 直接调 API（快 100x）
- 内容是静态网页 → httpx + BeautifulSoup
- 简单提取 → web_search hosted tool
- 需要登录敏感账号 → 别用 AI 自动登

Computer Use 是**没有 API 的 UI**时的兜底，不是首选。

---

## 14. 下一步

- 📖 横向对比 → [05-vs-others.md](./05-vs-others.md)
- 📖 Hosted Tools 完整 → [02-tools/02-hosted-tools.md](../02-tools/02-hosted-tools.md)
- 📖 安全实战 → [07-production/04-security.md](../07-production/04-security.md)
