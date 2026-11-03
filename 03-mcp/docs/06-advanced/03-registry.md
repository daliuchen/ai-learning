# MCP Advanced 03：把 Server 发布到 MCP Registry

> MCP Registry（registry.modelcontextprotocol.io）是官方维护的中心化元数据仓库。把 Server 登记上去之后，你发布在 npm、PyPI、Docker Hub 上的包就能被各家 Host 发现并统一安装。这篇讲 Registry 怎么运作、怎么发布，以及命名规则。Registry 目前还处于 preview 阶段，可能出现不兼容的变更或数据重置。用于生产之前，请留意官方的 release notes。

---

## 1. Registry 解决什么问题

在有 Registry 之前，写完 Server 发到 npm 或 PyPI，用户只能翻文档自己找；各个 Host 各自维护一份「内置 Server 推荐列表」；同名的 Server 良莠不齐，来源也无从核实。

Registry 相当于 MCP 生态的包索引。它定义了标准化的元数据格式（`server.json`），用 DNS 或 GitHub 验证命名空间，提供集中的 REST API 让 Host 和聚合平台统一拉取数据。它本身不存储包的代码，只指向 npm、PyPI、Docker Hub。

---

## 2. 命名空间约定

Server 名使用反向 DNS 格式：

| 来源 | 例子 |
|------|------|
| GitHub 用户/组织 | `io.github.username/server-name` |
| 自己的域名 | `com.example/server-name` |

发布之前要先验证命名空间（通过 DNS TXT 记录或 GitHub OAuth），保证别人没法冒充你。

---

## 3. server.json 格式

每个发布到 Registry 的 Server 都要提供一个 `server.json`（可以用 `mcp-publisher init` 生成模板）。注意字段用的是 camelCase，并且要带 `$schema`。网上一些早期教程写的 `registry_type`、`environment_variables` 是早期的 snake_case 写法，已经过时了：

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.yourname/hello-mcp",
  "title": "Hello MCP",
  "description": "本手册的 hello-mcp 演示 Server，三大原语示例",
  "version": "1.0.0",
  "repository": {
    "url": "https://github.com/yourname/hello-mcp",
    "source": "github"
  },
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "yourname-hello-mcp",
      "version": "1.0.0",
      "transport": { "type": "stdio" },
      "environmentVariables": [
        { "name": "API_KEY", "description": "上游服务的 API Key", "isRequired": true, "isSecret": true, "format": "string" }
      ]
    }
  ]
}
```

远程（hosted）Server 用 `remotes`。地址必须能从公网访问，传输应当用 Streamable HTTP，`"sse"` 只是为了兼容老客户端：

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "com.example/acme-analytics",
  "description": "Real-time business intelligence",
  "version": "2.0.0",
  "remotes": [{ "type": "streamable-http", "url": "https://analytics.example.com/mcp" }]
}
```

字段说明：

| 字段 | 含义 |
|------|------|
| `$schema` | server.json 的 JSON Schema（带日期的版本，当前是 `2025-12-11`） |
| `name` | 反向 DNS 格式的唯一名称（用 GitHub 认证时，必须以 `io.github.<你的用户名>/` 开头） |
| `title` / `description` | 显示名称 / 简短描述 |
| `version` | 每次发布都必须唯一，发布后该版本及其元数据不能修改。推荐用 SemVer（Registry 会尝试按 SemVer 解析来排序，但任意字符串都能接受） |
| `repository` | 源码仓库（`url` + `source`） |
| `packages` | 包在各个包注册表里的位置：`registryType`、`identifier`、`version`、`transport`、`environmentVariables`、`runtimeArguments` 等 |
| `remotes` | 远程 MCP 端点：`type`（`streamable-http` / `sse`）+ `url` |

---

## 4. 支持的包类型与所有权验证

Registry 只存元数据，不存代码。每种包类型都有自己的所有权验证方式，用来确认你发布的 server.json 确实对应这个包：

