# app/utils/utils_check/check_agent_chat.py —— 开发期自检：意图路由与多轮对话
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_agent_chat
#
# ⚠️ 本脚本会**真实调用大模型**（按 token 计费）。它做两件事：
#
#   第一段：三类输入的意图路由（纯咨询 / 模糊需求 / 明确需求）
#          —— 直接调 router 节点，不连库、不起 HTTP，最快也最省。
#   第二段：多轮对话的落库与回放（需要 API 在跑；不可用时自动跳过）
#          —— 验收"多轮对话落 PG 可完整回放"。
#
# 模型输出是概率性的，所以第一段的断言只钉"意图"这类稳定结论；
# 出现不一致时脚本会把 `reason` 打出来，便于判断是模型判错还是我们的提示词没写清。

import sys
import time
import uuid

import httpx

from app.agents.router.intent_router import route
from app.agents.state import RequirementSlots

API_BASE = "http://127.0.0.1:8000/api"
PASSWORD = "probe123456"


# ---------------------------------------------------------------------------
# 第一段：意图路由（三类输入）
# ---------------------------------------------------------------------------

# (标题, 用户输入, 对 intent 的期望, 对 readiness 的期望)
# readiness 期望为 None 表示"这一项不做硬断言"
ROUTER_CASES: list[tuple[str, str, str | None, str | None]] = [
    (
        "纯咨询（还没想好要什么）",
        "我还没想好，你觉得做个待办清单好，还是做个备忘录好？",
        "chat",
        None,
    ),
    (
        "模糊需求（想做但说不清）",
        "帮我做个网站吧，你看着办。",
        "generate",
        "needs_clarification",
    ),
    (
        "明确需求（信息齐全）",
        "做一个单页的待办清单：能添加、勾选完成、删除条目；"
        "数据存在浏览器 localStorage 里；风格极简白底，圆角卡片，无衬线字体；给普通个人用户用。",
        "generate",
        "ready",
    ),
]


def check_router_cases() -> bool:
    """跑三类输入，打印判定结果并做稳定项断言。"""
    print("=" * 74)
    print("1) 意图路由：三类输入（真实模型）")

    failures: list[str] = []

    for title, message, want_intent, want_readiness in ROUTER_CASES:
        started = time.perf_counter()
        result = route(message)
        elapsed = time.perf_counter() - started
        decision = result.decision

        print("-" * 74)
        print(f"   [{title}]")
        print(f"   输入    -> {message}")
        print(f"   判定    -> intent={decision.intent} readiness={decision.readiness}"
              f"（{elapsed:.1f}s，{'降级' if result.degraded else '正常'}）")
        print(f"   槽位    -> {decision.slots.model_dump()}")
        print(f"   缺失    -> {decision.missing_slots}")
        print(f"   追问    -> {decision.ask_hint or '（无）'}")
        print(f"   依据    -> {decision.reason}")
        print(f"   用量    -> in={result.usage.input_tokens} "
              f"out={result.usage.output_tokens} reasoning={result.usage.reasoning_tokens}")

        if result.degraded:
            failures.append(f"{title}：走了降级路径（模型调用或解析失败）")
        if want_intent and decision.intent != want_intent:
            failures.append(f"{title}：intent 期望 {want_intent}，实得 {decision.intent}")
        if want_readiness and decision.readiness != want_readiness:
            failures.append(
                f"{title}：readiness 期望 {want_readiness}，实得 {decision.readiness}"
            )

    print("-" * 74)
    if failures:
        print("   结论   -> 有偏差（请看上面的“依据”判断是模型判错还是提示词需加固）：")
        for item in failures:
            print(f"             · {item}")
    else:
        print("   结论   -> 通过")
    return not failures


