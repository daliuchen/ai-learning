# MCP Advanced 03：MCP Registry —— 把你的 Server 发布到官方注册表

> **一句话**：MCP Registry（registry.modelcontextprotocol.io）是官方维护的中心化元数据仓库——让你的 Server 在 npm / PyPI / Docker Hub 上的包能被全行业 Host 发现、统一安装。本篇讲 Registry 的工作模式 + 发布流程 + 命名规范。

> ⚠️ Registry 当前是 **preview**——可能有 breaking change 或数据重置。生产部署前关注官方 release notes。

---

## 1. Registry 解决什么问题

没 Registry 之前：

- 写完 Server → 发到 npm / PyPI → 用户翻文档找
- Host 内部各有"内置 Server 推荐列表"
- 同名 Server 鱼龙混杂、来源不可信

Registry 的定位：**MCP 生态的"包索引"**。

- 标准化 metadata（`server.json` 格式）
- DNS / GitHub 验证命名空间
- 集中化 REST API 让 Host / 聚合器统一拉数据
- 不存储包代码——指向 npm / PyPI / Docker Hub

---

## 2. 命名空间约定

Server 名用**反向 DNS** 格式：

| 来源 | 例子 |
|------|------|
| GitHub 用户/组织 | `io.github.username/server-name` |
| 自己的域名 | `com.example/server-name` |

发布前要做**命名空间验证**（DNS TXT 或 GitHub OAuth），保证别人不能冒充。

---

## 3. server.json 格式

每个发布到 Registry 的 Server 都要提供 `server.json`：

```json
{
  "name": "io.github.yourname/03-mcp-hello",
  "description": "本手册的 hello-mcp 演示 Server",
  "version": "1.0.0",
  "repository": {
    "url": "https://github.com/yourname/03-mcp"
  },
  "packages": [
    {
      "registry_type": "npm",
      "identifier": "@yourname/03-mcp-hello",
      "version": "1.0.0",
      "runtime_arguments": [],
      "environment_variables": []
    },
    {
      "registry_type": "pypi",
      "identifier": "yourname-03-mcp-hello",
      "version": "1.0.0"
    }
  ],
  "remotes": [
    {
      "url": "https://mcp.example.com/mcp",
      "transport": "streamable-http"
    }
  ]
}
```

字段：

| 字段 | 含义 |
|------|------|
| `name` | 反向 DNS 唯一名 |
| `description` | 一句话简介 |
| `version` | SemVer |
| `repository` | 源码仓库 |
| `packages` | 各包注册表的位置（npm / PyPI / Docker / Cargo / ...） |
| `remotes` | 远程 MCP URL（如果是 hosted） |

---

## 4. 支持的 Package Types

| Package Type | Identifier 示例 |
|-------------|----------------|
| `npm` | `@anthropic/mcp-server-foo` |
| `pypi` | `mcp-server-foo` |
| `dockerhub` | `anthropic/mcp-server-foo` |
| `oci` | `ghcr.io/anthropic/mcp-server-foo` |

更多类型按社区需求加。Registry 不存代码——这些字段告诉 Host **去哪装**。

---

## 5. 发布流程

### 5.1 用 `mcp-publisher` CLI

```bash
npm install -g @modelcontextprotocol/publisher

# 在含 server.json 的目录
mcp-publisher publish
```

第一次会让你做命名空间验证（OAuth 或 DNS TXT）。

### 5.2 用 GitHub Actions 自动发

```yaml
# .github/workflows/publish-mcp.yml
name: Publish to MCP Registry

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: modelcontextprotocol/publish-action@v1
        with:
          server-json: ./server.json
```

GitHub OIDC 自动验证 `io.github.<owner>/...` 命名空间。

---

## 6. Host / 聚合器消费 Registry

下游聚合器（marketplace、IDE 内嵌商店）每小时拉一次 Registry API：

```http
GET https://registry.modelcontextprotocol.io/v0/servers?limit=100&cursor=...
```

返回所有 Server 的 metadata。聚合器自己做：

- 评分 / 评论 / 分类
- 安全审核 / 标签
- 推荐算法

**官方 Registry 自己不做这些**——它只管 metadata。

---

## 7. 私有 / 公开 Server

