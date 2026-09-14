# app/agents/single_html_flow.py —— 单文件模式：一条 LangChain 链
# 职责边界（设计约定 §3.2）：**纯函数** —— 给它需求、还它 {文件名: 内容}；
# 不落盘、不落库、不碰 HTTP。所以它能脱离 MySQL / FastAPI 单独测试。

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda

from app.core.llm_client import llm_client
from app.utils.weg_gen.code_extractor import (
    extract_single_html, 
    CodeExtractError,
)
from app.utils.weg_gen.prompt_loader import load_prompt
from app.agents.common import (
    ensure_not_truncated, 
    _ensure_complete_html, 
    GenerationResult, 
    ModelUsage, 
    GenerationFailedError,
)


def _build_messages(prompt: str) -> list[BaseMessage]:
    """把用户需求包成"system 约束 + user 需求"两条消息。

    为什么用 SystemMessage 直接装提示词、而不是 ChatPromptTemplate：
    提示词里有大量 CSS / JS 花括号，模板引擎会把它们当变量解析直接报错
    （见 prompt_loader.py 注释）。这里 system 内容是原样读进来的字符串，
    用户需求拼在 HumanMessage 里，全程不经过模板解析。

    Args:
        prompt: 用户需求描述。

    Returns:
        两条消息组成的列表。
    """
    return [
        SystemMessage(content=load_prompt("single_html_system")),
        HumanMessage(content=f"用户需求：\n{prompt}"),
    ]


def build_single_html_chain(model: Runnable | None = None) -> Runnable:
    """构造单文件生成链。

    Args:
        model: 可注入的模型（默认用全局 llm_client）；测试时会传入假模型。

    Returns:
        LCEL 链：输入 str（用户需求）→ 输出 AIMessage。
    """
    return RunnableLambda(_build_messages) | (model or llm_client)


# 模块级只造一次链：链本身无状态，可反复 invoke
_CHAIN = build_single_html_chain()


def generate_single_html(prompt: str) -> GenerationResult:
    """根据需求生成单文件网页。

    Args:
        prompt: 用户需求描述。

    Returns:
        GenerationResult（files 形如 {"index.html": "<!DOCTYPE html>..."}）。

    Raises:
        GenerationFailedError: 输出被截断、结构不完整，或没有可用的 HTML 代码块。
    """
    message = _CHAIN.invoke(prompt)
    usage = ModelUsage.from_message(message)
    
    try:
        ensure_not_truncated(message)               # 第一道：模型自己说"我写不完了"
        files = extract_single_html(message.text)
        _ensure_complete_html(files["index.html"])  # 第二道：结构上验证真的写完了
    except (GenerationFailedError, CodeExtractError) as error:
        # 把用量挂到异常上再抛：失败也要记账
        raise GenerationFailedError(str(error), usage, raw_output=message.text) from error
    
    return GenerationResult(files=files, usage=usage)
