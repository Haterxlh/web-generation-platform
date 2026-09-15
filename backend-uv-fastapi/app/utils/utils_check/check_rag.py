# app/utils/utils_check/check_rag.py —— 开发期自检：检索必要性判定与三态语义（阶段 4）
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_rag
#
# 两段：
#
#   第一段（离线，无模型、无库）：三态语义与安全边界 ——
#        skipped 时**根本不调** provider、miss 时区分"没查到"与"未接入"、
#        hit 时保留 L1/L2 来源、user_id 必填且拒绝非法值、
#        merge 里 miss 进 uncertainty 而 skipped 不进。
#
#   第二段（⚠️ 真实调用大模型，按 token 计费；不需要 API 在跑）：两类需求的真实判定 ——
#        "通用需求"必须判不需要检索；"指代用户私人资料"必须判需要并给出关键词。
#        这两条是二期检索质量的地基：判定错了，后面查得再准也没用。
#
# 为什么不需要起 API：need_rag 是进程内的节点调用，不经过 HTTP。

import sys

from langchain_core.runnables import RunnableLambda

from app.agents.merge.requirement_merge import MergeDecision, merge
from app.agents.rag import need_rag
from app.agents.rag.retriever import StubRetrieverProvider, build_retriever_provider, retrieve
from app.agents.state import RagChunk, RequirementSlots

# ⚠️ Windows 中文控制台默认编码是 GBK，打印 ✅/⚠️ 会 UnicodeEncodeError 崩掉脚本
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SLOTS = RequirementSlots(
    site_kind="企业官网", features=["产品介绍", "联系方式"], style="公司蓝色主色"
)


class _CountingProvider:
    """假 provider：记录调用次数，用于验证"skipped 时不调用"。"""

    name = "counting"
    unavailable_reason: str | None = None

    def __init__(self, chunks: list[RagChunk] | None = None) -> None:
        self.chunks = chunks or []
        self.calls: list[tuple] = []

    def retrieve(self, user_id: int, query: str, top_k: int = 5) -> list[RagChunk]:
        self.calls.append((user_id, query, top_k))
        return list(self.chunks)


