# EKB 39：ACL 模型（角色、文档可见性、用户身份）

> 本项目用 RBAC：用户有角色，文档标注可见角色，中间用 `acl(doc_id, role)` 这张多对多表连起来。这篇讲这套模型怎么建，`all` 这个特殊角色怎么处理，用户的角色从哪里来，以及管理文档时怎么维护 ACL。

---

## 1. 三个实体

```
用户(User) ── 有 ── 角色(Role)
                      │
文档(Document) ── 可见于 ── 角色(Role)
                      │
            用户角色 ∩ 文档可见角色 ≠ ∅  → 可见
```

用户的登录态里带着角色，可能不止一个，比如某人既是 `engineer` 又是 `manager`。文档在 acl 表里记录哪些角色可见。判断规则很简单：用户的角色集合和文档的可见角色集合有交集，就可见。

---

## 2. 回顾 acl 表

```sql
CREATE TABLE acl (
    doc_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,
    PRIMARY KEY (doc_id, role)
);
```

一篇文档有几个可见角色，就有几行。例如：

```
doc 7 (报销制度)    → (7, 'all')                    公开
doc 15 (绩效标准)   → (15, 'hr'), (15, 'manager')   HR 和经理可见
doc 30 (薪资细则)   → (30, 'hr'), (30, 'finance')   HR 和财务可见
```

---

## 3. `all` 这个特殊角色

大多数制度文档对全员公开，用特殊角色 `all` 表示。检索时，任何用户都能看到标了 `all` 的文档：

```sql
-- 用户角色是 engineer，能看到 engineer 的 + all 的
WHERE a.role IN ('engineer', 'all')
```

实现时，给用户的角色集合始终加上 `all`：

```python
def effective_roles(user_roles: list[str]) -> list[str]:
    return list(set(user_roles) | {"all"})   # 永远包含 all
```

这样公开文档就是可见角色里含 `all` 的文档，和受限文档走同一套过滤逻辑，不需要特殊处理。

---

## 4. 用户角色从哪里来

权限判断的起点是：提问的这个用户是什么角色。常见来源有三个：

| 来源 | 说明 |
|------|------|
| 企业 SSO / IdP | 登录后从身份系统拿角色，生产环境的标准做法 |
| 用户表 | 自己维护 user→roles 的映射 |
| HR 系统 | 把部门、职级映射成角色 |

本项目是教学项目，做了简化：API 请求带一个 `role` 参数，或者从一张简单的 user 表里查。角色必须由可信来源决定，绝不能让前端或用户自己声明，否则改个参数就越权了。生产环境里，角色应该来自服务端验证过的 session 或 token。

---

## 5. 管理文档时维护 ACL

ACL 会变，文档管理后台要能修改可见性：

```python
def set_doc_roles(conn, doc_id: int, roles: list[str]):
    with conn.transaction():
        conn.execute("DELETE FROM acl WHERE doc_id = %s", (doc_id,))
        for role in roles:
            conn.execute(
                "INSERT INTO acl (doc_id, role) VALUES (%s, %s)", (doc_id, role))
```

几个常见的管理场景：

- 调整可见性：把文档从公开改成仅 HR 可见，重置它的 acl 行。
- 新增角色：公司新设了法务角色，就给相关文档加上 `(doc_id, 'legal')`。
- 默认值：上传文档时没指定可见性，是默认 `all`，还是默认最严（仅上传者所在部门）？企业场景我建议默认最严，宁可有人暂时看不到，也别把敏感文档放出去（详见 [文档管理后台](../09-frontend/03-doc-admin.md)）。

---

## 6. 角色的粒度

角色不是越多越好：

```
太粗：只有 all → 没法表达受限文档
太细：每个员工一个角色 → 退化成 ACL 逐人授权，难维护
合适：按部门/职能分（engineer/hr/finance/product/manager/legal...）
```

起步用部门级角色，能覆盖大部分需求。等真的出现「同一部门内还要再细分」的需求，再考虑引入属性（ABAC）。一开始就设计几十个角色，维护成本高，大部分还用不上。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 角色由前端声明 | 改个参数就越权 | 角色来自服务端的可信来源 |
| 没有 all 角色 | 每篇公开文档都得列全所有角色 | 用 all 统一表示 |
| 上传时默认全公开 | 敏感文档被误公开 | 默认最严 |
| 角色粒度过细 | 退化成逐人授权 | 从部门级起步 |
| 改可见性时只加不删 | 旧权限残留 | 先删后插，整体重置 |

---

## 下一步

模型建好了，接下来是重点：检索时怎么用它过滤。

→ [检索阶段过滤](./03-filter-at-retrieval.md)
