"""MCP Apps：用官方 Python SDK 内置的 Apps 扩展（io.modelcontextprotocol/ui）做一个「带界面的工具」

一个 MCP App = 一个 tool + 一个 ui:// 资源（text/html;profile=mcp-app）。Server 只负责提供这两样东西；
iframe 里的 postMessage 流量发生在 Host 与 iframe 之间，Server 根本看不到。

跑法（进程内自测：协商 Apps 与不协商时，同一个工具返回不同的内容；并读出 UI 资源）:
    python demos/advanced/01_mcp_app_clock.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from mcp import Client
from mcp.client import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID, Apps, ResourceCsp, client_supports_apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

CLOCK_HTML = """\
<!doctype html>
<title>Clock</title>
<h1 id="now">...</h1>
<script>
  const parent = window.parent;
  let initialized = false;
  window.addEventListener("message", (event) => {
    if (event.source !== parent || event.data?.jsonrpc !== "2.0") return;   // 永远校验来源和数据形状
    if (event.data.id === 1 && event.data.result?.protocolVersion) {
      parent.postMessage({jsonrpc: "2.0", method: "ui/notifications/initialized"}, "*");
      initialized = true;
      return;
    }
    if (!initialized || event.data.method !== "ui/notifications/tool-result") return;
    const text = event.data.params?.content?.[0]?.text;
    if (typeof text === "string") document.getElementById("now").textContent = text;
  });
  parent.postMessage({jsonrpc: "2.0", id: 1, method: "ui/initialize",
    params: {appInfo: {name: "clock", version: "1.0.0"}, appCapabilities: {}, protocolVersion: "2026-01-26"}}, "*");
</script>
"""

apps = Apps()


@apps.tool(resource_uri="ui://clock/app.html", description="The current time.")
def get_time(ctx: Context) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if not client_supports_apps(ctx):  # 优雅降级：文本客户端和模型只会看到 content，必须是有意义的一句话
        return f"The time is {now}."
    return now


# CSP / 权限放在「资源」上（不是工具上）：这是给 Host 的请求，Host 可以拒绝
apps.add_html_resource("ui://clock/app.html", CLOCK_HTML, title="Clock", csp=ResourceCsp(connect_domains=[]), prefers_border=True)

mcp = MCPServer("clock", extensions=[apps])  # 声明 io.modelcontextprotocol/ui 能力


async def main() -> None:
    supports_apps = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
    async with Client(mcp, extensions=[supports_apps]) as rich, Client(mcp) as plain:
        print("Server 声明的扩展:", sorted((rich.server_capabilities.extensions or {}).keys()))
        tool = next(t for t in (await rich.list_tools()).tools if t.name == "get_time")
        print("tool._meta       :", tool.meta)
        print("支持 Apps 的客户端:", (await rich.call_tool("get_time", {})).content[0].text)
        print("纯文本客户端     :", (await plain.call_tool("get_time", {})).content[0].text)
        res = (await rich.read_resource("ui://clock/app.html")).contents[0]
        print("UI 资源          :", res.mime_type, "|", res.text.splitlines()[1])


if __name__ == "__main__":
    asyncio.run(main())