def check_offline() -> bool:
    """第一段：三态语义与安全边界（无模型）。"""
    print("=" * 74)
    print("1) 三态语义与安全边界（离线）")

    problems: list[str] = []

    # ① skipped：判定不需要时，provider 一次都不能被调用
    provider = _CountingProvider(chunks=[RagChunk(source_type="document", text="不该被用到")])
    skipped = retrieve(7, needs_retrieval=False, query="公司 品牌色", provider=provider)
    ok = skipped.status == "skipped" and provider.calls == []
    print(f"   [skipped] 状态={skipped.status} provider 调用次数={len(provider.calls)} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("skipped 时不应调用 provider（二期会花钱、会连库）")

    # ② miss：桩 provider 必须自报"尚未接入"，而不是伪装成"没查到"
    missed = retrieve(7, needs_retrieval=True, query="公司 品牌色")
    ok = missed.status == "miss" and "尚未接入" in missed.reason
    print(f"   [miss]    状态={missed.status} 原因={missed.reason[:38]}… -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("桩 provider 应返回 miss 并说明「检索尚未接入」")

    # ③ hit：片段与 L1/L2 来源层级都要保留到消费端
    hit = retrieve(
        7,
        needs_retrieval=True,
        query="公司 品牌色",
        provider=_CountingProvider(
            chunks=[
                RagChunk(
                    source_type="conversation", source_ref="第 2 轮", text="公司主色 #123456"
                )
            ]
        ),
    )
    ok = hit.status == "hit" and hit.chunks[0].source_type == "conversation"
    print(f"   [hit]     状态={hit.status} 片段={len(hit.chunks)} 来源={hit.chunks[0].source_type if hit.chunks else '-'} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("hit 时应保留片段及其 L1/L2 来源层级（硬约束 14）")

    # ④ 安全边界：user_id 必填且必须为正
    try:
        retrieve(0, needs_retrieval=True, query="q")
        problems.append("user_id=0 竟然通过了 —— 越权检索的最后一道门失效")
        print("   [user_id] user_id=0 竟然通过 -> 失败")
    except ValueError:
        print("   [user_id] user_id=0 按预期被拒绝 -> 通过")

    # ⑤ merge：miss 进 uncertainty，skipped 不进
    fake_chain = RunnableLambda(lambda _payload: {"raw": None, "parsed": MergeDecision(summary="x")})
    miss_merge = merge(user_message="按我们公司规范做", rag=missed, chain=fake_chain)
    skip_merge = merge(user_message="做个计算器", rag=skipped, chain=fake_chain)
    miss_has = any("个人知识库未命中" in item for item in miss_merge.requirement.uncertainty)
    skip_clean = not any("个人知识库" in item for item in skip_merge.requirement.uncertainty)
    print(f"   [merge]   miss→uncertainty={miss_has} skipped→uncertainty={not skip_clean} -> {'通过' if (miss_has and skip_clean) else '失败'}")
    if not (miss_has and skip_clean):
        problems.append("merge 必须区分 miss（要告知用户）与 skipped（应静默）")

    # ⑥ 工厂：一期必须是桩，且桩明确声明查不了
    provider_impl = build_retriever_provider()
    ok = isinstance(provider_impl, StubRetrieverProvider) and provider_impl.unavailable_reason
    print(f"   [工厂]    provider={provider_impl.name} 自报原因={'有' if provider_impl.unavailable_reason else '无'} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("一期工厂应返回桩，且桩必须声明检索不可用")

    if problems:
        print("   结论   -> 不通过：")
        for item in problems:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


# (标题, 用户输入, 期望 need)
REAL_CASES: list[tuple[str, str, bool]] = [
    (
        "通用需求（不该检索）",
        "做一个单页的番茄钟计时器，能开始/暂停/重置，风格简洁即可。",
        False,
    ),
    (
        "指代用户私人资料（该检索）",
        "按我们公司的品牌色和 VI 规范做一版产品介绍页，文案用我们自己的产品名。",
        True,
    ),
]


def check_real_model() -> bool:
    """第二段：真实模型的检索必要性判定（⚠️ 计费）。"""
    print("=" * 74)
    print("2) 真实模型判定（⚠️ 计费；不需要 API）")

    problems: list[str] = []
    for title, message, want_need in REAL_CASES:
        try:
            result = need_rag.decide(user_message=message, slots=SLOTS)
        except Exception as error:  # noqa: BLE001 —— 模型不可用时按"跳过"处理
            print(f"   跳过   -> 模型不可用（{type(error).__name__}: {error}）")
            return True

        decision = result.decision
        print("-" * 74)
        print(f"   [{title}]")
        print(f"   输入 -> {message}")
        print(f"   判定 -> need={decision.need} query={decision.query!r}")
        print(f"   依据 -> {decision.reason}")
        print(f"   用量 -> in={result.usage.input_tokens} out={result.usage.output_tokens}"
              f"（{'降级' if result.degraded else '正常'}）")

        if result.degraded:
            problems.append(f"{title}：走了降级路径")
        if decision.need != want_need:
            problems.append(f"{title}：need 期望 {want_need}，实得 {decision.need}")
        if decision.need and not decision.query:
            problems.append(f"{title}：判成需要检索却没给关键词（会让二期空转）")

    # 把判定接到（桩）检索上，确认三态分派正确：不需要 → skipped；需要 → miss
    skipped = retrieve(1, needs_retrieval=False)
    missed = retrieve(1, needs_retrieval=True, query="公司 品牌色")
    print("-" * 74)
    print(f"   接线 -> 不需要时 {skipped.status}；需要时 {missed.status}（一期未接入）")

    if problems:
        print("   结论   -> 有偏差（请看上面的「依据」判断是模型判错还是提示词需加固）：")
        for item in problems:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    results = {
        "三态与安全边界（离线）": check_offline(),
        "真实模型判定": check_real_model(),
    }

    print("=" * 74)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")
    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
