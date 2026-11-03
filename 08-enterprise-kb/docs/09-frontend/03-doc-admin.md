# EKB 45：文档管理后台（上传、设权限、触发重建）

> 知识库的内容需要有人管：上传和更新文档、设置可见角色、下线文档、触发重新 ingest。这篇做一个最简单的管理后台，重点讲两件容易做错的事：可见性的默认值要从严，上传之后要触发增量 ingest。

---

## 1. 后台要有的功能

```
文档列表：标题 · 空间 · 可见角色 · 更新时间 · 状态(active/archived)
操作：
  ├─ 上传新文档（选文件 + 设可见角色）
  ├─ 编辑可见角色
  ├─ 下线/上线（archived ↔ active）
  └─ 删除
```

不用做得花哨，一个表格加几个操作就够了。要花心思的是操作背后的逻辑，不是 UI。

---

## 2. 上传：可见性默认从严

这里最重要的设计决定是：新文档默认对谁可见。

```tsx
// ❌ 危险默认：上传即全员可见
const [roles, setRoles] = useState(['all'])

// ✅ 安全默认：上传者部门可见，需显式放开才公开
const [roles, setRoles] = useState([uploaderDept])
```

把敏感文档误设成公开，比把公开文档误设成受限严重得多。前者是数据泄漏，后者只是有人暂时看不到。所以默认从严，公开必须由上传者主动确认：

```tsx
{roles.includes('all') && (
  <Warning>此文档将对全体员工可见，请确认不含敏感信息</Warning>
)}
```

---

## 3. 上传后触发增量 ingest

上传文档不能只存个文件，要走完整条 ingest 管道（解析、分块、embed、入库），文档才能被检索到：

```python
# api/main.py
@app.post("/api/docs")
async def upload_doc(file: UploadFile, roles: list[str] = Form(...), space: str = Form(...)):
    # 文件和表单字段一起提交，需要 python-multipart；roles/space 要声明成 Form，否则 FastAPI 会去 query 里找
    raw = (await file.read()).decode("utf-8")
    meta = {"title": ..., "space": space, "roles": roles, ...}
    # 复用 ingest 管道
    ingest_content(conn, meta, raw)        # parse → chunk → embed → load
    return {"status": "indexed"}
```

这里直接复用第 05 章的 ingest 代码，别为后台另写一套。上传走的是单文档增量 ingest（见 [增量更新](../05-ingest/06-incremental.md)），更新已有文档时先删掉旧 chunk 再重建。

> 大文档 embed 比较慢，可以做成异步：先返回「处理中」，后台 ingest 完成后再标记为 `indexed`，免得上传请求超时。

---

## 4. 改可见性后跑权限自检

修改文档的可见角色是高危操作，改错了要么泄漏，要么该看的人看不到。改完之后应该立刻自检：

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

做法是把 [验证越权内容不会被召回](../08-permission/05-no-leak-verify.md) 里的 `leak_scan` 缩小到单篇文档。可见性是人工操作、容易改错的地方，加一道自动检查很划算。

---

## 5. 下线和删除

后台要把这两个动作分开（设计见 [增量更新](../05-ingest/06-incremental.md)）：

| 动作 | 数据库 | 可恢复 | 适用 |
|------|--------|--------|------|
| 下线 | `status='archived'` | 可以，改回 active 即可 | 暂时不想被检索，比如等待修订 |
| 删除 | `DELETE`（级联删 chunk/acl） | 不可以 | 文档作废，彻底移除 |

检索 SQL 里的 `WHERE status='active'` 会自动过滤掉下线的文档。下线是可逆的轻操作，删除不可逆，所以 UI 上删除要二次确认。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 上传默认全员可见 | 敏感文档被误泄漏 | 默认从严，公开要确认 |
| 上传只存文件不 ingest | 文档检索不到 | 触发 ingest 管道 |
| 后台另写一套 ingest | 两边逻辑不一致 | 复用第 05 章的管道 |
| 大文档同步 ingest | 上传超时 | 异步处理 |
| 改了可见性不自检 | 改错导致泄漏 | 联动 leak_scan |
| 下线和删除不分 | 误删后难恢复 | 下线改 status，删除二次确认 |

---

## 下一步

前端两块都做完了，最后讲 TS 壳和 Python 脑怎么拆分、怎么协作：

→ [TypeScript 前端与 Python 后端的拆分](./04-ts-python-split.md)
