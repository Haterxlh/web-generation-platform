# app/utils/weg_gen/file_store.py —— per-request 的虚拟文件系统
#
# 为什么需要它（docs/agent_refactor_plan.md §3.7）：
#   Agent 的工具**不直接写磁盘**，而是写进这个内存字典；循环结束后由 service 一次性落盘。
#   三个理由，逐条都踩过坑：
#     1) 保住 agents 层「纯函数」契约（给它需求、还它 {文件名: 内容}，不落盘、不落库），
#        因此能脱离 MySQL / FastAPI 测试；
#     2) 避免半成品产物：直接写盘时中途失败会在用户目录留下残缺文件，
#        虚拟文件系统天然是「要么全交、要么全不交」；
#     3) 失败时仍可把最终快照交给 service 留证据（write_debug_raw）。
#
# ⚠️ 铁律：它必须是 **per-request 构造**的，绝不能做成模块级全局变量。
#    否则两个用户同时生成时，B 会读到 A 的文件、甚至覆盖 A 的产物 ——
#    这是本次改造里最隐蔽也最危险的 bug（§5 硬约束 2）。

from collections.abc import Iterable

from app.utils.weg_gen.file_writer import UnsafeFileNameError, safe_name


class FileStore:
    """一次生成过程中的虚拟文件系统（内存字典）。

    它**不是**通用文件系统：没有目录、没有二进制、没有元数据，
    只表达"这次要交付哪些文件、内容是什么"这一件事。
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    def put(self, name: str, content: str) -> str:
        """写入单个文件（同名覆盖）。

        Args:
            name: 文件名（单层名字，不允许任何路径成分）。
            content: 文件内容。

        Returns:
            校验并规范化后的文件名。

        Raises:
            UnsafeFileNameError: 文件名非法（含 / \\ .. 或绝对路径等）。
        """
        checked = safe_name(name)
        self._files[checked] = content
        return checked

    def put_many(self, files: dict[str, str]) -> list[str]:
        """批量写入（同名覆盖）。

        ⚠️ 先把**所有**文件名校验完再动手写：否则"写到一半撞上非法名字"，
        库里就留下了半批文件 —— 这正是"要么全交、要么全不交"要避免的中间态。

        Args:
            files: {文件名: 内容}。

        Returns:
            实际写入的文件名列表。

        Raises:
            UnsafeFileNameError: 其中任一文件名非法。
        """
        checked = {safe_name(name): content for name, content in files.items()}
        self._files.update(checked)
        return list(checked)

    def get(self, name: str) -> str | None:
        """读取文件内容。

        刻意**不做**文件名校验：读一个不存在的名字本来就只意味着"没有这个文件"，
        报错反而会让调用方多一层无意义的异常处理。

        Args:
            name: 文件名。

        Returns:
            文件内容；不存在时返回 None。
        """
        return self._files.get(name)

    def names(self) -> list[str]:
        """列出当前所有文件名（已排序，保证输出稳定便于测试与对比）。

        Returns:
            文件名列表。
        """
        return sorted(self._files)

    def sizes(self) -> dict[str, int]:
        """列出每个文件的字符数（给模型看"我写了多大"，比字节数对模型更直观）。

        Returns:
            {文件名: 字符数}。
        """
        return {name: len(content) for name, content in self._files.items()}

    def missing(self, required: Iterable[str]) -> list[str]:
        """找出 required 里**还没有**写入的文件名（完成门禁用）。

        Args:
            required: 期望存在的文件名集合。

        Returns:
            缺失的文件名列表。
        """
        return [name for name in required if name not in self._files]

    def snapshot(self) -> dict[str, str]:
        """取一份内容快照（拷贝，调用方改它不会影响 store）。

        Returns:
            {文件名: 内容}。
        """
        return dict(self._files)

    def __len__(self) -> int:
        return len(self._files)
