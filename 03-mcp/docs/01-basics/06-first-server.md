# MCP 06：写第一个 MCP Server 和 Client

> 这是手册里第一个端到端能跑的 demo。用 MCPServer 写一个最小的 Server，能做加法、查时间、读资源、出 Prompt，然后分别用 Inspector、自己写的 Client 和 Claude Code 三种方式把它调通。

---

## 1. 目标

写一个叫 `hello-mcp` 的 Server，把三种原语都用上：

- Tool：`add(a, b)`、`current_time(timezone)`
- Resource：`hello://greeting/{name}`
- Prompt：`code-review(file)`

然后用三种方式调用它：

1. Inspector（可视化调试）
2. 自己写的 Python Client（程序化调用）
3. Claude Code（真实使用场景）

做完这一篇，MCP 的完整工作流你就走过一遍了。

---

## 2. 写 Server

新建 `demos/basics/06_first_server.py`：

```python
# demos/basics/06_first_server.py
"""Hello MCP —— 演示三大原语的最小 Server"""
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server import MCPServer

# 1️⃣ 创建 Server 实例（name 是给 Host 看的标识）
mcp = MCPServer("hello-mcp")


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
        timezone: IANA 时区标识，默认 Asia/Shanghai。
                  例：UTC、America/New_York、Europe/London
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


@mcp.prompt(name="code-review")
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
    mcp.run()  # 默认 stdio
```

> 写的时候注意这几条：
> - 不要用 `print()`。stdio 模式下 stdout 是协议通道，`print` 会把它弄脏。要打日志用 `logging`（默认写 stderr）。
> - 认真写 docstring。MCPServer 会把它转成 tool/resource/prompt 的 description，读它的是 LLM。
> - 类型注解必须写，MCPServer 靠它推导 JSON Schema。
> - 从 v1 迁过来：`from mcp.server.fastmcp import FastMCP` 改成 `from mcp.server import MCPServer`，装饰器 `@mcp.tool()` / `@mcp.resource()` / `@mcp.prompt()` 的用法不变。

---

## 3. 方式一：用 Inspector 调试

```bash
cd 03-mcp
npx @modelcontextprotocol/inspector python demos/basics/06_first_server.py
```

> 新版 Inspector 需要 Node 22.19+。启动后终端会打印一个带 token 的 URL（0.x 版固定是 `http://localhost:6274`），按提示打开即可。已经装了 `mcp[cli]` 的话，也可以直接 `mcp dev demos/basics/06_first_server.py`。

打开页面，点 Connect。

协商成功后，能看到 Server 的 capabilities（tools、resources、prompts），左侧菜单里有 Tools / Resources / Prompts 三个 tab。

### 3.1 测 Tool

切到 Tools，能看到 `add` 和 `current_time`。点 `add`：

```
a = 7
b = 35
```

点 Run Tool，右侧显示：

```json
{
  "content": [{ "type": "text", "text": "42" }],
  "structuredContent": { "result": 42 }
}
```

结果里多了一个 `structuredContent`。因为 `add` 声明了返回类型 `-> int`，SDK 除了给模型读的 `content`，还给应用返回一份结构化结果（见[用 Tools 暴露可调用的操作](../02-server/01-tools.md)）。

### 3.2 测 Resource

切到 Resources，点 Templates，找到 `hello://greeting/{name}`，在 name 框里填 `张三`，点 Read，得到：

```
你好，张三！欢迎使用 MCP。今天是 2026-11-03。
```

### 3.3 测 Prompt

切到 Prompts，点 `code-review`，填参数：

```
file = src/auth.py
language = python
```

点 Get Prompt，就能看到生成的多行 review 引导文案。

### 3.4 看 Console

切到 Console / Notifications，能看到完整的 JSON-RPC 流量。调试时这个视图最有用。

---

## 4. 方式二：自己写 Python Client

新建 `demos/basics/06_first_client.py`：

