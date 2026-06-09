# EKB 42：越权不召回——给权限体系上锁

> **一句话**：权限的最终验收只有一条——**任何角色都检索不到对它受限的文档，一次都不行**。本篇把这条做成一个专门的安全测试（穷举所有受限文档 × 所有无权角色），作为上线的硬门禁，并讲怎么防住「召回没漏但答案/引用漏了」的次生泄漏。

---

## 1. 终极验收标准

```
对每一篇受限文档 D：
  对每一个「不该看到 D」的角色 R：
    用 R 的身份，尽力构造能命中 D 的问题去检索
    → D 必须不出现在召回结果里
若有任何一例 D 出现 → 权限失败，禁止上线
```

这比抽几个用例更严——它**穷举**受限文档和无权角色的组合，不留侥幸。

---

## 2. 自动化越权扫描

不用人工想问题，直接用文档自己的内容去「钓」——如果用文档原文都钓不出来，普通提问更不可能漏：

```python
# permission/leak_scan.py
def leak_scan(conn) -> list[dict]:
    leaks = []
    restricted = conn.execute(
        "SELECT DISTINCT doc_id FROM acl WHERE role != 'all'").fetchall()
    all_roles = conn.execute("SELECT DISTINCT role FROM acl").fetchall()

    for (doc_id,) in restricted:
        visible = get_doc_roles(conn, doc_id)          # 该文档可见角色
        # 用文档自己的一段内容当查询，最容易命中自己
        probe = get_doc_sample_text(conn, doc_id)
        for (role,) in all_roles:
            if role in visible or role == "all":
                continue                                # 有权，跳过
            hits = vector_search(probe, roles=[role], k=20)
            if any(h["doc_id"] == doc_id for h in hits):
                leaks.append({"doc": doc_id, "role": role})  # ❌ 越权命中
    return leaks

assert leak_scan(conn) == [], "存在越权泄漏，禁止上线！"
```

**用文档原文当探针**是关键——这是最容易命中该文档的查询，它都漏不出来，权限才算真严。

---

## 3. 防次生泄漏：召回、答案、引用三处都要查

召回过滤对了，泄漏还可能从别处溜出来（[38 篇](./01-permission-is-devil.md) 的四个入口）：

```python
def full_leak_check(role: str, doc_id_restricted: int, result: dict):
    # 1. 召回层：受限 doc 不在检索结果（leak_scan 已覆盖）
    # 2. 答案层：答案文本不应复述受限文档内容
    # 3. 引用层：引用列表不应出现受限文档
    for cite in result["citations"]:
        assert not is_restricted_for(role, cite["doc_id"]), "引用泄漏受限文档！"
```

最容易被忽略的是**引用层**：即使召回过滤对了，如果引用卡片的数据来源没同样过滤，可能把受限文档的标题/链接展示出去。**引用构造也要用同一套权限过滤。**

---

## 4. 把它接进 CI 当门禁

权限测试不是跑一次就完，要**每次改动都自动跑**——因为重构检索、改缓存、调融合，都可能不小心破坏权限：

```python
# 上线门禁（接 CI）
def permission_gate(conn):
    leaks = leak_scan(conn)
    assert leaks == [], f"越权泄漏: {leaks}"
    # 加上多角色对照用例的越权计数
    rows = run_permission_eval()
    assert count_leaks(rows) == 0
    print("✅ 权限门禁通过")
```

任何改动让这个门禁红，就是引入了权限回归，必须在合并前修掉。**权限是会被无意中改坏的，所以要持续守。**

---

## 5. 权限验收 checklist

上线前逐条确认：

- [ ] `leak_scan` 全绿（穷举受限文档 × 无权角色，零命中）
- [ ] 多角色对照用例全过（有权能看、无权兜底）
- [ ] 引用层无受限文档泄漏
- [ ] 多角色用户取并集正确
- [ ] 改文档可见性后重跑，无残留
- [ ] BM25 路和向量路都验证过（不只测了一路）
- [ ] 角色来自服务端可信来源，前端无法伪造
- [ ] 权限门禁已接入 CI

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只抽几个用例测权限 | 组合漏覆盖 | 穷举扫描 |
| 不用文档原文当探针 | 钓不出真漏洞 | 原文做最强探针 |
| 只查召回，不查引用 | 引用层泄漏 | 三层都查 |
| 权限测一次就不管 | 后续改动悄悄破坏 | CI 持续门禁 |
| 信任前端传的角色 | 伪造越权 | 服务端可信来源 |

---

## 下一步

权限上锁完成，知识库已经安全可信。接下来做出能让人用的界面：

→ [09-frontend/01-qa-page-streaming](../09-frontend/01-qa-page-streaming.md)
