# EKB 45：文档管理后台——上传、设权限、触发重建

> **一句话**：知识库的内容要能管理——上传/更新文档、设置可见角色、下线、触发重新 ingest。本篇做一个最简的管理后台，重点讲两件容易做错的事：**可见性默认值要从严**，以及**上传后要触发增量 ingest**。

---

## 1. 后台的核心功能

```
文档列表：标题 · 空间 · 可见角色 · 更新时间 · 状态(active/archived)
操作：
  ├─ 上传新文档（选文件 + 设可见角色）
  ├─ 编辑可见角色
  ├─ 下线/上线（archived ↔ active）
  └─ 删除
```

不需要花哨，一个表格 + 几个操作就够。关键在操作背后的逻辑，不在 UI。

---

## 2. 上传：可见性默认从严

最重要的设计决策——**新文档默认对谁可见**：

```tsx
// ❌ 危险默认：上传即全员可见
const [roles, setRoles] = useState(['all'])

// ✅ 安全默认：上传者部门可见，需显式放开才公开
const [roles, setRoles] = useState([uploaderDept])
```

理由：**误把敏感文档设成公开，比误把公开文档设成受限严重得多**。前者是数据泄漏，后者只是「有人暂时看不到」。所以默认从严，公开是一个需要**主动确认**的动作：

```tsx
{roles.includes('all') && (
  <Warning>此文档将对全体员工可见，请确认不含敏感信息</Warning>
)}
```

---

## 3. 上传后触发增量 ingest

上传文档不是存个文件就完——要走完整的 ingest 管道（解析→分块→embed→入库），文档才可被检索：

```python
# api/main.py
@app.post("/api/docs")
async def upload_doc(file: UploadFile, roles: list[str], space: str):
    raw = (await file.read()).decode("utf-8")
    meta = {"title": ..., "space": space, "roles": roles, ...}
    # 复用 ingest 管道
    ingest_content(conn, meta, raw)        # parse → chunk → embed → load
    return {"status": "indexed"}
```

要点：**复用第 05 章的 ingest 代码**，不要为后台重写一套。上传走的是「单文档增量 ingest」（[26 篇](../05-ingest/06-incremental.md)），更新已有文档则先删旧 chunk 再重建。

> 大文档 embed 慢，可以异步：先返回「处理中」，后台 ingest 完再标记 `indexed`，避免上传请求超时。

---

## 4. 改可见性要联动权限测试

改文档可见角色，是**高危操作**——改错了直接导致泄漏或失效。改完应：

```python
@app.put("/api/docs/{doc_id}/roles")
async def update_roles(doc_id: int, roles: list[str]):
    set_doc_roles(conn, doc_id, roles)     # 重置 acl（先删后插）
    # 联动：触发该文档相关的权限自检
    leaks = leak_scan_for_doc(conn, doc_id)
    if leaks:
        log.warning(f"doc {doc_id} 改权限后存在泄漏风险: {leaks}")
    return {"roles": roles}
```

把 [42 篇](../08-permission/05-no-leak-verify.md) 的 `leak_scan` 缩小到单文档，改完立刻自检。可见性是会被人为改错的地方，加一道自动检查很值。

---

## 5. 下线 vs 删除

后台要区分这两个动作（[26 篇](../05-ingest/06-incremental.md) 的设计）：

| 动作 | 数据库 | 可恢复 | 适用 |
|------|--------|--------|------|
| 下线 | `status='archived'` | ✅ 改回 active | 临时不想被检索（如待修订） |
| 删除 | `DELETE`（级联删 chunk/acl） | ❌ | 文档作废、彻底移除 |

检索 SQL 里 `WHERE status='active'` 自动过滤下线文档。**下线是可逆的轻操作，删除是不可逆的重操作**——UI 上删除要二次确认。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 上传默认全员可见 | 敏感文档误泄漏 | 默认从严，公开需确认 |
| 上传只存文件不 ingest | 文档检索不到 | 触发 ingest 管道 |
| 后台重写一套 ingest | 逻辑不一致 | 复用 05 章管道 |
| 大文档同步 ingest | 上传超时 | 异步处理 |
| 改可见性不自检 | 改错导致泄漏 | 联动 leak_scan |
| 下线和删除不分 | 误删难恢复 | 下线用 status，删除二次确认 |

---

## 下一步

前端两块都做完，最后讲清 TS 壳和 Python 脑怎么拆、怎么协作：

→ [04-ts-python-split](./04-ts-python-split.md)
