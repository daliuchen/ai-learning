# MCP 05：安装 Python SDK 与 Inspector

> **一句话**：开发 MCP 你只需要装两个东西——Python `mcp` SDK（写 Server / Client）和 Node 命令行 `@modelcontextprotocol/inspector`（可视化调试）。下面是最小一份"一次性配齐"的步骤。

---

## 1. 环境要求

| 软件 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.10+ | SDK 用 `match`、`PEP 604` 类型语法 |
| **Node.js** | 18+ | Inspector 跑在 Node |
| **uv** 或 `pip` | 任意 | 包管理。本手册以 `pip` 为主，附 `uv` 等价命令 |
| **Claude Code / Cursor** | 任意新版 | 用作 Host 测试 |

> macOS 自带 Python 是 3.9，请用 `brew install python@3.12` 或 `pyenv` 装 3.10+。

检查环境：

```bash
python --version    # 应 >= 3.10
node --version      # 应 >= 18
pip --version
```

---

## 2. 装 Python SDK

### 2.1 推荐：直接安装本手册的全套依赖

本手册根目录 `requirements.txt` 已经把所有篇章用到的包都列了：

```bash
cd 03-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2.2 最小安装

只想跑 01-basics 的 demo？最小化：

```bash
pip install "mcp>=1.10.0"
```

`mcp` 这个包同时包含 Server SDK（含高层 `FastMCP`、低层 `Server`）和 Client SDK（`ClientSession` + 各种 transport）。

### 2.3 用 uv（推荐）

如果你已经用 `uv`：

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

后续所有命令都用 `uv pip`、`uv run` 也都成立，本手册示例都用 `pip`/`python` 保持通用。

### 2.4 验证安装

```bash
python -c "import mcp; print(mcp.__version__)"
# 1.10.x （或更新）
```

```python
# 启 REPL 看看模块
from mcp.server.fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.stdio import stdio_client
print("✅ SDK 装好了")
```

---

## 3. 装 Inspector

Inspector 是官方提供的可视化调试工具，**完全免费、本地运行、不上传数据**。强烈建议每个写 MCP Server 的人都装。

### 3.1 临时启动（不装到全局）

```bash
npx @modelcontextprotocol/inspector python server.py
```

`npx` 会自动下载并跑一次。第一次会拉几十兆，耐心等。

### 3.2 装到全局

```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector python server.py
```

### 3.3 用 Inspector 调试一个 Server

Inspector 启动后会做两件事：
1. 在 `http://localhost:6274` 启动 Web UI
2. 在 `localhost:6277` 启动一个代理，把 Web UI 的 RPC 调用转发给 Server

打开浏览器：

```
http://localhost:6274
```

会看到三栏布局：
- **左侧**：连接配置（Transport、命令、参数、环境变量）
- **中间**：原语操作（Tools / Resources / Prompts 三个 tab）
- **右侧**：消息历史（看到完整 JSON-RPC 流量）

操作流程：
1. 选 Transport = `stdio`，填命令 `python` 和参数 `server.py`
2. 点 **Connect**
3. 看到 "Capabilities: tools, resources, ..." 就握手成功
4. 切到 **Tools** tab → 点某个工具 → 填参数 → **Run Tool**
5. 右侧能看到 raw JSON-RPC 请求与响应

### 3.4 Inspector 远程模式

要调远程 MCP（Streamable HTTP）：

```bash
npx @modelcontextprotocol/inspector
# 浏览器里把 Transport 选 "Streamable HTTP"，填 URL
```

详细玩法见 05-production/05-debugging-inspector。

---

## 4. 配置环境变量

把 `.env.example` 复制成 `.env`：

```bash
cp .env.example .env
```

本章只用到 LLM Key（后续 03-client/03-sampling、04-integration 才需要）：

```bash
# .env
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
```

测试加载：

```python
import os
from dotenv import load_dotenv
load_dotenv()
print("OPENAI_API_KEY 长度:", len(os.getenv("OPENAI_API_KEY", "")))
```

---

## 5. IDE 配置（可选，但强推）

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

`mcp` SDK 类型注解完整，开 typeCheckingMode 能在写 `@mcp.tool()` 装饰函数时拿到完整提示。

