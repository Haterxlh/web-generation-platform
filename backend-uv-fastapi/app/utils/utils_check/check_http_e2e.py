# app/utils/utils_check/check_http_e2e.py —— 开发期自检：异步生成链路的 HTTP 端到端
#
# 运行（在 backend-uv-fastapi 下）：
#   uv run python -m app.utils.utils_check.check_http_e2e
#
# 前置条件（三个都要满足，缺一个就会卡在"排队中"）：
#   1. Redis 已启动
#   2. API 已启动        uv run fastapi dev
#   3. worker 已启动     arq app.core.worker.WorkerSettings
#
# ⚠️ 本脚本会**真实调用大模型**（按 token 计费）。默认用 single 模式 + 短需求，成本很低；
#    只是为了验证"提交即返回 → 阶段推进 → 产物落库 → 可预览"这条链路是否通。
#    另外它会注册一个临时账号（账号名带时间戳，不会和已有账号冲突）。

import sys
import time
import uuid

import httpx

API_BASE = "http://127.0.0.1:8000/api"
PASSWORD = "probe123456"
# 轮询总上限：必须大于 worker 的 AGENT_JOB_TIMEOUT_SECONDS（默认 900s）才算合理，
# 但自检没必要等那么久 —— 单文件模式实测几十秒内就会结束。
POLL_DEADLINE_SECONDS = 300


def _register_and_login(client: httpx.Client) -> str:
    """注册一个临时账号并登录，返回 Bearer token。"""
    account = f"e2e{uuid.uuid4().hex[:10]}"
    response = client.post(
        f"{API_BASE}/user/register",
        json={"user_account": account, "user_password": PASSWORD},
    )
    print(f"注册 {account} -> HTTP {response.status_code}")
    response.raise_for_status()

    response = client.post(
        f"{API_BASE}/user/login",
        json={"user_account": account, "user_password": PASSWORD},
    )
    print(f"登录 -> HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()["access_token"]


def main() -> int:
    """跑一次完整的异步生成链路，返回退出码（0=通过）。"""
    with httpx.Client(timeout=30) as client:
        try:
            token = _register_and_login(client)
        except httpx.HTTPError as error:
            print(f"失败：API 不可用或注册/登录异常 -> {error}")
            print("处理：确认已执行 `uv run fastapi dev`")
            return 1

        headers = {"Authorization": f"Bearer {token}"}

        # ---- 1) 提交：必须**立即**返回 202，而不是等生成结束 ----
        started = time.perf_counter()
        response = client.post(
            f"{API_BASE}/generation/create",
            headers=headers,
            json={"prompt": "做一个极简的待办清单页面，可以添加和删除条目", "gen_type": "single"},
        )
        submit_seconds = time.perf_counter() - started
        print(f"\n提交 -> HTTP {response.status_code}（耗时 {submit_seconds:.2f}s）")
        if response.status_code != 202:
            print(f"失败：期望 202，实际 {response.status_code}：{response.text[:200]}")
            return 1
        if submit_seconds > 5:
            print("失败：提交耗时超过 5 秒，说明仍在同步执行（应改成入队即返回）")
            return 1

        accepted = response.json()
        task_uuid = accepted["task_uuid"]
        interval = accepted["poll_interval_ms"] / 1000
        print(f"     任务 {task_uuid}｜{accepted['stage_text']}｜"
              f"建议轮询间隔 {accepted['poll_interval_ms']}ms")

        # ---- 2) 轮询：记录每一次阶段变化 ----
        print("\n轮询进度：")
        transitions: list[tuple[str, str]] = []
        final: dict = {}
        deadline = time.time() + POLL_DEADLINE_SECONDS

        while time.time() < deadline:
            time.sleep(interval)
            detail = client.get(f"{API_BASE}/generation/{task_uuid}", headers=headers).json()

            key = (detail["status"], detail["stage"])
            if not transitions or transitions[-1] != key:
                transitions.append(key)
                print(f"   {time.perf_counter() - started:6.1f}s  {detail['status']:<8}"
                      f"{detail['stage']:<11}{detail['progress']:>3}%  {detail['stage_text']}")

            if detail["status"] != "running":
                final = detail
                break

        if not final:
            print(f"\n失败：{POLL_DEADLINE_SECONDS}s 内没有结束 —— worker 是否在跑？"
                  f"（arq --check app.core.worker.WorkerSettings）")
            return 1

        # ---- 3) 校验终态 ----
        print("\n终态：")
        for field in ("status", "stage", "progress", "file_list", "preview_url",
                      "duration_ms", "input_tokens", "output_tokens", "reasoning_tokens"):
            print(f"   {field:<18} {final.get(field)}")
        if final.get("error_msg"):
            print(f"   error_msg          {final['error_msg']}")

        problems: list[str] = []
        if final["status"] != "success":
            problems.append(f"终态不是 success（{final['status']}）")
        if final["stage"] != "done":
            problems.append(f"stage 不是 done（{final['stage']}）")
        if not final.get("file_list"):
            problems.append("产物文件列表为空")
        if not final.get("preview_url"):
            problems.append("没有 preview_url")
        if len(transitions) < 2:
            problems.append("没有观察到阶段推进（只有终态）")

        print("\n" + "=" * 60)
        if problems:
            print("结论：不通过 -> " + "；".join(problems))
            return 1
        print(f"结论：通过（观察到 {len(transitions)} 个阶段状态，产物 {final['file_list']}）")
        print(f"预览：http://127.0.0.1:8000{final['preview_url']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
