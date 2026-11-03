# EKB 42：验证越权内容不会被召回

> 权限的最终验收标准只有一条：任何角色都检索不到对它受限的文档，一次都不行。这篇把它做成一个专门的安全测试，穷举所有受限文档和所有无权角色的组合，作为上线前的硬门禁；另外也讲怎么防住召回没漏、答案或引用却漏了的次生泄漏。

---

## 1. 验收标准

```
对每一篇受限文档 D：
  对每一个「不该看到 D」的角色 R：
    用 R 的身份，尽力构造能命中 D 的问题去检索
    → D 必须不出现在召回结果里
若有任何一例 D 出现 → 权限失败，禁止上线
```

这比抽几个用例严格得多，它把受限文档和无权角色的组合全部跑一遍，不靠运气。

---

## 2. 自动化越权扫描

不必人工去想问题，直接拿文档自己的内容去「钓」。连原文都钓不出来，普通提问就更不会漏：

```python
# permission/leak_scan.py
def leak_scan(conn) -> list[dict]:
    leaks = []
    # 受限文档 = acl 里没有 'all' 这一行的文档（同时标了 all 的文档本来就公开）
    restricted = conn.execute(
        "SELECT DISTINCT doc_id FROM acl "
        "WHERE doc_id NOT IN (SELECT doc_id FROM acl WHERE role = 'all')").fetchall()
    all_roles = [r["role"] for r in conn.execute(
        "SELECT DISTINCT role FROM acl WHERE role != 'all'").fetchall()]

    for row in restricted:                              # 连接是 dict_row，按列名取
        doc_id = row["doc_id"]
        visible = get_doc_roles(conn, doc_id)          # 该文档可见角色
        # 用文档自己的一段内容当查询，最容易命中自己
        probe = get_doc_sample_text(conn, doc_id)
        for role in all_roles:
            if role in visible:
                continue                                # 有权，跳过
            # 两路都要扫：只扫向量路，BM25 路的漏洞查不出来
            hits = vector_search(probe, roles=[role], k=20) + bm25_search(probe, roles=[role], k=20)
            if any(h["doc_id"] == doc_id for h in hits):
                leaks.append({"doc": doc_id, "role": role})  # ❌ 越权命中
    return leaks

assert leak_scan(conn) == [], "存在越权泄漏，禁止上线！"
```

用文档原文当探针，是因为这是最容易命中这篇文档的查询。它都漏不出来，权限才算可靠。

---

## 3. 防次生泄漏：召回、答案、引用三处都查

召回过滤做对了，泄漏还可能从别的地方溜出去，也就是 [为什么权限是企业知识库的难点](./01-permission-is-devil.md) 里列的四个入口：

```python
def full_leak_check(role: str, doc_id_restricted: int, result: dict):
    # 1. 召回层：受限 doc 不在检索结果（leak_scan 已覆盖）
    # 2. 答案层：答案文本不应复述受限文档内容
    # 3. 引用层：引用列表不应出现受限文档
    for cite in result["citations"]:
        assert not is_restricted_for(role, cite["doc_id"]), "引用泄漏受限文档！"
```

最容易被忽略的是引用层。召回过滤即使做对了，引用卡片的数据如果从别处取、没有经过同样的过滤，受限文档的标题和链接照样会被展示出去。构造引用时也要走同一套权限过滤。

---

## 4. 接进 CI 当门禁

权限测试不能跑一次就算完，每次改动都要自动跑。重构检索、改缓存、调融合，都可能不小心把权限弄坏：

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

哪次改动让这个门禁变红，就说明引入了权限回归，必须在合并前修掉。权限很容易在无意中被改坏，只能靠持续检查来守住。

---

## 5. 权限验收清单

上线前逐条确认：

- [ ] `leak_scan` 全部通过（受限文档和无权角色的组合全跑一遍，零命中）
- [ ] 多角色对照用例全部通过（有权能看到，无权走兜底）
- [ ] 引用层没有泄漏受限文档
- [ ] 多角色用户取并集正确
- [ ] 改了文档可见性后重跑，没有残留
- [ ] BM25 路和向量路都验证过，不是只测了一路
- [ ] 角色来自服务端的可信来源，前端无法伪造
- [ ] 权限门禁已接入 CI

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只抽几个用例测权限 | 有些组合没覆盖到 | 穷举扫描 |
| 不用文档原文当探针 | 钓不出实际存在的漏洞 | 用原文做探针 |
| 只查召回，不查引用 | 引用层泄漏 | 召回、答案、引用三层都查 |
| 权限测一次就不管了 | 后续改动悄悄弄坏 | 在 CI 里持续当门禁 |
| 信任前端传来的角色 | 伪造角色越权 | 角色取自服务端可信来源 |

---

## 下一步

权限上了锁，知识库可以放心用了。接下来做一个让人用得上的界面：

→ [问答页](../09-frontend/01-qa-page-streaming.md)
