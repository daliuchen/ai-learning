# PE Production 03：Prompt Templating（Jinja 与框架内置模板）

> prompt 里的动态变量一多，再加上条件分支、多语言切换，字符串拼接很快就会变得一团乱，这时用 Jinja2 或框架自带的模板引擎会干净得多。反过来，简单场景别为了模板化而模板化，f-string 就够了。

---

## 1. 什么时候该用模板

| 情况 | 用模板 |
|------|--------------|
| prompt 有 5 个以上变量 | 是 |
| 有条件分支（某段时有时无） | 是 |
| 多语言版本（中 / 英 prompt 切换） | 是 |
| few-shot 拼接逻辑复杂 | 是 |
| 只有 1-2 个变量 | 否，用 f-string |

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

## 3. 模板文件怎么组织

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

## 4. 框架自带的模板

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

各个框架都有自己的模板实现，基本都是 f-string 或 mustache 风格。

---

## 5. 写模板时容易踩的坑

### 5.1 花括号转义
Jinja 只把 `{{`、`{%`、`{#` 当定界符，单个 `{"key": "value"}` 原样输出，没问题。麻烦出在 JSON 嵌套时连着写出两个左花括号，比如 `{{"a": 1}}` 这种示例，会被当成变量表达式解析报错：

```jinja
返回 JSON 数组: [{"items": [{{"id": 1}}]}]     ← {{ 被当成变量开始，报错
```

解决办法：
```jinja
返回 JSON: {{ "{" }}"key": "value"{{ "}" }}
或
{% raw %}
返回 JSON: {"key": "value"}
{% endraw %}
```

### 5.2 改名时漏改变量
```jinja
{{user_input}}  ← rename 时漏改
```

给模板写单元测试，覆盖关键场景：

```python
def test_render():
    rendered = template.render(role="x", task="y")
    assert "你是 x" in rendered
    assert "任务：y" in rendered
```

### 5.3 嵌套太深
```jinja
{% if a %}{% if b %}{% if c %}...{% endif %}{% endif %}{% endif %}
```

嵌套到五层就该重构了，把复杂逻辑挪到 Python 里预处理：

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

也可以拆成两个文件：

```
prompts/classifier/
├── system.zh.j2
└── system.en.j2
```

再按语言路由：

```python
def load_for_lang(name, lang):
    return Template(read(f"prompts/{name}/system.{lang}.j2"))
```

---

## 7. 模板和 prompt caching 一起用

用模板生成 prompt 时，要保证渲染出来的 system 部分是稳定的：

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

这样缓存命中率才高得起来。

---

## 8. 模板和版本绑定

```yaml
# prompts/classifier/v1.0.0/meta.yml
version: v1.0.0
template_file: system.j2
schema_version: v2     # 用什么 variables schema
evalset: v3.1
```

模板版本和变量 schema 版本是分开的。模板可以小改升到 v1.0.1，变量仍然用 schema v2。

---

## 9. 完整 demo

```python
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

对应的 `templates/classifier/v1.0.0/system.j2`：

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
| 简单 prompt 也上 Jinja | f-string 就够，按需引入 |
| 花括号没转义 | Jinja 报错；用 `{% raw %}` |
| 模板和变量版本对不上 | 在 meta.yml 里标清楚 |
| 动态内容混进了 system | 缓存命中不了 |
| 多语言靠 if 嵌套 | 拆成多个文件更清楚 |

---

## 11. 下一步

- [A/B 与可观测](./04-ab-observability.md)
- [Prompt 的团队协作与 Review 流程](./05-team-collab.md)

## 参考资料

- Jinja2 文档: https://jinja.palletsprojects.com
- LangChain ChatPromptTemplate: https://python.langchain.com/docs/concepts/prompt_templates/
