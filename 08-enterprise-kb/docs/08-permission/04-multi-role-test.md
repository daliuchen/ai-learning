# EKB 41：多角色测试——同一问题，不同角色不同结果

> **一句话**：权限对不对，靠「同一个问题用不同角色问，看结果是否符合预期」来验证。HR 问「绩效怎么评」该命中绩效文档，普通工程师问同样的问题该命中不到、走兜底。本篇把这种「角色对照」做成评估用例，纳入自动化测试。

---

## 1. 权限测试的核心思路：角色对照

单角色测不出权限问题——必须**同问题、多角色对照**：

```
问题：「绩效是怎么评定的？」（绩效标准文档仅 hr/manager 可见）

asker_role = hr        → 期望命中绩效文档 [15]，found=true
asker_role = manager   → 期望命中绩效文档 [15]，found=true
asker_role = engineer  → 期望命中不到，expected=[]，found=false（兜底）
```

第三条是权限测试的**关键用例**：它验证「无权角色确实查不到」。少了它，你只知道「有权的能看到」，不知道「无权的看不到」——而后者才是安全的核心。

---

## 2. 把角色对照做成评估用例

在测试集里成对/成组地写同一问题的不同角色版本：

```jsonl
{"id":50,"question":"绩效是怎么评定的？","asker_role":"hr","expected_doc_ids":[15]}
{"id":51,"question":"绩效是怎么评定的？","asker_role":"manager","expected_doc_ids":[15]}
{"id":52,"question":"绩效是怎么评定的？","asker_role":"engineer","expected_doc_ids":[]}
{"id":53,"question":"薪资核算规则是什么？","asker_role":"finance","expected_doc_ids":[30]}
{"id":54,"question":"薪资核算规则是什么？","asker_role":"engineer","expected_doc_ids":[]}
```

`run_eval` 跑这些时，`retrieve(question, roles=[asker_role])` 带上角色，就能验证过滤生效。期望为空的用例同时检验**兜底**（无权 → 应该 found=false）。

---

## 3. 评估脚本里区分两种「查不到」

权限上线后，`recall=0` 可能是两回事，评估要分开看：

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

- `expected=[]` 且 `found=false` 且**无越权召回** → ✅ 权限正确
- `expected=[]` 但召回了受限文档 → ❌ **越权泄漏**（红线）

---

## 4. 越权召回 = 红线指标

第 19 篇定过：越权召回不是「越少越好」，而是**必须为 0**。专门统计它：

```python
def count_leaks(rows) -> int:
    """统计有多少次召回了对当前角色受限的文档"""
    leaks = 0
    for r in rows:
        for doc_id in r["retrieved_docs"]:
            if is_restricted_for(r["asker_role"], doc_id):
                leaks += 1
    return leaks

# 评估报告里单列
leaks = count_leaks(rows)
assert leaks == 0, f"❌ 越权召回 {leaks} 次，存在权限漏洞！"
```

`is_restricted_for(role, doc_id)`：查该 doc 的可见角色，如果不含 role（也不含 all），就是受限。**任何一次越权召回都是严重 bug，CI 应直接 fail。**

---

## 5. 边界用例别漏

权限测试容易漏的边界情况，都要造用例覆盖：

| 边界 | 用例 |
|------|------|
| 多角色用户 | 用户同时是 engineer + manager，能看两者的并集 |
| all 文档 | 任何角色都能看到公开文档 |
| 角色拼写/大小写 | `HR` vs `hr` 是否一致处理 |
| 空角色/匿名 | 没角色的用户只能看 all（或全拒） |
| 文档改了可见性后 | 改完重检索，旧权限不残留 |

尤其「多角色用户」和「改可见性后」最容易出 bug——前者并集逻辑易错，后者缓存/索引可能没更新。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只测有权角色 | 不知无权的是否被挡 | 必须有无权对照用例 |
| 越权召回当普通指标 | 容忍泄漏 | 红线，必须 0，CI fail |
| 不测多角色用户 | 并集逻辑 bug 漏网 | 专门造用例 |
| 改可见性不重测 | 旧权限残留泄漏 | 改后重跑权限测试 |
| 大小写/拼写不统一 | 角色匹配失败 | 规范化角色字符串 |

---

## 下一步

权限测试有了，最后用专门的「越权不召回」验证给整个权限体系上锁：

→ [05-no-leak-verify](./05-no-leak-verify.md)
