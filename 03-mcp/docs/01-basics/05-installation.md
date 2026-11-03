# MCP 05：安装 Python SDK 与 Inspector

> 开发 MCP 需要装的东西其实只有两样：写 Server 和 Client 用的 Python `mcp` SDK，以及做可视化调试的 Node 命令行工具 `@modelcontextprotocol/inspector`。这篇把它们一次配齐，最后用一个自检脚本确认环境没问题。

---

## 1. 环境要求

| 软件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | `mcp` 2.3.0 包本身声明 `>=3.10`，本手册的代码按 3.11+ 写，README 里的环境用 3.12 |
| Node.js | 22.19+ | 最新版 Inspector 的要求（0.x 版 Inspector 只要 18+） |
| uv 或 `pip` | 任意 | 包管理。本手册以 `pip` 为主，附 `uv` 的等价命令 |
| Claude Code / Cursor | 任意新版 | 作为 Host 做测试 |

> macOS 自带的 Python 是 3.9，请用 `brew install python@3.12`、`pyenv` 或 conda 装 3.11 以上的版本。

检查环境：

```bash
python --version    # 应 >= 3.11
node --version      # 应 >= 22.19（只用 Inspector 才需要）
pip --version
```

---

## 2. 安装 Python SDK

### 2.1 推荐做法：装本手册的全套依赖

手册根目录的 `requirements.txt` 列出了所有篇章用到的包：

```bash
cd 03-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2.2 最小安装

只想跑 01-basics 的 demo，装这些就够：

```bash
pip install "mcp>=2.3"          # 要用 mcp dev / mcp run 命令行再加 extras：pip install "mcp[cli]>=2.3"
```

`mcp` 这个包同时包含 Server SDK（高层的 `MCPServer`、低层的 `Server`）和 Client SDK（新的 `Client`、底层的 `ClientSession` 以及各种 transport）。协议类型拆到了独立的 `mcp-types` 包里，会作为依赖自动装上，访问方式仍然是 `from mcp.types import ...`。

> 如果是从 v1 升级：v2 是大版本，`FastMCP` 改名为 `MCPServer`，`mcp.server.fastmcp` 整个模块没了，类型字段全部改成 snake_case（`inputSchema` → `input_schema`），HTTP 客户端换成了 `httpx2`。官方有完整的[迁移指南](https://py.sdk.modelcontextprotocol.io/migration/)。如果你的包暂时没法升级，就写 `mcp>=1.28,<2` 钉在 1.x（1.x 已进入维护期，只修关键 bug）。

### 2.3 用 uv

已经在用 `uv` 的话：

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

后面的命令换成 `uv pip`、`uv run` 也都能用。为了通用，手册里的示例统一写 `pip`/`python`。

### 2.4 验证安装

```bash
pip show mcp | grep Version
# Version: 2.3.0 （或更新；mcp 包没有 __version__ 属性）
```

```python
# 启 REPL 看看模块
from mcp.server import MCPServer
from mcp import Client, StdioServerParameters
print("✅ SDK 装好了")
```

---

## 3. 安装 Inspector

Inspector 是官方的调试工具，免费、在本地运行、不上传数据，写 MCP Server 的话建议装上。最新版（`@modelcontextprotocol/inspector` 2.x）一个包里提供三种客户端：Web（默认）、`--cli`（适合脚本、CI 和编码 Agent）、`--tui`（终端界面），另外还带一个实验性的 `mcpdo`（连一次、发多条命令）。

### 3.1 临时启动（不装到全局）

```bash
npx @modelcontextprotocol/inspector python server.py
```

`npx` 会自动下载并运行一次。第一次要拉几十兆，需要等一会儿。

### 3.2 装到全局

```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector python server.py          # 默认 Web；加 --cli / --tui 切换形态
mcp-inspector --cli python server.py --method tools/list        # 命令行，一次列工具
```

> 也可以不单独装：装了 `mcp[cli]` 之后直接 `mcp dev server.py`，SDK 会替你拉起 Inspector。

### 3.3 用 Inspector 调试 Server

Inspector 启动后，终端会打印一个带本次启动 token 的 URL，并自动在浏览器里打开（0.x 版是固定的 `http://localhost:6274`；设置 `MCP_AUTO_OPEN_ENABLED=false` 可以不自动打开）。Web UI 背后有一个本地代理，负责拉起 stdio 子进程或连接远程 Server。

