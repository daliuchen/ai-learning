# MCP Client 04：Roots + Elicitation —— 沙盒边界与中途询问

> **一句话**：**Roots** 让 Client 告诉 Server "你可访问哪些目录"（沙盒边界提示）；**Elicitation** 让 Server 在执行过程中"中途请求用户输入"。前者是协作约束，后者是 UI 交互通道。两个都是客户端能力，让 Server 行为更受控、更智能。

---

## 1. Roots：沙盒边界

### 1.1 定位

> **Roots are coordination, NOT security.**

Roots 是 Client 告诉 Server "**你应该**只在这些目录下干活"。但**不强制**——Server 行为不受底层 OS 沙盒约束，所以是"礼貌建议"，不是安全屏障。**真正的安全要靠 OS 文件权限、容器、AppArmor 等。**

适用场景：
- IDE 把当前 workspace 暴露给 filesystem MCP Server
- 用户切换项目时，roots 跟着变
- 多项目聚合到一个 Server 里时区分边界

### 1.2 协议

Client 必须声明：

```json
{"capabilities": {"roots": {"listChanged": true}}}
```

Server 调：

```json
{"method": "roots/list"}
```

Client 回：

```json
{
  "roots": [
    {"uri": "file:///Users/me/projects/foo", "name": "Foo Project"},
    {"uri": "file:///Users/me/projects/bar", "name": "Bar Project"}
  ]
}
```

URI 必须是 `file://` scheme（只支持文件系统）。

变更时 Client 发：

```json
{"method": "notifications/roots/list_changed"}
```

Server 收到后重新调 `roots/list`。

### 1.3 Python Client 端实现

```python
from mcp import ClientSession
from mcp.types import Root, ListRootsResult


async def list_roots(request_ctx) -> ListRootsResult:
    return ListRootsResult(roots=[
        Root(uri="file:///Users/me/projects/foo", name="Foo"),
        Root(uri="file:///Users/me/projects/bar", name="Bar"),
    ])


async with ClientSession(read, write, list_roots_callback=list_roots) as session:
    await session.initialize()
    ...
```

切换项目时主动通知 Server：

```python
await session.send_roots_list_changed()
```

### 1.4 Python Server 端用 Roots

```python
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("fs-server")


@mcp.tool()
async def list_files(ctx: Context) -> list[str]:
    """列出所有 roots 下的文件"""
    # 通过 ctx.session 拿 roots
    roots_result = await ctx.session.list_roots()

    out = []
    for root in roots_result.roots:
        path = root.uri.replace("file://", "")
        from pathlib import Path
        for f in Path(path).rglob("*"):
            if f.is_file():
                out.append(str(f.relative_to(path)))
    return out
```

### 1.5 Roots 设计要点

- **不要把 roots 当 enforcement**：用户可能拒绝/没声明，Server 还是能拿到其他路径
- **roots 是可信提示**：让 Server 做合理范围
- **路径合法性**：Server 端做了文件访问后仍要做边界检查（防越权）

---

## 2. Elicitation：中途询问用户

### 2.1 定位

Server 跑工具跑到一半发现需要更多信息——比如订票时模型给的航班需要用户确认座位偏好——它可以**反向**问用户。

Client 端弹 UI（表单 / 链接），用户填，回传 Server。

### 2.2 协议

Client 必须声明：

```json
{
  "capabilities": {
    "elicitation": {
      "form": {},     // 支持表单
      "url": {}       // 支持跳到外部 URL（2025-11-25 新增）
    }
  }
}
```

Server 调：

```json
{
  "method": "elicitation/create",
  "params": {
    "message": "确认你的巴塞罗那行程：",
    "schema": {
      "type": "object",
      "properties": {
        "confirmBooking": {
          "type": "boolean",
          "description": "确认下单（机票+酒店 $3000）"
        },
        "seatPreference": {
          "type": "string",
          "enum": ["window", "aisle", "no_preference"],
          "description": "座位偏好"
        },
        "addInsurance": {
          "type": "boolean",
          "default": false,
          "description": "购买保险 ($150)"
        }
      },
      "required": ["confirmBooking"]
    }
  }
}
```

Client 弹 UI 让用户填，回：

```json
{
  "action": "accept",
  "content": {
    "confirmBooking": true,
    "seatPreference": "window",
    "addInsurance": false
  }
}
```

`action` 可能是：
- `"accept"`：用户填好提交
- `"decline"`：用户拒绝
- `"cancel"`：用户关掉对话框

### 2.3 Schema 限制

Elicitation schema **必须是简单的 object**，每个字段必须是**基本类型**（string / number / integer / boolean / enum / format=date|email|uri 等）。

**不能**有嵌套 object、不能 oneOf / anyOf。这是为了让 Client 能用通用表单 UI 渲染。

### 2.4 URL Mode（带外交互）

2025-11-25 新增的 `url` mode 让 Server 把用户跳到外部 URL（比如 OAuth 授权、外部支付）：

```json
{
  "method": "elicitation/create",
  "params": {
    "message": "请在外部页面完成支付",
    "mode": "url",
    "url": "https://payment.example.com/checkout?session=abc"
  }
}
```

Client 把用户跳过去 → 用户在外部完成 → 通过其他通道（比如 webhook 回 Server）回传结果。

### 2.5 Python Client 端实现