```python
# demos/basics/06_first_client.py
"""用 Python Client 调用上面的 Server（mcp 2.x 的 Client）"""
import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

SERVER_PATH = Path(__file__).parent / "06_first_server.py"


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,  # 用当前同一个 Python 解释器避免环境问题
        args=[str(SERVER_PATH)],
    )

    async with Client(params) as client:
        # ===== 1. 连接（协商已在 async with 里做完）=====
        info = client.server_info  # 2026 版 Server 的身份是可选的，可能为 None
        print(f"🤝 已连接 {info.name if info else '(匿名)'}")
        print(f"   协议版本：{client.protocol_version}")
        print(f"   能力：{client.server_capabilities.model_dump(exclude_none=True)}")

        # ===== 2. 列举 Tools / Resources / Prompts =====
        tools = await client.list_tools()
        print(f"\n📦 工具数量：{len(tools.tools)}")
        for t in tools.tools:
            print(f"   - {t.name}: {(t.description or '').splitlines()[0]}")

        templates = await client.list_resource_templates()
        print(f"\n📚 资源模板：{len(templates.resource_templates)}")
        for r in templates.resource_templates:
            print(f"   - {r.uri_template}")

        prompts = await client.list_prompts()
        print(f"\n💡 Prompt：{len(prompts.prompts)}")
        for p in prompts.prompts:
            print(f"   - {p.name}: {(p.description or '').splitlines()[0]}")

        # ===== 3. 调一个 Tool =====
        print("\n🔧 调用 add(40, 2)：")
        result = await client.call_tool("add", {"a": 40, "b": 2})
        print(f"   → {result.content[0].text}  structured={result.structured_content}")

        print("\n🔧 调用 current_time(timezone='America/New_York')：")
        result = await client.call_tool("current_time", {"timezone": "America/New_York"})
        print(f"   → {result.content[0].text}")

        # ===== 4. 读一个 Resource =====
        print("\n📖 读 hello://greeting/Claude：")
        res = await client.read_resource("hello://greeting/Claude")
        print(f"   → {res.contents[0].text}")

        # ===== 5. 拿一个 Prompt =====
        print("\n📝 拿 code-review 模板：")
        pr = await client.get_prompt("code-review", {"file": "auth.py"})
        print(f"   → 共 {len(pr.messages)} 条消息")
        for m in pr.messages:
            text = getattr(m.content, "text", str(m.content))
            snippet = text[:80].replace("\n", " ")
            print(f"      [{m.role}] {snippet}...")


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
python demos/basics/06_first_client.py
```

预期输出：

```
🤝 已连接 hello-mcp
   协议版本：2026-07-28
   能力：{'prompts': {'list_changed': True}, 'resources': {'subscribe': True, 'list_changed': True}, 'tools': {'list_changed': True}}

📦 工具数量：2
   - add: 两个整数相加。
   - current_time: 获取当前时间（按指定时区）。

📚 资源模板：1
   - hello://greeting/{name}

💡 Prompt：1
   - code-review: 生成一段 code review 引导。

🔧 调用 add(40, 2)：
   → 42  structured={'result': 42}

🔧 调用 current_time(timezone='America/New_York')：
   → 2026-11-02 11:17:05 EST-0500

📖 读 hello://greeting/Claude：
   → 你好，Claude！欢迎使用 MCP。今天是 2026-11-03。

📝 拿 code-review 模板：
   → 共 1 条消息
      [user] 请对 python 文件 `auth.py` 做代码 review，关注以下方面： 1. 安全性（注入、敏感信息泄漏） 2. 性能（明显的算法或 IO 瓶颈） ...
```

> 和 v1 对比一下：v1 要写 `async with stdio_client(params) as (read, write): async with ClientSession(read, write) as session: await session.initialize()`，字段是 camelCase（`resourceTemplates`、`uriTemplate`）。v2 只用一个 `Client`，字段都是 snake_case（`resource_templates`、`uri_template`），线上传输的 JSON 仍然是 camelCase。


这段代码已经是一个 MCP Host 的雏形，它做的就是 Claude Code 内部那个 Client 的工作。

---

## 5. 方式三：接进 Claude Code

> 没装 Claude Code 的话可以先跳过，读完 [在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md) 再回来。

### 5.1 添加 Server 配置

用命令行添加：

```bash
claude mcp add hello-mcp -- python /absolute/path/to/03-mcp/demos/basics/06_first_server.py
```

也可以写在项目根目录的 `.mcp.json` 里（可以提交进仓库，团队共享）：

```json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": ["/absolute/path/to/03-mcp/demos/basics/06_first_server.py"]
    }
  }
}
```

路径要换成你本机的绝对路径；`python` 要指向装了 `mcp` 的那个环境（用虚拟环境的话就写 `.venv/bin/python` 的绝对路径）。详见[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)。

### 5.2 确认已经连上

重启 Claude Code，输入 `/mcp` 查看各 Server 的连接状态，或者在对话里问：

```
列一下你能用的 MCP 工具
```

Claude 应该回答它能用 hello-mcp 这个 Server 提供的 `add` 和 `current_time` 工具。

### 5.3 实际对话