### 5.2 PyCharm
直接打开 03-mcp 目录，让 PyCharm 识别 `.venv`。`mcp` SDK 自带 PyI 文件，类型推导开箱即用。

---

## 6. 一次性自检脚本

把下面这段保存为 `demos/basics/00_self_check.py` 跑一下，全过就说明环境彻底没问题。

```python
# demos/basics/00_self_check.py
"""自检脚本：验证 MCP SDK 安装 & 基本功能"""
import asyncio
import sys


def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    print(f"{'✅' if ok else '❌'} Python {major}.{minor} (需要 >=3.10)")
    return ok


def check_mcp_import() -> bool:
    try:
        import mcp
        from mcp.server.fastmcp import FastMCP
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters
        print(f"✅ mcp == {mcp.__version__}")
        return True
    except ImportError as e:
        print(f"❌ mcp 导入失败：{e}")
        return False


async def check_client_server_roundtrip() -> bool:
    """启一个临时 Server，自己当 Client 连它，跑一遍完整流程"""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    inline_server = '''
import sys
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("self-check")

@mcp.tool()
def ping(msg: str = "hi") -> str:
    """回声测试工具"""
    return f"pong: {msg}"

mcp.run()
'''
    import tempfile, os, pathlib
    tmp = pathlib.Path(tempfile.gettempdir()) / "_mcp_self_check_server.py"
    tmp.write_text(inline_server)

    params = StdioServerParameters(command=sys.executable, args=[str(tmp)])
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "self-check"
                tools = await session.list_tools()
                assert any(t.name == "ping" for t in tools.tools)
                result = await session.call_tool("ping", {"msg": "ok"})
                text = result.content[0].text
                assert "pong: ok" in text
                print("✅ Client/Server 完整流程跑通")
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
    else:
        print("⚠️  请按上方提示修复后再继续")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

跑：

```bash
python demos/basics/00_self_check.py
```

期望输出：

```
=== MCP 环境自检 ===
✅ Python 3.12 (需要 >=3.10)
✅ mcp == 1.10.x
✅ Client/Server 完整流程跑通

🎉 一切正常，可以开始 06-first-server.md
```

---

## 7. 装一个官方 Reference Server 试手

官方维护了一仓库 [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)，里头有 filesystem、git、postgres 等参考实现。

跑一下官方 `everything` server（含三种原语示例）：

```bash
# 这是 TS 实现，需要 npx
npx -y @modelcontextprotocol/server-everything
```

或者 filesystem server：

```bash
npx -y @modelcontextprotocol/server-filesystem /tmp/my-workspace
```

用 Inspector 连上去看一眼真实生产 MCP Server 的样子，再回来写自己的，会很有体感。

---

## 8. 常见坑

| 坑 | 解决 |
|----|------|
| **`ModuleNotFoundError: No module named 'mcp'`** | 没装 / 没激活 venv。先 `source .venv/bin/activate` 再 `pip install mcp` |
| **`mcp.server.fastmcp` 不存在** | SDK 版本太低（<0.5），升级：`pip install -U mcp` |
| **Inspector 一直 "Connecting..."** | Server 命令路径错 / Server 启动失败。直接 `python server.py` 跑一下看报错 |
| **Inspector 显示 capabilities 是空的** | Server 端 print() 污染了 stdout，握手响应被破坏 |
| **`npx` 卡在下载** | Node 网络问题。把 npm registry 切到 https://registry.npmmirror.com 或预先 `npm install -g @modelcontextprotocol/inspector` |
| **macOS 系统 Python (3.9) 与项目 venv 冲突** | 始终用 venv 里的 python：`./.venv/bin/python ...` 或先 activate |

---

## 9. 下一步

- 🛠️ 跑通 Hello World Server → [06-first-server.md](./06-first-server.md)
- 🔍 想知道 SDK 的高层 vs 低层 API → 02-server/01-tools
- 🔍 想知道 Inspector 全部用法 → 05-production/05-debugging-inspector

## 参考资料

- Python SDK：https://github.com/modelcontextprotocol/python-sdk
- Inspector：https://github.com/modelcontextprotocol/inspector
- 官方 Reference Servers：https://github.com/modelcontextprotocol/servers