页面是三栏布局：左边是连接配置（Transport、命令、参数、环境变量），中间是原语操作（Tools / Resources / Prompts 三个 tab），右边是消息历史，能看到完整的 JSON-RPC 流量。

操作步骤：
1. Transport 选 `stdio`，命令填 `python`，参数填 `server.py`
2. 点 Connect
3. 能看到 Server 的 capabilities（tools、resources 等）就说明协商成功。Inspector 会自动判断对面是 2026 版还是 2025 版（官方文档叫 protocol eras）
4. 切到 Tools tab，点某个工具，填参数，点 Run Tool
5. 在右侧查看原始的 JSON-RPC 请求和响应

### 3.4 连接远程 Server

调试远程 MCP（Streamable HTTP）：

```bash
npx @modelcontextprotocol/inspector
# 浏览器里把 Transport 选 "Streamable HTTP"，填 URL
npx @modelcontextprotocol/inspector --server-url http://localhost:8000/mcp --transport http
```

更多用法见[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)。

---

## 4. 配置环境变量

把 `.env.example` 复制成 `.env`：

```bash
cp .env.example .env
```

本章不需要任何 Key，只有后面用真实模型的 demo 才需要（03-client 的最小 Agent、04-integration 的两篇、07-practice）。任何 OpenAI 兼容的服务都可以，手册示例用的是阿里云百炼的千问 `qwen-flash`：

```bash
# .env
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-flash
```

测试一下能否加载：

```python
import os
from dotenv import load_dotenv
load_dotenv()
print("OPENAI_API_KEY 长度:", len(os.getenv("OPENAI_API_KEY", "")))
```

---

## 5. IDE 配置（可选）

### 5.1 VS Code / Cursor

`.vscode/settings.json`：

```json
{
  "python.analysis.typeCheckingMode": "basic",
  "python.analysis.autoImportCompletions": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true
  }
}
```

`mcp` SDK 的类型注解很完整，打开 typeCheckingMode 后，写 `@mcp.tool()` 修饰的函数时能拿到完整提示。

### 5.2 PyCharm
直接打开 03-mcp 目录，让 PyCharm 识别 `.venv` 即可。`mcp` SDK 带 `py.typed` 标记（类型注解写在源码里），类型推导不需要额外配置。

---

## 6. 自检脚本

把下面这段存成 `demos/basics/00_self_check.py` 跑一遍，全部通过就说明环境没问题。

