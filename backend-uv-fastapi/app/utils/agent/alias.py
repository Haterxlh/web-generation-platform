# app/utils/agent/alias.py —— 附件别名机制（纯函数：分配 / 解析 / 展开 / 校验）
#
# 目的（docs/agent_refactor_plan.md §3.6）：带文件的对话中，消息里只用别名引用文件，
# **绝不把文件正文或磁盘路径塞进消息**。真正"展开成摘要"只发生在拼 prompt 的时候。
#
# 收益：prompt 短（省 token）、不泄露磁盘路径、文件可重新解析而历史消息无需改写。
#
# 本模块是**纯函数**集合：不碰数据库、不碰 HTTP、不读写文件，因此可以整块离线单测。
# 数据库里的落地由 app/models/agent/generation_source.py 的 (session_id, alias) 唯一约束保证。

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# 别名形态：@doc1 / @doc2 …
ALIAS_PREFIX = "@doc"

# ⚠️ 边界条件是这个正则的全部价值所在。
#
# 最直觉的写法 `@doc(\d+)` 是错误的：它在邮箱 `a@doc1.com` 上会命中
# （`.` 是词边界，`\b` 拦不住），于是用户随手写的一个邮箱地址会被当成附件引用。
#
#   (?<![A-Za-z0-9_])  前面不能紧跟字母/数字/下划线 → 排除 a@doc1、x@doc1
#   (?![A-Za-z0-9_.])  后面不能紧跟字母/数字/下划线/点 → 排除 @doc1.com、@doc1x
#
# 于是只有"独立成词的 @docN"才会被识别，邮箱与普通 @ 提及天然错开。
ALIAS_RE = re.compile(r"(?<![A-Za-z0-9_])@doc(\d+)(?![A-Za-z0-9_.])")

# 别名的白名单校验（进 prompt 前用）：只允许 @doc + 数字。
# ⚠️ 必须带捕获组：parse_alias_index() 会用 group(1) 取序号（漏了会 IndexError）。
_SAFE_ALIAS_RE = re.compile(r"^@doc(\d{1,4})$")

# 角色 → 给模型看的中文说明
_ROLE_TEXT = {
    "content": "内容源",
    "style": "风格源",
    "both": "内容+风格源",
}


@dataclass(frozen=True)
class AliasTarget:
    """一个别名在 prompt 里需要展示的全部信息。

    刻意做成"扁平的数据"而不是直接传 ORM 对象：别名模块是纯函数层，
    不应该依赖数据库模型（否则单测就得造 ORM 对象）。

    Attributes:
        alias: 别名，形如 ``@doc1``。
        source_uuid: 指向的附件标识（`generation_source.source_uuid`）；
            渲染时用不到，但消息落库时要靠它记录"这条消息引用了哪个附件"。
        display_name: 原始文件名（只用于展示与宽松解析）。
        role: ``content`` / ``style`` / ``both``。
        digest: 解析出的需求摘要文本；None 表示**尚未解析**。
        available: False 表示该附件已被删除（逻辑删除）。
    """

    alias: str
    source_uuid: str | None = None
    display_name: str | None = None
    role: str = "content"
    digest: str | None = None
    available: bool = True


def format_alias(index: int) -> str:
    """把序号转成别名。

    Args:
        index: 从 1 开始的序号。

    Returns:
        形如 ``@doc1``。

    Raises:
        ValueError: 序号小于 1。
    """
    if index < 1:
        raise ValueError(f"别名序号必须从 1 开始，收到 {index}")
    return f"{ALIAS_PREFIX}{index}"


def parse_alias_index(alias: str) -> int | None:
    """从别名里取出序号。

    Args:
        alias: 形如 ``@doc3``。

    Returns:
        序号；格式不合法时返回 None（不抛异常，便于"宽松解析"）。
    """
    match = _SAFE_ALIAS_RE.match(alias or "")
    return int(match.group(1)) if match else None


def is_safe_alias(alias: str) -> bool:
    """别名是否可以安全地放进 prompt。

    这一步不是形式主义：别名会原样进入模型上下文，
    必须保证它不含换行、路径分隔符等可以"越权指令"的内容。

    Args:
        alias: 待校验的别名。

    Returns:
        True 表示合法。
    """
    return bool(_SAFE_ALIAS_RE.match(alias or ""))


def next_alias(existing: Iterable[str]) -> str:
    """在已占用的别名集合里挑出下一个可用别名。

    ⚠️ 别名**不可复用**：即使某个附件被删了，也不回收它的序号。
    否则历史消息里的 ``@doc1`` 会指向另一个文件 —— 那是静默的数据错乱。

    Args:
        existing: 会话内已占用的别名。

    Returns:
        下一个可用别名。
    """
    used = {parse_alias_index(alias) for alias in existing}
    used.discard(None)
    index = 1
    while index in used:
        index += 1
    return format_alias(index)


def find_aliases(text: str) -> list[str]:
    """按出现顺序取出正文里所有**形态正确**的别名（去重）。

    Args:
        text: 消息正文。

    Returns:
        别名列表；没有则返回空列表。
    """
    seen: dict[str, None] = {}
    for match in ALIAS_RE.finditer(text or ""):
        seen.setdefault(match.group(0))
    return list(seen)


def render_target(target: AliasTarget) -> str:
    """把别名目标渲染成一行"给模型看"的说明。

    措辞刻意面向模型：说清"这是什么文件、它是干什么用的、里面有什么要点"。

    Args:
        target: 别名目标。

    Returns:
        形如 ``【附件 @doc1｜设计规范.html｜角色：风格源｜摘要：…】``。
    """
    if not target.available:
        return f"【附件 {target.alias}（已失效）】"

    parts = [f"附件 {target.alias}"]
    if target.display_name:
        parts.append(target.display_name)
    parts.append(f"角色：{_ROLE_TEXT.get(target.role, target.role)}")

    if target.digest:
        parts.append(f"摘要：{target.digest}")
    else:
        # ⚠️ 尚未解析时必须显式占位，**不能静默省略**。
        # 否则"带附件的对话"会莫名少掉一个附件而毫无痕迹 —— 这正是我们一路在防的静默失败。
        parts.append("尚未解析")
    return "【" + "｜".join(parts) + "】"


def expand_aliases(text: str, targets: Mapping[str, AliasTarget]) -> str:
    """把正文里的别名展开成附件说明。

    三条规则（§3.6.1）：

    1. **只展开命中已注册别名的** ``@token``；
    2. 未命中的 ``@xxx`` **原样保留、不报错** —— 那是邮箱或普通的 @ 提及，误伤代价很高；
    3. 已删除的附件展开成"（已失效）"，**不阻断历史回放**。

    Args:
        text: 含别名的正文。
        targets: 本会话内"别名 → 别名目标"的映射。

    Returns:
        展开后的文本。
    """

    def _replace(match: re.Match[str]) -> str:
        alias = match.group(0)
        target = targets.get(alias)
        if target is None:
            return alias  # 规则 2：不认识就原样留着
        return render_target(target)

    return ALIAS_RE.sub(_replace, text or "")


def unresolved_aliases(text: str, targets: Mapping[str, AliasTarget]) -> list[str]:
    """找出正文里"像别名但当前会话没有注册"的 token。

    用途：附件**紧邻上下文**时提示用户"你写的 @doc9 不存在"，
    而不是对所有 ``@`` 都报错（那就误伤邮箱了）。

    Args:
        text: 正文。
        targets: 已注册别名映射。

    Returns:
        未注册的别名列表（去重、保序）。
    """
    return [alias for alias in find_aliases(text) if alias not in targets]
