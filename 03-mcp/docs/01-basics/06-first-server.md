# MCP 06：5 分钟跑通 Hello World MCP Server

> **一句话**：用 FastMCP 写一个能加法、能查时间、能列资源、能用 Prompt 的最小 Server，分别用 Inspector、自己写的 Client、Claude Code 三种方式调通。这是整本手册第一个能跑的端到端 demo。

---

## 1. 目标

写一个名叫 `hello-mcp` 的 Server，同时演示三大原语：

- 一个 **Tool**：`add(a, b)`、`current_time(timezone)`
- 一个 **Resource**：`hello://greeting/{name}`
- 一个 **Prompt**：`code-review(file)`

然后用三种方式调用它：

1. **Inspector**（可视化调试）
2. **自己写的 Python Client**（程序化调用）
3. **Claude Code**（真实使用场景）

跑完这一篇你就完整经历过一遍 MCP 工作流。

---

## 2. 写 Server

新建 `demos/basics/06_first_server.py`：

```python
# demos/basics/06_first_server.py
"""Hello MCP —— 演示三大原语的最小 Server"""
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

# 1️⃣ 创建 Server 实例（name 是给 Host 看的标识）
mcp = FastMCP("hello-mcp")


# ===== Tools：模型可调用的"动词" =====

@mcp.tool()
def add(a: int, b: int) -> int:
    """两个整数相加。

    Args:
        a: 被加数
        b: 加数
    """
    return a + b


@mcp.tool()
def current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间（按指定时区）。

    Args:
        timezone: IANA 时区标识，默认 Asia/Shanghai。例：UTC、America/New_York
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f"❌ 未知时区: {timezone}"
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z%z")


# ===== Resource：应用可读取的"上下文" =====

@mcp.resource("hello://greeting/{name}")
def greeting(name: str) -> str:
    """给指定姓名的人生成一个问候语"""
    return f"你好，{name}！欢迎使用 MCP。今天是 {datetime.now():%Y-%m-%d}。"


# ===== Prompt：用户显式触发的"模板" =====

@mcp.prompt(name="code-review")  # 显式 kebab-case 名称，便于 /hello-mcp:code-review 调用
def code_review(file: str, language: str = "python") -> str:
    """生成一段 code review 引导。

    Args:
        file: 要审查的文件路径
        language: 编程语言，默认 python
    """
    return (
        f"请对 {language} 文件 `{file}` 做代码 review，关注以下方面：\n"
        f"1. 安全性（注入、敏感信息泄漏）\n"
        f"2. 性能（明显的算法或 IO 瓶颈）\n"
        f"3. 可读性（命名、注释、复杂度）\n"
        f"4. 测试覆盖率（关键路径有没有用例）\n"
        f"\n请按 issue 严重度从高到低排列。"
    )


if __name__ == "__main__":
    # 默认 stdio 传输——Host 启动子进程时用
    mcp.run()
```

> **几条关键约定**：
> - **不要 `print()`**：stdio 模式下 stdout 是协议通道，`print` 会污染。要打日志用 `logging` 写到 stderr，或用工具里的 `ctx` 对象（后续章节讲）。
> - **docstring 写好**：FastMCP 会把它转成 tool/resource/prompt 的 description，**LLM 是读者**。
> - **类型注解必填**：FastMCP 通过类型推导 JSON Schema。

---

## 3. 方式一：用 Inspector 调试

```bash
cd 03-mcp
npx @modelcontextprotocol/inspector python demos/basics/06_first_server.py
```

打开 http://localhost:6274 → 点 **Connect**。

握手成功后能看到：

- **左下角**：`Capabilities: tools, resources, prompts`
- **左侧菜单**：Tools / Resources / Prompts 三个 tab

### 3.1 测 Tool

切到 **Tools** → 看到 `add` 和 `current_time` → 点 `add`：

```
a = 7
b = 35
```

点 **Run Tool**，右侧看到：

```json
{
  "content": [
    {
      "type": "text",
      "text": "42"
    }
  ]
}
```

### 3.2 测 Resource