```python
from mcp import ClientSession
from mcp.types import ElicitResult


async def on_elicit(params, request_ctx) -> ElicitResult:
    """Server 想问用户问题"""
    print(f"\n💬 Server 问: {params.message}")
    print(f"   字段需要: {list(params.schema.get('properties', {}).keys())}")

    # 真实 Host 应该弹 UI；这里只读简单 stdin
    user_input = input("回答（y/n + 用空格补充字段）: ")
    if user_input.lower().startswith("n"):
        return ElicitResult(action="decline")
    # 简化处理
    return ElicitResult(action="accept", content={"confirmBooking": True})


async with ClientSession(read, write, elicitation_callback=on_elicit) as session:
    ...
```

### 2.6 Python Server 端发起 Elicit

```python
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("booking")


@mcp.tool()
async def book_trip(destination: str, ctx: Context) -> dict:
    """订旅行套餐，过程中确认细节"""
    # 中途询问
    confirm = await ctx.elicit(
        message=f"确认下单去 {destination} 的套餐 ($3000)？",
        schema={
            "type": "object",
            "properties": {
                "confirmBooking": {"type": "boolean"},
                "seatPreference": {"type": "string", "enum": ["window", "aisle"]},
                "addInsurance": {"type": "boolean", "default": False},
            },
            "required": ["confirmBooking"],
        },
    )

    if confirm.action != "accept":
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError(f"用户取消下单: action={confirm.action}")

    data = confirm.content
    if not data.get("confirmBooking"):
        raise ToolError("用户未确认下单")

    # 进入真正下单
    return {"booking_id": "abc", "seat": data.get("seatPreference")}
```

---

## 3. Roots 和 Elicitation 的协作

二者都是「Client 端能力」，但用途完全不同：

| 维度 | **Roots** | **Elicitation** |
|------|-----------|------------------|
| 谁主动 | Client（告诉 Server） | Server（问 Client） |
| 性质 | 边界声明 | 交互请求 |
| UI | 通常无 UI（IDE 自动维护） | 弹窗 / 表单 |
| 频率 | 项目级 / 较稳定 | 工具调用中 |
| 数据流向 | Client → Server | Server → Client → User → Client → Server |

---

## 4. 安全注意

| 风险 | 缓解 |
|------|------|
| **Elicitation 钓取敏感信息** | Server 不应通过 elicitation 收集密码 / API key —— spec 明令禁止 |
| **Elicitation 假冒系统提示** | UI 必须显著标明"这是 Server XXX 发起的请求" |
| **Roots 没声明 / 部分声明** | Server 不能依赖 roots 做安全约束，仍要做路径白名单 |
| **URL mode 钓鱼** | 跳外部 URL 前要校验 URL 合法性，弹警告 |

---

## 5. 综合 demo：项目分析 + 用户确认

```python
# demos/server/04_roots_elicit.py
"""Server 用 Roots 拿到目录、用 Elicitation 让用户确认是否分析"""
from pathlib import Path
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("project-analyzer")


@mcp.tool()
async def analyze_workspace(ctx: Context) -> dict:
    """分析当前 workspace（先问用户授权）"""
    # 1. 拿到 Client 声明的 roots
    roots_result = await ctx.session.list_roots()
    if not roots_result.roots:
        return {"error": "Client 没声明 roots，无法分析"}

    root_uri = roots_result.roots[0].uri
    root_path = root_uri.replace("file://", "")

    # 2. 问用户：要分析吗？是否包含 node_modules？
    confirm = await ctx.elicit(
        message=f"将分析目录: {root_path}。确认继续？",
        schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "确认分析"},
                "includeNodeModules": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否包含 node_modules（耗时）",
                },
            },
            "required": ["confirm"],
        },
    )

    if confirm.action != "accept" or not confirm.content.get("confirm"):
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError("用户取消分析")

    include_nm = confirm.content.get("includeNodeModules", False)

    # 3. 实际分析
    files = []
    for f in Path(root_path).rglob("*"):
        if "node_modules" in f.parts and not include_nm:
            continue
        if f.is_file():
            files.append(str(f.relative_to(root_path)))

    return {
        "root": root_path,
        "file_count": len(files),
        "sample": files[:10],
    }


if __name__ == "__main__":
    mcp.run()
```

Client 同时实现 `list_roots_callback` 和 `elicitation_callback`，跑下来用户会看到两次交互。

---

## 6. 常见坑

| 坑 | 排查 |
|----|------|
| **roots 当成安全约束** | 它只是约定，Server 端仍要做路径白名单 |
| **Elicitation schema 有 nested object** | UI 渲染不了；保持扁平 |
| **Elicitation 收密码** | spec 明令禁止；用 OAuth / 外部流程 |
| **没声明 roots/elicitation 能力** | Server 调用直接 -32601；要先检查 capabilities |
| **`action: accept` 但 content 为空** | 用户可能跳过可选字段，Server 端要处理 None |
| **roots 切换没 list_changed 通知** | Server 不会知道；要在 Client 主动调 send_roots_list_changed |

---

## 7. 下一步

- 📖 多 Server 聚合 + 综合最佳实践 → [05-multi-server-best-practices.md](./05-multi-server-best-practices.md)
- 📖 安全细节 → 05-production/04-security

## 参考资料

- Roots spec：https://modelcontextprotocol.io/specification/2025-11-25/client/roots
- Elicitation spec：https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation
- Client Concepts：https://modelcontextprotocol.io/docs/learn/client-concepts