| `registryType` | 支持的来源 | 所有权验证 |
|-------------|-----------|-----------|
| `npm` | 仅 npm 公共仓库 | 在 `package.json` 里加 `"mcpName": "io.github.username/server"`，必须和 server.json 的 `name` 一致 |
| `pypi` | 仅 pypi.org | 包的 README（也就是 PyPI 上的包描述）里包含 `mcp-name: <server 名>` 这个字符串，通常写成 HTML 注释 `<!-- mcp-name: io.github.username/server -->` |
| `nuget` | 仅官方 NuGet | 包里包含对应的 `mcp-name` 声明 |
| `cargo` | crates.io | 同上 |
| `oci`（Docker/OCI 镜像） | 镜像仓库 | 镜像标签里带有 `io.modelcontextprotocol.server.name` 之类的标注 |
| `mcpb` | MCPB 包（打包好运行时的本地 Server） | 见官方 Package Types 页 |

（各类型具体的验证字段以官方 Package Types 页为准。）

---

## 5. 发布流程

### 5.1 用 `mcp-publisher` 命令行工具

`mcp-publisher` 是官方提供的预编译二进制程序（用 Go 写的），不是 npm 包。`npm install -g @modelcontextprotocol/publisher` 装不到它：

```bash
brew install mcp-publisher            # macOS；或下载 GitHub Releases 里的预编译包（Linux/macOS/Windows）
mcp-publisher --help                  # init / login / logout / publish
```

完整流程（以 PyPI 包加 GitHub 认证为例）：

```bash
# 1. 在 README 里加所有权验证标记，然后发布 PyPI 包
#    <!-- mcp-name: io.github.yourname/hello-mcp -->
python -m build && twine upload dist/*

# 2. 生成并编辑 server.json（name 必须与 README 里的 mcp-name 一致）
mcp-publisher init

# 3. 认证（GitHub 设备码流程：打开链接、输入终端里显示的码、授权）
mcp-publisher login github

# 4. 发布
mcp-publisher publish
```

认证方式有好几种（GitHub OAuth、GitHub OIDC、DNS TXT、HTTP 等，见官方 Authentication 页）。用 `com.example/...` 这类自有域名的命名空间发布时，用 DNS 或 HTTP 验证。

### 5.2 用 GitHub Actions 自动发布（推荐 OIDC）

```yaml
# .github/workflows/publish-mcp.yml
name: Publish to MCP Registry
on:
  push:
    tags: ["v*"]                 # 打 v1.0.0 之类的 tag 触发
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write            # OIDC 认证必需
      contents: read
    steps:
      - uses: actions/checkout@v5
      # ……先构建并发布底层包（npm publish / twine upload，各类型步骤不同）……
      - name: Install mcp-publisher
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
      - name: Authenticate to MCP Registry
        run: ./mcp-publisher login github-oidc
      - name: Publish server to MCP Registry
        run: ./mcp-publisher publish
```

（网上有些教程用的 `modelcontextprotocol/publish-action@v1` 并不是官方推荐的写法。官方的做法就是像上面这样，直接安装 `mcp-publisher`，再执行 `login github-oidc` 和 `publish`。也可以在发布前用 `jq` 把 tag 里的版本号写进 `server.json`。）

---

## 6. Host 和聚合平台怎么使用 Registry

下游的聚合平台（marketplace、IDE 内置商店）定期（比如每小时一次）从 Registry API 拉取数据：

```http
GET https://registry.modelcontextprotocol.io/v0/servers?limit=100&cursor=...
```

接口返回所有 Server 的元数据。评分、评论、分类、安全审核、打标签、推荐算法这些事，由聚合平台自己做。官方 Registry 不做这些，只管元数据。

---

## 7. 私有与公开 Server

| 类型 | 能发布到官方 Registry 吗 |
|------|----------------------|
| 公开的 GitHub + 公开的 npm | 可以 |
| 私有 npm (Artifactory) | 不行（自建 Registry） |
| 公司内网托管的 MCP | 不行（自建 Registry） |
| 开源代码 + 商业服务 | 可以（packages 指向公开的来源） |

公司内部可以自建 Registry：按官方的 OpenAPI spec 实现，就能复用 Host 端已有的支持。

---

## 8. 信任与安全

Registry 的验证机制有三层：命名空间认证（防止冒充）；底层包注册表自带的安全扫描（比如 npm advisories）；下游聚合平台的评分和审核。

它不做的事情包括：没有官方的代码扫描，这交给 npm、PyPI 自己；没有自动沙盒或沙盒评级；没有用户评论，这由聚合平台来做。

防垃圾信息方面，最重要的是命名空间验证，此外还有字段长度和正则限制，以及维护者按 Moderation Policy 手动下架。