切到 **Resources** → 点 "Templates" → 看到 `hello://greeting/{name}` → 在 name 框填 `张三` → **Read**，得到：

```
你好，张三！欢迎使用 MCP。今天是 2026-05-20。
```

### 3.3 测 Prompt

切到 **Prompts** → 点 `code-review` → 填参数：

```
file = src/auth.py
language = python
```

→ **Get Prompt**，看到生成的多行 review 引导文案。

### 3.4 看 Console

切到 **Console** / **Notifications** 能看到完整 JSON-RPC 流量。这是你 debug 时最有用的视图。

---

## 4. 方式二：自己写 Python Client 调

新建 `demos/basics/06_first_client.py`：

```python
# demos/basics/06_first_client.py
"""用 Python Client 调用上面的 Server，演示完整生命周期"""
import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

SERVER_PATH = Path(__file__).parent / "06_first_server.py"


async def main() -> None:
    params = StdioServerParameters(
        command="python",
        args=[str(SERVER_PATH)],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # ===== 1. 握手 =====
            init = await session.initialize()
            print(f"🤝 已连接 {init.serverInfo.name} v{init.serverInfo.version}")
            print(f"   协议版本：{init.protocolVersion}")

            # ===== 2. 列举 Tools / Resources / Prompts =====
            tools = await session.list_tools()
            print(f"\n📦 工具数量：{len(tools.tools)}")
            for t in tools.tools:
                print(f"   - {t.name}: {t.description}")

            resources = await session.list_resource_templates()
            print(f"\n📚 资源模板：{len(resources.resourceTemplates)}")
            for r in resources.resourceTemplates:
                print(f"   - {r.uriTemplate}")

            prompts = await session.list_prompts()
            print(f"\n💡 Prompt：{len(prompts.prompts)}")
            for p in prompts.prompts:
                print(f"   - {p.name}: {p.description}")

            # ===== 3. 调一个 Tool =====
            print("\n🔧 调用 add(40, 2)：")
            result = await session.call_tool("add", {"a": 40, "b": 2})
            print(f"   → {result.content[0].text}")

            # ===== 4. 读一个 Resource =====
            print("\n📖 读 hello://greeting/Claude：")
            res = await session.read_resource("hello://greeting/Claude")
            print(f"   → {res.contents[0].text}")

            # ===== 5. 拿一个 Prompt =====
            print("\n📝 拿 code-review 模板：")
            pr = await session.get_prompt("code-review", {"file": "auth.py"})
            print(f"   → 共 {len(pr.messages)} 条消息")
            for m in pr.messages:
                print(f"      [{m.role}] {m.content.text[:80]}...")


if __name__ == "__main__":
    asyncio.run(main())
```

跑：

```bash
python demos/basics/06_first_client.py
```

预期输出：

```
🤝 已连接 hello-mcp v1.10.x
   协议版本：2025-11-25

📦 工具数量：2
   - add: 两个整数相加。
   - current_time: 获取当前时间（按指定时区）。

📚 资源模板：1
   - hello://greeting/{name}

💡 Prompt：1
   - code-review: 生成一段 code review 引导。

🔧 调用 add(40, 2)：
   → 42

📖 读 hello://greeting/Claude：
   → 你好，Claude！欢迎使用 MCP。今天是 2026-05-20。

📝 拿 code-review 模板：
   → 共 1 条消息
      [user] 请对 python 文件 `auth.py` 做代码 review，关注以下方面：...
```

这段代码就是一个**完整的 MCP Host 雏形**——它扮演的就是 Claude Code 内部 Client 的角色。

---

## 5. 方式三：接到 Claude Code 实际用

> 如果你没装 Claude Code，先跳过这节，看完 04-integration/01-claude-code 再回来。

### 5.1 添加 Server 配置

打开 `~/.claude/mcp.json`（或在 Claude Code 设置里通过 UI 添加）：

```json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": [
        "/Users/cliu/cliu/me_workspace/ai-learning/03-mcp/demos/basics/06_first_server.py"
      ]
    }
  }
}
```