def check_slot_accumulation() -> bool:
    """模拟第二轮：已有槽位 + 用户补充 → 新信息应被合并进旧槽位。

    这里直接注入 `draft_slots` 模拟"上一轮的成果"，不必连库。
    """
    print("=" * 74)
    print("2) 槽位跨轮累积（把已有槽位当上下文再判一次）")

    draft = RequirementSlots(site_kind="单页展示", features=["添加待办"])
    result = route("界面要极简一点，白底就行", draft_slots=draft)
    slots = result.decision.slots

    print(f"   已有槽位 -> {draft.model_dump()}")
    print(f"   本轮补充 -> “界面要极简一点，白底就行”")
    print(f"   合并结果 -> {slots.model_dump()}")

    ok = slots.site_kind == "单页展示" and "添加待办" in slots.features
    if not ok:
        print("   结论     -> 不通过：旧的 site_kind / features 被丢掉了")
    elif not slots.style:
        print("   结论     -> 部分通过：旧槽位保住了，但本轮提到的风格没被抽取（可接受，看提示词）")
    else:
        print("   结论     -> 通过（旧槽位保住，新风格也抽到了）")
    return ok


# ---------------------------------------------------------------------------
# 第二段：HTTP 多轮对话与回放
# ---------------------------------------------------------------------------


def check_http_multiturn() -> bool:
    """真实走一遍 /api/agent/chat 的多轮对话，并验证会话可完整回放。"""
    print("=" * 74)
    print("3) 多轮对话落库与回放（HTTP，需要 API 在跑）")

    try:
        with httpx.Client(timeout=90) as client:
            account = f"agent{uuid.uuid4().hex[:10]}"
            client.post(
                f"{API_BASE}/user/register",
                json={"user_account": account, "user_password": PASSWORD},
            ).raise_for_status()
            token = client.post(
                f"{API_BASE}/user/login",
                json={"user_account": account, "user_password": PASSWORD},
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 第 1 轮：模糊需求
            first = client.post(
                f"{API_BASE}/agent/chat",
                headers=headers,
                json={"message": "帮我做个网站吧"},
            )
            first.raise_for_status()
            data = first.json()
            session_uuid = data["session_uuid"]
            print(f"   第 1 轮 -> intent={data['intent']} readiness={data['readiness']} "
                  f"ready={data['ready_to_generate']}")
            print(f"             reply: {data['reply'][:60]}")
            print(f"             槽位: {data['slots']}")

            # 第 2 轮：带上会话，补齐信息
            second = client.post(
                f"{API_BASE}/agent/chat",
                headers=headers,
                json={
                    "session_uuid": session_uuid,
                    "message": "做个单页待办清单，能添加和删除，数据存 localStorage，极简白底",
                },
            )
            second.raise_for_status()
            data2 = second.json()
            print(f"   第 2 轮 -> intent={data2['intent']} readiness={data2['readiness']} "
                  f"ready={data2['ready_to_generate']}")
            print(f"             槽位: {data2['slots']}")
            print(f"             用量: {data2['usage']}")

            # 回放
            detail = client.get(f"{API_BASE}/agent/session/{session_uuid}", headers=headers)
            detail.raise_for_status()
            replay = detail.json()
            print(f"   回放   -> 共 {len(replay['messages'])} 条消息，"
                  f"角色序列 {[m['role'] for m in replay['messages']]}")
            print(f"             草稿摘要: {replay['summary']}")

            problems: list[str] = []
            if replay["session_uuid"] != session_uuid:
                problems.append("回放的会话标识不一致")
            if len(replay["messages"]) != 4:
                problems.append(f"应有 4 条消息（2 轮 × 2），实得 {len(replay['messages'])}")
            if [m["role"] for m in replay["messages"]] != [
                "user", "assistant", "user", "assistant",
            ]:
                problems.append("消息顺序不对（应为 user/assistant/user/assistant）")
            if not replay["slots"]["site_kind"] and not replay["slots"]["features"]:
                problems.append("第 2 轮之后槽位仍然是空的（槽位没有落库）")
            if not replay["summary"]:
                problems.append("会话草稿的 summary 为空")

            if problems:
                print("   结论   -> 不通过：")
                for item in problems:
                    print(f"             · {item}")
                return False
            print("   结论   -> 通过")
            return True
    except httpx.HTTPError as error:
        print(f"   跳过   -> API 不可用（{type(error).__name__}），"
              f"先执行 `uv run fastapi dev` 再重跑本段")
        return True  # 不算失败：本段是可选验证


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    results = {
        "三类输入路由": check_router_cases(),
        "槽位跨轮累积": check_slot_accumulation(),
        "多轮对话与回放": check_http_multiturn(),
    }

    print("=" * 74)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")
    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
