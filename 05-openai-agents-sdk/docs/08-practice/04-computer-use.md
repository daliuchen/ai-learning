# 实战 4：Computer Use Agent（操作浏览器）

> `ComputerTool` 配上支持 computer use 的 OpenAI 模型，就能让 AI 自己操作浏览器：截图、看图、决定点击/输入/滚动，然后循环。订机票、抓数据、跑 UI 测试都能做。
>
> 以 openai-agents 0.24 为准，需要 OpenAI API key 和支持 computer 工具的模型（`gpt-5.6-*`）；SDK 不带浏览器实现，要自己写 `AsyncComputer`（见第 2、3 节）。

---

## 1. 风险预警

Computer Use 是权限很高的工具。AI 可以在浏览器里做任何操作，如果网页里藏了指令（prompt injection），它可能去执行登录、转账之类的恶意操作。所以它必须跑在沙箱或隔离环境里，关键操作要有人盯着，也别让它碰你的个人账号。

SDK 提供了两道闸。

一是 `on_safety_check` 回调。Responses API 会在 `computer_call` 里带上 `pending_safety_checks`（`id` / `code` / `message`）。你给 `ComputerTool(on_safety_check=...)` 传一个回调，返回 True 才放行；返回 False 会抛 `UserError`，动作和截图都不执行。不写回调就等于直接执行（同时留下未确认的 check），SDK 不会替你弹人工确认。遇到不认识的 check code，也应该当成需要人工复核。

二是在 harness 层强制执行的策略。没有被标记 safety check 的调用根本不会触发回调，所以 URL 白名单、禁止点击某些按钮这类必须生效的规则，要写在你自己的 `Computer` 实现里。

---

## 2. 设置

```bash
pip install openai-agents          # 没有 [browser] 这个 extra，openai-agents[browser] 装不上
pip install playwright
playwright install chromium
```

SDK 包里没有 `LocalPlaywrightComputer`，`from agents.computer import LocalPlaywrightComputer` 会直接 ImportError。`agents.computer` 只提供 `Computer` / `AsyncComputer` 两个接口，官方的 Playwright 实现放在仓库的 `examples/tools/computer_use.py` 里。下面自己写一个。

---

## 3. 自己实现 `AsyncComputer`（Playwright 版）

需要实现 9 个方法和两个类属性，签名如下：

| 成员 | 签名 |
|------|------|
| `environment`, `dimensions` | 类属性，如 `"browser"`、`(1280, 800)` |
| `screenshot()` | `-> str`（base64 的 PNG） |
| `click(x, y, button)` | `button` ∈ `left/right/wheel/back/forward` |
| `double_click(x, y)` / `move(x, y)` | |
| `scroll(x, y, scroll_x, scroll_y)` | |
| `type(text)` / `keypress(keys: list[str])` | |
| `wait()` / `drag(path: list[tuple[int, int]])` | |

```python
import asyncio
import base64
from playwright.async_api import async_playwright
from agents.computer import AsyncComputer


class PlaywrightComputer(AsyncComputer):
    environment = "browser"
    dimensions = (1280, 800)

    def __init__(self, headless: bool = True):
        self.headless = headless

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self.page = await self._browser.new_page(viewport={"width": 1280, "height": 800})
        return self

    async def __aexit__(self, *exc):
        await self._browser.close()
        await self._pw.stop()

    async def screenshot(self) -> str:
        png = await self.page.screenshot()
        return base64.b64encode(png).decode()

    async def click(self, x, y, button="left"):
        pw_button = {"left": "left", "right": "right", "wheel": "middle"}.get(button, "left")
        await self.page.mouse.click(x, y, button=pw_button)

    async def double_click(self, x, y):
        await self.page.mouse.dblclick(x, y)

    async def scroll(self, x, y, scroll_x, scroll_y):
        await self.page.mouse.move(x, y)
        await self.page.mouse.wheel(scroll_x, scroll_y)

    async def type(self, text):
        await self.page.keyboard.type(text)

    async def wait(self):
        await asyncio.sleep(1)

    async def move(self, x, y):
        await self.page.mouse.move(x, y)

    async def keypress(self, keys):
        for k in keys:
            await self.page.keyboard.press(k)         # 注意：模型给的键名（如 "ENTER"）可能要映射成 Playwright 的写法

    async def drag(self, path):
        await self.page.mouse.move(*path[0])
        await self.page.mouse.down()
        for p in path[1:]:
            await self.page.mouse.move(*p)
        await self.page.mouse.up()
```

> 这个类不连模型也能先单独检查：构造出来、挂进 `ComputerTool` 和 `Agent` 不报错，说明 9 个方法的签名对上了。真实的浏览器操作要连上 computer use 模型才会触发。

