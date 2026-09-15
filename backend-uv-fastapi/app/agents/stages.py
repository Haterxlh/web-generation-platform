# app/agents/stages.py —— Agent 流水线的阶段定义（编排层的"进度语言"）
#
# 为什么放 agents/：
#   "阶段"是 Agent 编排的概念，属于编排层。service 只负责把它写进数据库，
#   api 只负责把它翻译成给用户看的文案 —— 阶段有哪些、叫什么，只在这里定义一次。
#
# 为什么单独一个文件：
#   阶段会被 service（推进）、api（返回）、worker（重置）共同引用；
#   放在 common.py 里会让那个文件既管用量又管阶段，职责发糊。

from enum import StrEnum


class AgentStage(StrEnum):
    """一次生成流水线所处的阶段。

    ⚠️ 它与 `status` 是**两个正交维度**，不要合并成一个字段：

    - ``status``：生命周期 —— running / success / failed（任务整体是活着还是结束了）
    - ``stage``：本枚举 —— 任务走到了哪一步

    合并会立刻产生自相矛盾的状态（例如"success 但 stage 还停在 generating"）。
    """

    QUEUED = "queued"          # 已入队，等 worker 取（阶段 0 之后由 arq 负责）
    ROUTING = "routing"        # 意图识别（阶段 2 启用）
    DIGESTING = "digesting"    # 文档解析与需求归并（阶段 3 启用）
    RETRIEVING = "retrieving"  # 个人 RAG 检索（阶段 4 启用）
    PLANNING = "planning"      # 规划（阶段 5 启用）
    GENERATING = "generating"  # web-agent 工具调用生成（阶段 0 直接进这里）
    DONE = "done"              # 终态
    FAILED = "failed"          # 终态


# 阶段 → 给用户看的中文文案
STAGE_TEXT: dict[AgentStage, str] = {
    AgentStage.QUEUED: "排队中",
    AgentStage.ROUTING: "正在理解你的需求",
    AgentStage.DIGESTING: "正在解析文档",
    AgentStage.RETRIEVING: "正在检索你的资料",
    AgentStage.PLANNING: "正在规划文件结构",
    AgentStage.GENERATING: "正在生成网页",
    AgentStage.DONE: "已完成",
    AgentStage.FAILED: "已失败",
}

# 阶段 → 进度百分比。
# 刻意只给"有意义的阶段"定进度：FAILED 不在表内 ——
# 失败时应当**保留"失败在第几 %"**，归零反而丢失信息（见 generation_service._set_stage）。
STAGE_PROGRESS: dict[AgentStage, int] = {
    AgentStage.QUEUED: 0,
    AgentStage.ROUTING: 10,
    AgentStage.DIGESTING: 25,
    AgentStage.RETRIEVING: 40,
    AgentStage.PLANNING: 55,
    AgentStage.GENERATING: 70,
    AgentStage.DONE: 100,
}


def stage_text(stage: str | None) -> str:
    """把阶段值翻译成给用户看的中文文案。

    Args:
        stage: 数据库里的阶段字符串（可能是 None 或历史脏数据）。

    Returns:
        中文文案；未知值时原样返回，保证接口不会因为一条脏数据就 500。
    """
    if not stage:
        return ""
    try:
        return STAGE_TEXT[AgentStage(stage)]
    except ValueError:
        return stage