路径要换成你本地的**绝对路径**。

### 5.2 验证已连上

重启 Claude Code，看左下角 / 状态栏里的 MCP 指示，或在对话里输入：

```
列一下你能用的 MCP 工具
```

Claude 应该回："我能用 hello-mcp 这个 Server 提供的 `add` 和 `current_time` 工具……"

### 5.3 真实对话

```
用 hello-mcp 算一下 17 * 19，再告诉我现在纽约时间
```

Claude 会：
1. 先用 `add` 工具——但发现 add 不能做乘法，转去用 Python REPL 之类的（如果有）
2. 用 `current_time` 工具，参数 `timezone="America/New_York"`，把结果转给你

可以观察到 Claude Code UI 上会弹出工具调用气泡，显示 input 和 output。

### 5.4 用 Prompt

在 Claude Code 里输入 `/`，应该能看到 `hello-mcp:code-review`：

```
/hello-mcp:code-review file=src/auth.py
```

按 Enter，Claude Code 会用 prompt 模板里的文本开启对话——比你打字快得多。

### 5.5 用 Resource

Claude Code 里资源通常通过 `@` 提及或在 UI 里附加。具体见 04-integration/01-claude-code。

---

## 6. 一个完整 demo 的目录结构

跑完上面，你的 `03-mcp/demos/basics/` 应该是：

```
demos/basics/
├── 00_self_check.py             # 环境自检（05-installation 写的）
├── 06_first_server.py           # 本篇 Server
└── 06_first_client.py           # 本篇 Client
```

---

## 7. 这一步打通了什么

恭喜，你刚才完成了 MCP 开发的完整闭环：

```
[Server 端]               [Client 端]                 [Host 端]
FastMCP 装饰器       <→  ClientSession        <→  Claude Code
  ↓ 协议层 ↓            ↓ 协议层 ↓                ↓ 真实用户场景 ↓
JSON-RPC 2.0 over stdio
```

后面所有章节都是在这条主链上添东西：

- 02-server：把工具/资源/提示的细节用全（参数 schema、错误返回、订阅、生命周期注入、Tasks 异步……）
- 03-client：把客户端能力用全（Sampling、Elicitation、Roots、多 Server 聚合）
- 04-integration：换 Host（Cursor、VS Code、ChatGPT），换框架（LangChain、Pydantic AI）
- 05-production：换传输层（远程 HTTP + OAuth），换部署形态
- 06-advanced + 07-practice：用进阶特性与实战项目串起来

---

## 8. 常见坑

| 现象 | 原因 |
|------|------|
| **Client 卡在 initialize** | Server 端 `print()` 污染 stdout / 文件路径不对 / Python 解释器版本不匹配 |
| **Inspector 显示 "0 tools"** | 装饰器位置错（必须在 module 顶层），或 `if __name__ ...` 块外没创建 mcp 实例 |
| **Claude Code 里看不到 Server** | mcp.json 路径写错；路径必须**绝对路径**；Claude Code 没重启 |
| **`mcp.run()` 后立即退出** | 你可能跑在 jupyter notebook 或脚本中混入了 asyncio loop；用 `python xxx.py` 直接跑 |
| **资源模板的参数没传** | `hello://greeting/{name}` 必须填 name，URI 写完整：`hello://greeting/张三` |

---

## 9. 下一步：开始 02-server

01-basics 到此结束。从下一章 02-server 开始，每一篇都是"把某个原语/特性用到极致"：

- 02-server/01-tools：参数 schema 细节、返回结构化内容、错误返回、annotations
- 02-server/02-resources：模板补全、订阅、二进制资源
- 02-server/03-prompts：多轮对话模板、引用 Resource、参数补全
- 02-server/04-lifespan-context：启动钩子、Context 注入 DB 连接
- ……

## 参考资料

- 官方 Build a Server 教程：https://modelcontextprotocol.io/docs/develop/build-server
- FastMCP 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/fastmcp
- Reference everything server：https://github.com/modelcontextprotocol/servers/tree/main/src/everything