```
用 hello-mcp 算一下 17 * 19，再告诉我现在纽约时间
```

Claude 会这样处理：
1. `add` 做不了乘法，Claude 会直接自己算出 323，不会去调 `add`
2. 调用 `current_time`，参数 `timezone="America/New_York"`，把结果告诉你

Claude Code 的界面上会弹出工具调用的气泡，显示 input 和 output。

### 5.4 用 Prompt

在 Claude Code 里输入 `/`，菜单里能看到 `/hello-mcp:code-review (MCP)`；直接输入时写成 `/mcp__服务器名__prompt名`，参数按位置用空格隔开：

```
/mcp__hello-mcp__code-review src/auth.py
```

按 Enter，Claude Code 会用 prompt 模板里的文本开启对话，比自己打字快不少。

### 5.5 用 Resource

在 Claude Code 里，资源一般通过 `@` 提及，或者在 UI 里附加。具体见[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)。

---

## 6. 目录结构

做完上面这些，`03-mcp/demos/basics/` 应该是这样：

```
demos/basics/
├── 00_self_check.py             # 环境自检（05-installation 写的）
├── 06_first_server.py           # 本篇 Server
└── 06_first_client.py           # 本篇 Client
```

---

## 7. 这一篇打通了什么

到这里，Server、Client、Host 三端已经连通：

```
[Server 端]               [Client 端]                 [Host 端]
MCPServer 装饰器       <→  Client               <→  Claude Code
  ↓ 协议层 ↓            ↓ 协议层 ↓                ↓ 真实用户场景 ↓
JSON-RPC 2.0 over stdio
```

后面的章节都是在这条主链上往上加：

- Server 部分（从[用 Tools 暴露可调用的操作](../02-server/01-tools.md)开始）：把工具、资源、提示的细节用全（参数 schema、错误返回、订阅、生命周期注入、Tasks 异步等）
- Client 部分（从[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)开始）：把客户端能力用全（传输、多轮请求与 Elicitation、已弃用的 Sampling/Roots、多 Server 聚合）
- 集成部分（从[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)开始）：换 Host（Cursor、VS Code、ChatGPT），换框架（LangChain、Pydantic AI）
- 生产部分（从[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)开始）：换传输层（远程 HTTP + OAuth），换部署形态
- 进阶和实战部分（从[用 MCP Apps 在客户端中嵌入交互式 UI](../06-advanced/01-mcp-apps.md)和[内部知识库 MCP Server](../07-practice/01-project-internal-kb.md)开始）：进阶特性和实战项目

---

## 8. 常见坑

| 现象 | 原因 |
|------|------|
| Client 卡在连接或协商阶段 | Server 里的 `print()` 污染了 stdout，或者文件路径不对，或者 Python 解释器版本不匹配 |
| Inspector 显示 "0 tools" | 装饰器位置不对（必须在模块顶层），或者 mcp 实例没在 `if __name__ ...` 块外面创建 |
| Claude Code 里看不到 Server | `.mcp.json` 或 `claude mcp add` 里的路径写错了（建议写绝对路径），项目级 Server 还没批准，或者 Claude Code 没重启 |
| `mcp.run()` 之后立即退出 | 可能是在 jupyter notebook 里跑，或者脚本里混入了别的 asyncio loop；直接用 `python xxx.py` 运行 |
| 资源模板的参数没传 | `hello://greeting/{name}` 必须填 name，URI 要写完整：`hello://greeting/张三` |

---

## 9. 下一步：进入 Server 部分

基础部分到这里结束。下一章讲 Server，每篇深入一个原语或特性：

- [用 Tools 暴露可调用的操作](../02-server/01-tools.md)：参数 schema 细节、返回结构化内容、错误返回、annotations
- [用 Resources 提供上下文数据](../02-server/02-resources.md)：模板补全、订阅、二进制资源
- [用 Prompts 封装可复用的对话模板](../02-server/03-prompts.md)：多轮对话模板、引用 Resource、参数补全
- [用 Lifespan 和 Context 向工具注入共享资源](../02-server/04-lifespan-context.md)：启动钩子、用 Context 注入 DB 连接
- [用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)：长任务（Tasks 已移出核心协议，变成扩展）
- ……

## 参考资料

- 官方 Build a Server 教程：https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server
- Python SDK First steps：https://py.sdk.modelcontextprotocol.io/get-started/first-steps/
- MCPServer 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/mcpserver
- Reference everything server：https://github.com/modelcontextprotocol/servers/tree/main/src/everything
