# app/core/llm_client.py —— 模型客户端单例
# 类比 mysql_db.py：那边建 engine，这边建 chat model，都是"全局一份、复用"
# 为什么是两个：DeepSeek 的"思考模式"与"强制指定函数的结构化输出"互斥（详见下方注释）

from langchain.chat_models import init_chat_model

from app.core.llm_config import llm_settings

# 两个客户端共用的部分
_COMMON = {
    "model": llm_settings.deepseek_model,
    "model_provider": "deepseek",
    "api_key": llm_settings.deepseek_api_key,
    "base_url": llm_settings.deepseek_base_url,
    "max_tokens": llm_settings.llm_max_tokens,
}


def _build_client(*, thinking: str):
    """按"是否开启思考模式"构造客户端。

    `thinking` 不是 OpenAI 标准字段，DeepSeek 通过请求体的 `thinking` 传，
    在 OpenAI SDK 里要放进 `extra_body`，LangChain 会把 extra_body 透传给 SDK。

    Args:
        thinking: "enabled" 或 "disabled"。

    Returns:
        BaseChatModel: 配置好的聊天模型实例。
    """
    extra_body = {"thinking": {"type": thinking}}

    if thinking == "enabled":
        # 思考模式下 temperature 无效，改用它控制思考强度（放 extra_body 避免字段类型限制）
        extra_body["reasoning_effort"] = llm_settings.deepseek_reasoning_effort
        return init_chat_model(**_COMMON, extra_body=extra_body)

    # 非思考模式才轮到 temperature 生效
    return init_chat_model(**_COMMON, temperature=llm_settings.llm_temperature, extra_body=extra_body)


# ① 生成用：跑代码生成这种"要质量"的活，按 .env 的配置（默认 enabled）
llm_client = _build_client(thinking=llm_settings.deepseek_thinking)

# ② 结构化输出用：强制关闭思考模式
#    原因：思考模式不支持"强制指定某个函数"的 tool_choice，
#    而 with_structured_output(method="function_calling") 正是靠它来保证 schema 的。
#    步骤 8 的 plan 节点（生成文件清单）必须用这个客户端。
llm_structured_client = _build_client(thinking="disabled")