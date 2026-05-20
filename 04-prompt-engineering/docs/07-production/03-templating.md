# PE Production 03：Prompt Templating —— Jinja 与框架内置

> **一句话**：当 prompt 含多个动态变量、需要条件分支、多语言切换时，用 templating 引擎（Jinja2 / 框架内置）比字符串拼接干净。但**别过度模板化**——简单场景 f-string 就够。

---

## 1. 什么时候要 templating

| 触发 | 用 templating |
|------|--------------|
| Prompt 含 5+ 变量 | ✅ |
| 有条件分支（含 / 不含某段） | ✅ |
| 多语言版本（中 / 英 prompt 切换） | ✅ |
| 复杂 few-shot 拼接 | ✅ |
| 简单 1-2 变量 | ❌ 用 f-string |

---

## 2. Jinja2 基础

```python
from jinja2 import Template

t = Template("""你是 {{role}}。

任务：{{task}}

{% if examples %}
示例：
{% for ex in examples %}
- 输入: {{ex.input}}
  输出: {{ex.output}}
{% endfor %}
{% endif %}

约束：
{% for c in constraints %}
- {{c}}
{% endfor %}

{% if language == 'zh' %}
回答用中文。
{% else %}
Answer in English.
{% endif %}
""")

rendered = t.render(
    role="客服分类器",
    task="把反馈分类",
    examples=[{"input": "App 闪退", "output": "bug"}],
    constraints=["返回 JSON", "用 enum"],
    language="zh",
)
```

---

## 3. 实战模板组织

```
prompts/
├── classifier/
│   ├── v1.0.0/
│   │   ├── system.j2              # Jinja 模板
│   │   ├── variables.example.yml  # 变量示例
│   │   └── meta.yml
```

`system.j2`：

```jinja
你是 {{role}}。

任务：{{task}}

{% for cat in categories %}
- {{cat.name}}: {{cat.description}}
{% endfor %}

{% if examples %}
示例：
{% for ex in examples %}
输入: {{ex.input}}
输出: {{ex.output}}
{% endfor %}
{% endif %}

{% if business_context %}
业务背景:
{{business_context}}
{% endif %}
```

`variables.example.yml`：

```yaml
role: "客服反馈分类师"
task: "把用户反馈归到 8 个类别之一"
categories:
  - {name: bug, description: "软件错误"}
  - {name: feature, description: "功能请求"}
  - ...
examples:
  - {input: "App 闪退", output: "bug"}
business_context: |
  我们是 SaaS 公司...
```

---

## 4. 框架内置模板

### LangChain ChatPromptTemplate

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}。任务：{task}"),
    ("user", "{user_input}"),
])

resp = (prompt | model).invoke({
    "role": "分类师",
    "task": "...",
    "user_input": "App 闪退",
})
```

### LlamaIndex PromptTemplate

```python
from llama_index.core import PromptTemplate
prompt = PromptTemplate("你是 {role}...")
```

各家框架都有自己的 templating，本质上是 f-string 或 mustache 风格。

---

## 5. 模板的常见坑

### 5.1 Jinja 转义 problem
prompt 里的 `{` / `}` 会被 Jinja 当变量符号：

```jinja
返回 JSON: {"key": "value"}     ← Jinja 解析 {"key" 报错
```

对策：
```jinja
返回 JSON: {{ "{" }}"key": "value"{{ "}" }}
或
{% raw %}
返回 JSON: {"key": "value"}
{% endraw %}
```

### 5.2 不小心改了变量名
```jinja
{{user_input}}  ← rename 时漏改
```

对策：模板用单元测试覆盖关键 case：

```python
def test_render():
    rendered = template.render(role="x", task="y")
    assert "你是 x" in rendered
    assert "任务：y" in rendered
```

### 5.3 模板嵌套过深
```jinja
{% if a %}{% if b %}{% if c %}...{% endif %}{% endif %}{% endif %}
```

5 层嵌套 → 重构成 Python 预处理：

```python
def build_prompt(vars):
    pre = preprocess_vars(vars)  # Python 处理复杂逻辑
    return simple_template.render(**pre)
```

---

## 6. 多语言 prompt

```jinja
{% if lang == "zh" %}
你是中文助手。{{zh_task}}
{% elif lang == "en" %}
You are an English assistant. {{en_task}}
{% endif %}
```

或者拆成两个文件：

```
prompts/classifier/
├── system.zh.j2
└── system.en.j2
```

并加路由：

```python
def load_for_lang(name, lang):
    return Template(read(f"prompts/{name}/system.{lang}.j2"))
```

---

## 7. 模板 + Prompt caching 组合

模板生成 prompt 时**保证 system 部分稳定**：

```python
def render(name, version, user_input):
    template = load_template(name, version)
    rendered = template.render(
        role=ROLE_CACHE,        # 静态
        examples=EXAMPLES_CACHE, # 静态
        # 不要把 user_input 渲染进 system
    )
    return rendered

system = render("classifier", "v1.0.0", user_input)
# 再调 API 时 user_input 走 user message
```

让 caching 命中率高。

---

## 8. 模板 + 版本绑定

```yaml
# prompts/classifier/v1.0.0/meta.yml
version: v1.0.0
template_file: system.j2
schema_version: v2     # 用什么 variables schema
evalset: v3.1
```

template version 和 variables schema version 解耦——template 可以 v1.0.1（小改），但 variables 仍是 schema v2。

---

## 9. 完整 demo

```python
# demos/production/03_templating.py
"""Jinja 模板 + 多语言 + 缓存友好"""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_system(name: str, version: str, vars: dict) -> str:
    template = env.get_template(f"{name}/{version}/system.j2")
    return template.render(**vars)


VARS = {
    "role": "客服反馈分类师",
    "categories": [
        {"name": "bug", "desc": "软件错误"},
        {"name": "feature", "desc": "功能请求"},
        {"name": "other", "desc": "其他"},
    ],
    "examples": [
        {"input": "App 闪退", "output": "bug"},
    ],
}


if __name__ == "__main__":
    system = render_system("classifier", "v1.0.0", VARS)
    print(system)
```

对应 `templates/classifier/v1.0.0/system.j2`：

```jinja
你是 {{ role }}。

任务：把反馈分类。

类别：
{% for c in categories %}
- {{ c.name }}: {{ c.desc }}
{% endfor %}

{% if examples %}
示例：
{% for ex in examples %}
输入: {{ ex.input }} → 输出: {{ ex.output }}
{% endfor %}
{% endif %}

输出 JSON: {"category": "<enum>"}
```

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **简单 prompt 也上 Jinja** | f-string 就够；按需 |
| **{ } 没 escape** | Jinja 报错；用 `{% raw %}` |
| **模板和 variables 不同 version** | 加 meta.yml 标注 |
| **dynamic content 混进 system** | caching miss |
| **多语言用 if 嵌套** | 拆文件更干净 |

---

## 11. 下一步

- 📖 A/B 与可观测 → [04-ab-observability.md](./04-ab-observability.md)
- 📖 团队协作 → [05-team-collab.md](./05-team-collab.md)

## 参考资料

- Jinja2 文档: https://jinja.palletsprojects.com
- LangChain ChatPromptTemplate: https://python.langchain.com/docs/concepts/prompt_templates/