| 类型 | 能发到官方 Registry 吗 |
|------|----------------------|
| 公开 GitHub + 公开 npm | ✅ |
| 私有 npm (Artifactory) | ❌（自建 Registry） |
| 公司内网 hosted MCP | ❌（自建 Registry） |
| 开源代码 + 商业服务 | ✅（packages 指向公开来源） |

公司内部可以自建 Registry——按官方 OpenAPI spec 实现，复用 Host 端支持。

---

## 8. 信任与安全

### 8.1 验证机制
- 命名空间认证（不可冒充）
- Underlying package registry 自带的安全扫描（npm advisories 等）
- Downstream 聚合器加 rating / 审核

### 8.2 不做的事
- **没有**官方代码扫描——交给 npm/PyPI 自己
- **没有**自动 sandbox / 沙箱评级
- **没有**用户评论（聚合器做）

### 8.3 Spam 防御
- 命名空间验证（最重要）
- 字段长度 + 正则限制
- 维护者手动 takedown（按 Moderation Policy）

---

## 9. 完整 demo：发布自己的 hello-mcp

### 9.1 准备 PyPI 包

```bash
# pyproject.toml
[project]
name = "yourname-03-mcp-hello"
version = "1.0.0"
dependencies = ["mcp>=1.10"]

[project.scripts]
hello-mcp = "hello_mcp:main"
```

```bash
python -m build
twine upload dist/*
```

### 9.2 创建 server.json

```json
{
  "name": "io.github.yourname/03-mcp-hello",
  "description": "本手册的 hello-mcp 演示 Server，三大原语示例",
  "version": "1.0.0",
  "repository": {
    "url": "https://github.com/yourname/03-mcp"
  },
  "packages": [
    {
      "registry_type": "pypi",
      "identifier": "yourname-03-mcp-hello",
      "version": "1.0.0",
      "runtime_arguments": [],
      "environment_variables": []
    }
  ]
}
```

### 9.3 发布

```bash
mcp-publisher publish
# 第一次会引导 GitHub OAuth 验证
```

发布后用户能在 Registry 搜到，并用 `uvx yourname-03-mcp-hello` 一行运行。

### 9.4 配置到 Claude Code

```json
{
  "mcpServers": {
    "hello": {
      "command": "uvx",
      "args": ["yourname-03-mcp-hello"]
    }
  }
}
```

---

## 10. Versioning

Registry 支持多版本共存：

- 发布新版本：递增 `version`
- 老版本不会被覆盖
- Host 默认装最新 stable

deprecation：

```json
{
  "version": "0.9.0",
  "deprecated": true,
  "deprecation_message": "请升级到 1.0.0"
}
```

---

## 11. 命名 vs 已有项目

如果你想发布的能力别人已经发了？

- Registry 用名字唯一性强制——`io.github.X/Y` 只有 X 能发
- 但你可以用自己 namespace 发同类工具：`io.github.you/another-github-server`
- 推荐：在 description / repo 里说清和已有的区别

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **命名空间验证失败** | GitHub OAuth 权限 / DNS TXT 没生效 |
| **package identifier 写错** | 必须和实际 npm / PyPI 上一致 |
| **runtime_arguments 不知道填什么** | 命令行参数（一般为空） |
| **发布后 Host 不显示** | 等下游聚合器（Cursor / Claude Code 商店）拉数据，有几小时延迟 |
| **私有 Server 误发** | 私网内的 server 不要发到官方 Registry，自建 |

---

## 13. 跨 Registry 互通

官方 Registry 提供 OpenAPI spec，其他 Registry（私有 / 行业垂直）也可以实现这套 API，让 Host 用同一份配置同时消费：

```
Host
  ├── 官方 Registry (公开 Server)
  ├── 公司 Registry (内部 Server)
  └── Cursor Marketplace (聚合 + 评分)
```

未来这种"Registry 联邦"会越来越普遍。

---

## 14. 下一步

06-advanced 全部 3 篇结束。下一章 07-practice：3 个实战项目。

## 参考资料

- Registry About：https://modelcontextprotocol.io/registry/about
- Quickstart 发布：https://modelcontextprotocol.io/registry/quickstart
- server.json schema：https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/draft/server.schema.json
- Package Types：https://modelcontextprotocol.io/registry/package-types
- GitHub Actions：https://modelcontextprotocol.io/registry/github-actions
- Registry 仓库：https://github.com/modelcontextprotocol/registry
