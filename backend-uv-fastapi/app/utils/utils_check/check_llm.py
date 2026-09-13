# app/utils/utils_check/check_llm.py —— 开发期脚本：验证 DeepSeek 连通性与参数
# 运行：uv run python -m app.utils.utils_check.check_llm （在 backend-uv-fastapi 目录下）
# 注意：会真实调用 API（花 token），所以不属于 pytest 用例

import time

from pydantic import BaseModel, Field

from app.core.llm_client import llm_client, llm_structured_client
from app.core.llm_config import llm_settings


class FilePlan(BaseModel):
    """（步骤 8 会正式用到）多文件模式的文件清单"""

    files: list[str] = Field(description="需要生成的文件名列表，如 index.html")


def check_basic() -> None:
    """① 普通对话：验证 key / base_url / 模型名 三件事都对"""
    t0 = time.perf_counter()
    resp = llm_client.invoke("用一句话介绍你自己。")
    cost = time.perf_counter() - t0

    print(resp.text)
    print(f"[耗时 {cost:.2f}s] usage={resp.usage_metadata}")


def check_stream() -> None:
    """② 流式输出：步骤 10（SSE）依赖它"""
    print("流式输出：", end="")
    for chunk in llm_client.stream("从 1 数到 5，只输出数字，用空格分隔。"):
        print(chunk.text, end="", flush=True)
    print()


def check_structured() -> None:
    """③ 结构化输出：用"非思考模式"的客户端 + 默认的 function_calling"""
    structured = llm_structured_client.with_structured_output(FilePlan)
    plan = structured.invoke("我要一个「点击按钮计数」的网页，拆成 html / css / js 三个文件")
    print(plan)
    print("类型检查：", type(plan).__name__, "files =", plan.files)


if __name__ == "__main__":
    print(f"model    = {llm_settings.deepseek_model}")
    print(f"thinking = {llm_settings.deepseek_thinking}  effort={llm_settings.deepseek_reasoning_effort}")
    print("-" * 60)
    check_basic()
    print("-" * 60)
    check_stream()
    print("-" * 60)
    check_structured()