```python
# demos/basics/00_self_check.py
"""自检脚本：验证 MCP SDK 安装 & 基本功能（mcp 2.x）

跑法:
    python demos/basics/00_self_check.py

期望:
    全部 ✅, 退出码 0
"""
from __future__ import annotations

import asyncio
import sys
from importlib.metadata import PackageNotFoundError, version


def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    print(f"{'✅' if ok else '❌'} Python {major}.{minor} (需要 >=3.11)")
    return ok


def check_mcp_import() -> bool:
    try:
        from mcp import Client, StdioServerParameters  # noqa: F401
        from mcp.server import MCPServer  # noqa: F401

        try:
            v = version("mcp")
        except PackageNotFoundError:
            v = "unknown"
        major = int(v.split(".")[0]) if v[0].isdigit() else 0
        print(f"{'✅' if major >= 2 else '⚠️ '} mcp == {v} (本手册基于 2.x)")
        return True
    except ImportError as e:
        print(f"❌ mcp 导入失败：{e}")
        print("   修复：pip install -U 'mcp>=2.3'")
        print("   若你的环境是 mcp 1.x，把 `from mcp.server import MCPServer` 换成 `from mcp.server.fastmcp import FastMCP`")
        return False


async def check_client_server_roundtrip() -> bool:
    """启一个临时 Server，自己当 Client 连它，跑一遍完整流程"""
    import pathlib
    import tempfile

    from mcp import Client, StdioServerParameters

    inline_server = """
from mcp.server import MCPServer

mcp = MCPServer("self-check")

@mcp.tool()
def ping(msg: str = "hi") -> str:
    \"\"\"回声测试工具\"\"\"
    return f"pong: {msg}"

mcp.run()
"""
    tmp = pathlib.Path(tempfile.gettempdir()) / "_mcp_self_check_server.py"
    tmp.write_text(inline_server)

    params = StdioServerParameters(command=sys.executable, args=[str(tmp)])
    try:
        async with Client(params) as client:
            assert client.server_info and client.server_info.name == "self-check", client.server_info
            tools = await client.list_tools()
            assert any(t.name == "ping" for t in tools.tools), "ping 工具没找到"
            result = await client.call_tool("ping", {"msg": "ok"})
            assert "pong: ok" in result.content[0].text, result
            print(f"✅ Client/Server 完整流程跑通 (协议 {client.protocol_version}: 协商 → list_tools → call_tool)")
            return True
    except Exception as e:
        print(f"❌ Client/Server 自检失败：{e!r}")
        return False
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    print("=== MCP 环境自检 ===")
    ok = True
    ok &= check_python_version()
    ok &= check_mcp_import()
    ok &= asyncio.run(check_client_server_roundtrip())
    print()
    if ok:
        print("🎉 一切正常，可以开始 06-first-server.md")
        return 0
    print("⚠️  请按上方提示修复后再继续")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

运行：

```bash
python demos/basics/00_self_check.py
```

期望输出：

```
=== MCP 环境自检 ===
✅ Python 3.12 (需要 >=3.11)
✅ mcp == 2.3.0 (本手册基于 2.x)
✅ Client/Server 完整流程跑通 (协议 2026-07-28: 协商 → list_tools → call_tool)

🎉 一切正常，可以开始 06-first-server.md
```

---

## 7. 先跑一个官方 Reference Server

官方维护的 [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) 仓库里有 filesystem、git、fetch、memory 等参考实现。

可以先跑官方的 `everything` server（包含三种原语的示例）：

```bash
# 这是 TS 实现，需要 npx
npx -y @modelcontextprotocol/server-everything
```

或者 filesystem server：

```bash
npx -y @modelcontextprotocol/server-filesystem /tmp/my-workspace
```

用 Inspector 连上去，看看真实的 MCP Server 是什么样，再回来写自己的会顺手很多。

---

## 8. 常见坑

| 坑 | 解决 |
|----|------|
| `ModuleNotFoundError: No module named 'mcp'` | 没装，或者没激活 venv。先 `source .venv/bin/activate`，再 `pip install mcp` |
| `No module named 'mcp.server.fastmcp'` | 装的是 mcp 2.x，`FastMCP` 已经改名，用 `from mcp.server import MCPServer`。要继续跑 v1 代码就 `pip install "mcp<2"` |
| Inspector 启动时报 Node 版本过低 | 新版 Inspector 要求 Node 22.19+。升级 Node，或者临时改用 0.x 版：`npx @modelcontextprotocol/inspector@0.x` |
| Inspector 一直显示 "Connecting..." | Server 命令路径写错了，或者 Server 启动失败。直接 `python server.py` 跑一下看报错 |
| Inspector 里 capabilities 是空的 | Server 里的 print() 污染了 stdout，握手响应被破坏 |
| `npx` 卡在下载 | Node 网络问题。把 npm registry 切到 https://registry.npmmirror.com ，或者提前 `npm install -g @modelcontextprotocol/inspector` |
| macOS 系统 Python (3.9) 和项目 venv 冲突 | 始终用 venv 里的 python：`./.venv/bin/python ...`，或者先 activate |

---

## 9. 下一步

- 跑通 Hello World Server：[写第一个 MCP Server 和 Client](./06-first-server.md)
- SDK 的高层与低层 API：[用 Tools 暴露可调用的操作](../02-server/01-tools.md)
- Inspector 的全部用法：[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)

## 参考资料

- Python SDK：https://github.com/modelcontextprotocol/python-sdk
- Inspector：https://github.com/modelcontextprotocol/inspector
- 官方 Reference Servers：https://github.com/modelcontextprotocol/servers