---

## 4. 最简示例

```python
import asyncio
from agents import Agent, ComputerTool, Runner


def reject_flagged(data) -> bool:           # ComputerToolSafetyCheckData：data.safety_check / data.tool_call
    return False                            # 保守：任何被标记的调用都拒绝；放行前应交给人工确认


async def main():
    async with PlaywrightComputer(headless=True) as computer:
        await computer.page.goto("https://news.ycombinator.com")

        agent = Agent(
            name="WebAgent",
            instructions="""你能操作浏览器。
- 谨慎、一步一步
- 不要登录 / 输密码
- 看不清就放大或滚动
""",
            tools=[ComputerTool(computer=computer, on_safety_check=reject_flagged)],
            model="gpt-5.6",               # GA 的内置 computer 工具；旧的 computer-use-preview 见下
        )

        result = await Runner.run(agent, "告诉我 Hacker News 前 3 条标题", max_turns=20)
        print(result.final_output)


asyncio.run(main())
```

官方给的模型迁移要点：

| | 旧（preview） | 新（GA） |
|---|---|---|
| 模型 | `computer-use-preview` | `gpt-5.6-*`（`gpt-5.6`、Sol / Terra / Luna） |
| 工具选择符 | `computer_use_preview` | `computer` |
| 一次调用的动作 | 单个 action | 批量 `actions[]`，SDK 按序执行后返回一张截图 |
| 截断 | 必须 `ModelSettings(truncation="auto")` | 不需要 |

SDK 按实际请求的模型名选择 payload 格式：`computer-use-preview` 开头的走旧格式，其他模型名（包括不写 `model` 时的默认 `gpt-5.6-luna`）都走 GA 格式。如果用的是提示词模板、请求里没带 model，SDK 会沿用旧 payload，除非你显式写 `model="gpt-5.6"` 或 `ModelSettings(tool_choice="computer")`。`ComputerTool` 也支持传 `ComputerProvider` 工厂：GA payload 序列化时不需要 environment 和尺寸，所以可以先序列化，再创建 computer。

---

## 5. 怎么工作的

Computer Use 的循环如下：

```
1. Agent 拿 screenshot（computer.screenshot()）
2. 喂给 model
3. Model 输出动作（click / type / scroll / key；GA 模型一次可能是一批）
4. SDK 按序执行动作（调用你的 Computer 方法）
5. 拿新 screenshot，作为 computer_call_output 回给模型
6. 重复直到 final_output
```

每一轮都是看、想、做三步。截图和动作在 `new_items` 里表现为 `ToolCallItem` / `ToolCallOutputItem`（computer_call / computer_call_output）。要注意，computer 工具的 safety check 不走 `RunState` 的 interruptions 流程，只走回调；能通过 `needs_approval` 暂停 run 的是其他工具。

---

## 6. 调试和配置第 3 节的 PlaywrightComputer

```python
async with PlaywrightComputer(headless=False) as computer:     # headless=False：debug 时能看到浏览器跑
    ...
```

viewport、浏览器类型（chromium / firefox / webkit）、user agent 都在你自己的 `__aenter__` 里配置。`dimensions` 要和 viewport 保持一致，否则模型点击的坐标会偏。生产环境用 `headless=True`。

---

## 7. 实战：自动订餐

```python
async def order_food():
    async with PlaywrightComputer(headless=False) as computer:
        agent = Agent(
            name="OrderBot",
            instructions="""帮我在外卖网站点餐。

约束：
- 价格不超过 50
- 选评分 4.5+ 的
- 不要登录我的账号，只下单到购物车，等我确认
""",
            tools=[ComputerTool(computer=computer, on_safety_check=reject_flagged)],
            model="gpt-5.6",
        )

        # 先手动打开网站 + 登录
        await computer.page.goto("https://example-food-app.com")
        # ... 手动登录
        # 然后让 agent 接管

        result = await Runner.run(agent, "点一份午餐", max_turns=30)
        print(result.final_output)
```

这个例子有风险，别真拿自己的账号试。

---

## 8. 自动化测试场景（安全）

```python
async def test_app_flow():
    """端到端测试：注册 → 登录 → 完成任务"""
    async with PlaywrightComputer() as computer:
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
            tools=[ComputerTool(computer=computer, on_safety_check=reject_flagged)],
            model="gpt-5.6",
        )

        result = await Runner.run(agent, "执行注册测试", max_turns=20)

        # 保留 trace 用于回归
        return result
```

我觉得 QA 自动化是 Computer Use 最合适的用途之一：环境是自己的测试站点，风险可控。

