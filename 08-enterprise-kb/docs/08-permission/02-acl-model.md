# EKB 39：ACL 模型——角色、文档可见性、用户身份

> **一句话**：本项目用 RBAC——用户有角色，文档标可见角色，`acl(doc_id, role)` 多对多表把两者连起来。本篇讲清这套模型怎么建、`all` 这种特殊角色怎么处理、用户的角色从哪来，以及文档管理时怎么维护 ACL。

---

## 1. 三个实体

```
用户(User) ── 有 ── 角色(Role)
                      │
文档(Document) ── 可见于 ── 角色(Role)
                      │
            用户角色 ∩ 文档可见角色 ≠ ∅  → 可见
```

- **用户**：登录态带角色（可能多个，如某人既是 `engineer` 又是 `manager`）
- **文档**：标注「哪些角色可见」（存 acl 表）
- **可见规则**：用户的角色集合和文档的可见角色集合**有交集**就可见

---

## 2. acl 表回顾

```sql
CREATE TABLE acl (
    doc_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,
    PRIMARY KEY (doc_id, role)
);
```

一篇文档对多个可见角色 = 多行。例：

```
doc 7 (报销制度)    → (7, 'all')                    公开
doc 15 (绩效标准)   → (15, 'hr'), (15, 'manager')   HR 和经理可见
doc 30 (薪资细则)   → (30, 'hr'), (30, 'finance')   HR 和财务可见
```

---

## 3. `all` 这个特殊角色

大多数制度文档对全员公开，用一个特殊角色 `all` 表示。检索时，**任何用户都能看到 `all` 的文档**：

```sql
-- 用户角色是 engineer，能看到 engineer 的 + all 的
WHERE a.role IN ('engineer', 'all')
```

实现上，把用户的角色集合**总是加上 `all`**：

```python
def effective_roles(user_roles: list[str]) -> list[str]:
    return list(set(user_roles) | {"all"})   # 永远包含 all
```

这样「公开文档」就是「可见角色含 all 的文档」，统一进同一套过滤逻辑，不用特判。

---

## 4. 用户角色从哪来

权限的起点是「这个提问的用户是什么角色」。来源通常是：

| 来源 | 说明 |
|------|------|
| 企业 SSO / IdP | 登录后从身份系统拿角色（生产标准做法） |
| 用户表 | 自己维护 user→roles 映射 |
| HR 系统 | 部门/职级映射到角色 |

本项目（教学）简化：API 请求带一个 `role` 参数，或从一个简单 user 表查。**关键是：角色由可信来源决定，绝不能由前端/用户自己声明**——否则用户改个参数就越权了。生产中角色应来自服务端验证过的 session/token。

---

## 5. 文档管理时维护 ACL

ACL 不是一次性的，文档管理后台要能改可见性：

```python
def set_doc_roles(conn, doc_id: int, roles: list[str]):
    with conn.transaction():
        conn.execute("DELETE FROM acl WHERE doc_id = %s", (doc_id,))
        for role in roles:
            conn.execute(
                "INSERT INTO acl (doc_id, role) VALUES (%s, %s)", (doc_id, role))
```

几个管理场景：
- **调整可见性**：把文档从「公开」改成「仅 HR」→ 重置 acl 行
- **新增角色**：公司新设「法务」角色 → 相关文档加 `(doc_id, 'legal')`
- **默认值**：上传文档时若没指定，默认 `all` 还是默认最严（仅上传者部门）？**企业场景建议默认最严**，宁可漏看不可错放（详见 [09-frontend/03-doc-admin](../09-frontend/03-doc-admin.md)）。

---

## 6. 角色设计的粒度

角色不是越多越好：

```
太粗：只有 all → 没法表达受限文档
太细：每个员工一个角色 → 退化成 ACL 逐人授权，难维护
合适：按部门/职能分（engineer/hr/finance/product/manager/legal...）
```

起步用**部门级角色**，覆盖 90% 需求。真出现「同部门内还要再分」的需求，再考虑加属性（ABAC）。别一开始就设计几十个角色——维护成本高，且大多用不上。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 角色由前端声明 | 改参数即越权 | 角色来自服务端可信来源 |
| 没有 all 角色，公开文档难表达 | 每个公开文档列所有角色 | 用 all 统一 |
| 上传默认全公开 | 敏感文档误公开 | 默认最严 |
| 角色粒度过细 | 退化成逐人授权 | 部门级起步 |
| 改可见性只加不删旧 acl | 旧权限残留 | 重置（先删后插） |

---

## 下一步

模型有了，核心来了——怎么在检索时用它过滤：

→ [03-filter-at-retrieval](./03-filter-at-retrieval.md)
