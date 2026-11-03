# EKB 41：多角色测试

> 权限做得对不对，要拿同一个问题换不同角色去问，看结果是否符合预期。HR 问「绩效怎么评」应该命中绩效文档，普通工程师问同一个问题应该查不到、走兜底。这篇把这种角色对照写成评估用例，放进自动化测试。

---

## 1. 思路：同一问题，多个角色对照

只用一个角色测不出权限问题，必须拿同一个问题让多个角色来问：

```
问题：「绩效是怎么评定的？」（绩效标准文档仅 hr/manager 可见）

asker_role = hr        → 期望命中绩效文档 [15]，found=true
asker_role = manager   → 期望命中绩效文档 [15]，found=true
asker_role = engineer  → 期望命中不到，expected=[]，found=false（兜底）
```

第三条最要紧，它验证的是无权角色确实查不到。少了它，你只知道有权的人能看到，却不知道无权的人看不到，而安全靠的恰恰是后者。

---

## 2. 把角色对照写成评估用例

在测试集里，同一个问题按角色写成一组：

```jsonl
{"id":50,"question":"绩效是怎么评定的？","asker_role":"hr","expected_doc_ids":[15]}
{"id":51,"question":"绩效是怎么评定的？","asker_role":"manager","expected_doc_ids":[15]}
{"id":52,"question":"绩效是怎么评定的？","asker_role":"engineer","expected_doc_ids":[]}
{"id":53,"question":"薪资核算规则是什么？","asker_role":"finance","expected_doc_ids":[30]}
{"id":54,"question":"薪资核算规则是什么？","asker_role":"engineer","expected_doc_ids":[]}
```

`run_eval` 跑这些用例时调用 `retrieve(question, roles=[asker_role])`，把角色带进去，就能验证过滤有没有生效。期望为空的用例同时也在检验兜底：无权时应该 found=false。

---

## 3. 评估脚本里分清两种查不到

权限上线以后，`recall=0` 可能是两回事，评估时要分开统计：

```python
def score_permission(case, retrieved_docs, ans):
    expected = case["expected_doc_ids"]
    if not expected:
        # 期望查不到（无权或确实没有）
        # 正确 = 没召回到「本应无权」的文档 且 走了兜底
        leaked = any(is_restricted_for(case["asker_role"], d) for d in retrieved_docs)
        return {"leak": leaked, "fallback_ok": (not ans.found)}
    else:
        # 期望查得到
        return {"recall": recall_at_k(retrieved_docs, expected, 5)}
```

判定方式：

- `expected=[]`、`found=false`，而且没有越权召回：权限正确。
- `expected=[]`，却召回了受限文档：越权泄漏，触碰红线。

---

## 4. 越权召回是红线指标

第 19 篇定过规矩：越权召回必须为 0，只是比上次少几条也不算过关。所以要单独统计：

```python
def count_leaks(rows) -> int:
    """统计有多少次召回了对当前角色受限的文档"""
    leaks = 0
    for r in rows:
        for doc_id in r["retrieved"]:          # 第 20 篇 score_one 记下的召回 doc_id
            if is_restricted_for(r["asker_role"], doc_id):
                leaks += 1
    return leaks

# 评估报告里单列
leaks = count_leaks(rows)
assert leaks == 0, f"❌ 越权召回 {leaks} 次，存在权限漏洞！"
```

`is_restricted_for(role, doc_id)` 查这篇文档的可见角色，既不含 role 也不含 all，就算受限。任何一次越权召回都是严重 bug，CI 应该直接失败。

---

## 5. 别漏了边界用例

权限测试里容易漏掉的边界情况，都要造用例覆盖：

| 边界 | 用例 |
|------|------|
| 多角色用户 | 用户同时是 engineer 和 manager，能看两者的并集 |
| all 文档 | 任何角色都能看到公开文档 |
| 角色拼写/大小写 | `HR` 和 `hr` 是否按同一个角色处理 |
| 空角色/匿名 | 没有角色的用户只能看 all（或者全部拒绝） |
| 文档改了可见性后 | 改完重新检索，旧权限没有残留 |

其中多角色用户和改可见性这两类最容易出 bug：前者的并集逻辑容易写错，后者可能是缓存或索引没更新。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只测有权角色 | 不知道无权的人是否被挡住 | 必须有无权对照用例 |
| 把越权召回当普通指标 | 容忍了泄漏 | 当红线，必须为 0，CI 失败 |
| 不测多角色用户 | 并集逻辑的 bug 漏网 | 专门造用例 |
| 改了可见性不重测 | 旧权限残留导致泄漏 | 改完重跑权限测试 |
| 角色大小写、拼写不统一 | 角色匹配失败 | 统一规范角色字符串 |

---

## 下一步

有了权限测试，最后再用一个专门的越权扫描，给整个权限体系上锁：

→ [验证越权内容不会被召回](./05-no-leak-verify.md)