---

## 9. 数据抓取（read-only）

```python
async def scrape_listings(query: str):
    async with PlaywrightComputer() as computer:
        agent = Agent(
            name="Scraper",
            instructions=f"""搜索 "{query}"，提取前 10 条结果：标题 / 价格 / URL。

只看不动：
- 不点广告
- 不下单
- 不填表单
""",
            tools=[ComputerTool(computer=computer, on_safety_check=reject_flagged)],
            output_type=ScrapingResult,
            model="gpt-5.6",
        )

        result = await Runner.run(agent, f"去 example-shop.com 搜 {query}", max_turns=15)
        return result.final_output
```

---

## 10. 跟 function_tool 混用

```python
@function_tool
async def parse_html(html: str) -> dict:
    """解析 HTML 提取结构化数据"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    return {"title": soup.title.string if soup.title else None}


agent = Agent(
    name="Web",
    tools=[
        ComputerTool(computer=computer, on_safety_check=reject_flagged),
        parse_html,
    ],
)
```

Computer Tool 负责 UI 操作，function_tool 负责其他处理。截图由 computer 自己产出，不用再造一个 `save_screenshot` 工具。同一类工具里还有 `ShellTool`（本地或 OpenAI 托管容器）和 `ApplyPatchTool`（模型给 diff，由你的 `ApplyPatchEditor` 写盘），它们默认 `needs_approval=False`，要审批得显式设成 `True`。

---

## 11. 远程 Computer

不一定非得用本地 Playwright。`Computer` / `AsyncComputer` 只是接口，你可以把截图和动作转发给远程浏览器服务（Browserbase 之类）或云桌面：

```python
class RemoteComputer(AsyncComputer):
    environment = "browser"
    dimensions = (1280, 800)
    # 每个方法转成对远程服务的 API 调用 ...

async with RemoteComputer(api_key="...") as computer:
    agent = Agent(tools=[ComputerTool(computer=computer)], model="gpt-5.6")
```

在没有 GUI 的 CI 机器上跑、需要多开实例并发、不想污染本地环境，这几种情况都适合用远程方案。

---

## 12. 限制 & 安全

### 限制

- 慢：每一轮都要截图加一次 LLM 调用，一步常常要好几秒
- 贵：截图消耗大量 input token，computer 模型的价格看 OpenAI 价格页
- 不稳：UI 变化、加载慢都容易出错
- 上下文：截图多了很占上下文，可以用 `RunConfig(call_model_input_filter=...)` 只保留最近几张

### 安全 best practices

```python
# 1. URL 白名单：写在你的 Computer 实现里（必须应用的策略不要指望 safety check 回调）
ALLOWED_HOSTS = {"example.com", "internal.company.com"}


async def safe_run():
    async with PlaywrightComputer() as computer:
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


# 2. 被 Responses 标记的 safety check：默认拒绝，人工确认后才放行
# 3. max_turns 控制
result = await Runner.run(agent, "...", max_turns=10)  # 不放任
# 4. 跑在一次性的容器 / 虚拟机里，账号凭证最小权限
```

---

## 13. 完整 demo（需要 OpenAI key 和 playwright）

```python
import asyncio
from agents import Agent, ComputerTool, Runner
# PlaywrightComputer 见第 3 节


def reject_flagged(data) -> bool:
    return False


async def main():
    async with PlaywrightComputer(headless=False) as computer:
        await computer.page.goto("https://news.ycombinator.com")

        agent = Agent(
            name="HNAgent",
            instructions="""你浏览 Hacker News。
- 看截图
- 滚动 / 点击只在必要时
- 提取信息后输出，别瞎逛
""",
            tools=[ComputerTool(computer=computer, on_safety_check=reject_flagged)],
            model="gpt-5.6",
        )

        result = await Runner.run(agent, "告诉我当前第 1-3 条帖子的标题和评论数", max_turns=10)
        print(result.final_output)


asyncio.run(main())
```

---

## 14. 何时不用 Computer Use

- 网站有 API → 直接调 API（快得多，也稳得多）
- 内容是静态网页 → httpx + BeautifulSoup
- 简单提取 → web_search hosted tool
- 需要登录敏感账号 → 别用 AI 自动登

只有面对没有 API 的界面时，才轮到 Computer Use 出场。

---

## 15. 下一步

- 横向对比 → [横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](./05-vs-others.md)
- Hosted Tools 的完整介绍 → [Hosted Tools：OpenAI 托管的内置工具](../02-tools/02-hosted-tools.md)
- 安全 → [安全：Prompt Injection / 越狱防御 / 滥用监控](../07-production/04-security.md)
