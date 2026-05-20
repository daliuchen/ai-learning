"""
14_callbacks.py
===============
自定义 CallbackHandler：审计、token、费用
"""
from dotenv import load_dotenv

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()


class Auditor(BaseCallbackHandler):
    def __init__(self):
        self.in_tok = 0
        self.out_tok = 0
        self.events: list[tuple[str, dict]] = []

    def _push(self, name, **kw):
        self.events.append((name, kw))

    def on_chain_start(self, serialized, inputs, **kw):
        self._push("chain_start", name=serialized.get("name"), inputs_keys=list(inputs.keys()) if isinstance(inputs, dict) else None)

    def on_chain_end(self, outputs, **kw):
        self._push("chain_end", preview=str(outputs)[:80])

    def on_chat_model_start(self, serialized, messages, **kw):
        self._push("model_start", msgs=len(messages[0]))

    def on_llm_end(self, response, **kw):
        try:
            m = response.generations[0][0].message
            u = getattr(m, "usage_metadata", None)
            if u:
                self.in_tok += u["input_tokens"]
                self.out_tok += u["output_tokens"]
            self._push("model_end", usage=u)
        except Exception:
            self._push("model_end", usage=None)

    def on_tool_start(self, serialized, input_str, **kw):
        self._push("tool_start", name=serialized.get("name"), input=input_str)

    def on_tool_end(self, output, **kw):
        self._push("tool_end", output=str(output)[:80])


@tool
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


def main():
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是会用工具的助手"),
        ("human", "{q}"),
    ])
    model = ChatOpenAI(model="gpt-4o-mini").bind_tools([add])
    chain = prompt | model | StrOutputParser()

    auditor = Auditor()
    out = chain.invoke({"q": "请帮我算 12 + 30，再告诉我答案"}, config={"callbacks": [auditor]})
    print("\n最终输出:", out)
    print("\n审计事件:")
    for ev, data in auditor.events:
        print(f"  {ev:13s} {data}")
    print(f"\n输入 token={auditor.in_tok} 输出 token={auditor.out_tok}")


if __name__ == "__main__":
    main()
