# app/utils/utils_check/check_multi_response.py —— 线级诊断：把模型响应对象整个拆开看
"""直接调用一次模型（不经过图），逐个打印响应对象的组成部分。

用途：多文件反复"缺少代码块"、而提示词已经加到不能再加时，用来判断
      代码块到底有没有被生成出来 —— 是被写进了思考内容，还是被截断了。

用法：
    uv run python -m app.utils.utils_check.check_multi_response
    uv run python -m app.utils.utils_check.check_multi_response --no-thinking
"""

import sys

from langchain.messages import HumanMessage, SystemMessage

from app.core.llm_client import llm_client, llm_structured_client
from app.utils.weg_gen.code_extractor import extract_code_blocks
from app.utils.weg_gen.prompt_loader import load_prompt

PROMPT = "第一人称穿越门框后变化场景，无限循环，不断穿越门框，然后看到不同的场景"


def describe(label: str, text: str) -> None:
    """打印一段文本的代码块结构。

    Args:
        label: 这段文本来自响应对象的哪个字段。
        text: 文本内容。
    """
    blocks = extract_code_blocks(text)
    print(f"  [{label}] 字符数={len(text)}  围栏数={text.count('```')}  识别到={[b.lang for b in blocks]}")
    for index, block in enumerate(blocks):
        print(f"      [{index}] lang={block.lang!r} chars={len(block.content)}")


def main() -> None:
    """跑一次模型调用并打印响应对象的全部可见部分。"""
    no_thinking = "--no-thinking" in sys.argv
    # llm_structured_client 正是"强制关闭思考"的那个客户端，天然适合做对照组
    model = llm_structured_client if no_thinking else llm_client
    print(f"=== 客户端: {'thinking=disabled（对照组）' if no_thinking else 'thinking=enabled（现状）'} ===")

    message = model.invoke(
        [
            SystemMessage(content=load_prompt("multi_file_system")),
            HumanMessage(content=f"用户需求：\n{PROMPT}"),
        ]
    )

    print("\n--- response_metadata（看 finish_reason）---")
    print(message.response_metadata)
    print("\n--- usage_metadata（看思考占比）---")
    print(message.usage_metadata)

    print("\n--- content 的类型 ---")
    print("  type:", type(message.content).__name__)
    if isinstance(message.content, list):
        for index, block in enumerate(message.content):
            if isinstance(block, dict):
                print(
                    f"    [{index}] type={block.get('type')!r} "
                    f"keys={sorted(block.keys())} text_len={len(str(block.get('text', '')))}"
                )

    print("\n--- additional_kwargs 的构成 ---")
    for key, value in (message.additional_kwargs or {}).items():
        size = len(value) if isinstance(value, (str, list)) else value
        print(f"  {key}: {type(value).__name__} 长度/值={size}")

    print("\n--- 可见答案 message.text ---")
    describe("text", message.text)

    print("\n--- 思考内容里有没有藏着 css/js？---")
    for key, value in (message.additional_kwargs or {}).items():
        if isinstance(value, str) and value:
            has_fence = "```" in value
            has_files = "```css" in value or "```js" in value or "style.css" in value
            print(f"  {key}: 有围栏={has_fence} 出现css/js迹象={has_files}")
            if has_fence or has_files:
                print(f"    ⚠️ 这个字段里出现了代码/文件名 —— 极可能藏着我们没拿到的 css/js！")
                describe(key, value)


if __name__ == "__main__":
    main()