---

## 9. 完整 demo：发布自己的 hello-mcp

### 9.1 准备 PyPI 包

```toml
# pyproject.toml
[project]
name = "yourname-hello-mcp"
version = "1.0.0"
readme = "README.md"                  # README 会成为 PyPI 的包描述，所有权验证看的就是它
dependencies = ["mcp>=2.3"]

[project.scripts]
hello-mcp = "hello_mcp:main"          # main() 里调 mcp.run()（stdio）
```

在 README.md 里加一行验证标记：

```markdown
<!-- mcp-name: io.github.yourname/hello-mcp -->
```

```bash
python -m build
twine upload dist/*
```

### 9.2 创建 server.json

使用上面第 3 节的 PyPI 示例（`registryType: pypi`、`identifier: yourname-hello-mcp`、`transport.type: stdio`）。

### 9.3 发布

```bash
mcp-publisher login github      # 第一次：设备码流程
mcp-publisher publish
```

发布之后，用户就能在 Registry 里搜到它，并用 `uvx yourname-hello-mcp` 一行命令运行。（这一节只是流程说明，包名是示例。）

### 9.4 配置到 Claude Code

```bash
claude mcp add hello -- uvx yourname-hello-mcp
```

或者写进 `.mcp.json`：

```json
{
  "mcpServers": {
    "hello": { "command": "uvx", "args": ["yourname-hello-mcp"] }
  }
}
```

---

## 10. 版本管理

每次发布的 `version` 都必须唯一，发布后该版本及其元数据都不能修改。之前的版本不会被覆盖，多个版本同时存在，Host 默认选择最新版。

想弃用某个已发布的版本，可以用 Registry 的状态管理功能，或者发布新版本并加以说明，具体机制见官方 Versioning 页。`server.json` 里没有专门的 `deprecated` 字段，这样写不符合 schema。

---

## 11. 如果别人已经发布了类似的 Server

Registry 靠名称唯一性来约束：`io.github.X/Y` 只有 X 能发布。不过你可以在自己的命名空间下发布同类工具，比如 `io.github.you/another-github-server`，最好在 description 或仓库里说清楚和已有 Server 的区别。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 命名空间验证失败 | 检查 GitHub OAuth 权限，或者 DNS TXT 记录是否已经生效 |
| package identifier 写错 | 必须和 npm / PyPI 上实际的包名一致 |
| 字段还在用 snake_case（`registry_type`） | 现在是 camelCase：`registryType`、`environmentVariables`、`runtimeArguments`，并且要加 `$schema` |
| PyPI 所有权验证失败 | README 里要有 `mcp-name: <name>`，而且要出现在 PyPI 的包描述里；`name` 要和 server.json 一致 |
| 想用 npm 安装 mcp-publisher | 它是预编译的二进制程序（通过 brew 或 GitHub Releases 获取），不是 npm 包 |
| 发布后 Host 里看不到 | 要等下游聚合平台（各家 Host 的商店）按自己的周期同步数据，会有延迟 |
| 私有 Server 被误发布 | 私有网络里的 Server 不要发布到官方 Registry，自建一个 |

---

## 13. 跨 Registry 互通

官方 Registry 提供了 OpenAPI spec，其他 Registry（私有的或行业垂直的）也可以实现同一套 API，这样 Host 用一份配置就能同时使用多个 Registry：

```
Host
  ├── 官方 Registry (公开 Server)
  ├── 公司 Registry (内部 Server)
  └── Cursor Marketplace (聚合 + 评分)
```

---

## 14. 下一步

进阶部分的 3 篇到这里结束。下一章是 3 个实战项目，从[内部知识库 MCP Server](../07-practice/01-project-internal-kb.md)开始。

## 参考资料

- Registry About：https://modelcontextprotocol.io/registry/about
- Quickstart 发布：https://modelcontextprotocol.io/registry/quickstart
- server.json schema：https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
- Package Types：https://modelcontextprotocol.io/registry/package-types
- 远程 Server：https://modelcontextprotocol.io/registry/remote-servers
- Versioning：https://modelcontextprotocol.io/registry/versioning
- GitHub Actions：https://modelcontextprotocol.io/registry/github-actions
- Registry 仓库：https://github.com/modelcontextprotocol/registry
