# app/utils/utils_check/check_compare.py —— 阶段 7 开发期自检：新旧实现对照实验
#
# 运行（在 backend-uv-fastapi 下）：
#   .venv\Scripts\python.exe -m app.utils.utils_check.check_compare --dry-run
#   .venv\Scripts\python.exe -m app.utils.utils_check.check_compare
#   （uv 可用时把前一段换成 `uv run python -m app.utils.utils_check.check_compare`）
#
# 前置条件（三个都要满足，缺一个任务就会一直停在"排队中"）：
#   1. Redis 已启动          docker compose up -d redis
#   2. API 已启动            uv run fastapi dev
#   3. worker 已启动且是当前代码  arq app.core.worker.WorkerSettings
#      ⚠️ arq **不热重载**：改了 agents/ 或 services/ 必须重启 worker
#
# ⚠️ 本脚本会**真实调用大模型**（按 token 计费）。
#    默认规模 = 3 个需求 × 3 种模式 × 1 次 = 9 次生成；先用 --dry-run 看清单再决定开跑。
#    它还会注册一个临时账号（账号名带随机串，不与已有账号冲突）。
#
# 它回答阶段 7 的问题：**旧实现（single / multi）该不该退役**。
# 采集四类数据（同一批需求、出厂配置下对照，不人为拉平客户端差异）：
#   ① 成功率   status == success
#   ② 产物完整度 入口存在 / 清单文件全部落盘 / 入口引用到的本地资源都在 / 入口不是截断的 HTML
#   ③ 总 token   input / output / reasoning（含失败任务，失败也计费）
#   ④ 耗时     提交响应耗时（同步/异步的体感差异）与任务 duration_ms
#
# 另：agent 模式会顺带打出 `generation_plan` 的「预估 vs 实际」对账，用于校正难度档位。

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.settings_base import BASE_DIR
from app.core.storage_config import storage_settings

API_BASE = "http://127.0.0.1:8000/api"
WEB_BASE = "http://127.0.0.1:8000"
PASSWORD = "probe123456"
ENTRY_FILE = "index.html"

# 单次轮询上限（秒）。必须大于 worker 的 AGENT_JOB_TIMEOUT_SECONDS（默认 900）才"等得完"，
# 但对照实验没必要真等 15 分钟 —— 复杂需求实测 200 秒内结束，超时就按"未在期限内结束"记录。
DEFAULT_POLL_DEADLINE = 600


@dataclass(frozen=True)
class Need:
    """一个对照需求（三个需求构成简单 → 复杂的梯度）。

    Attributes:
        need_id: 短标识（ASCII，表格里用它保证对齐）。
        title: 中文标题（单独打印成图例，不塞进表格）。
        prompt: 提交给接口的需求原文（**纯文本、不带附件** —— 附件只有 agent 支持，
            带上就等于让旧实现直接弃权，测不出真实差距）。
        expected_min_files: 按需求常识预期的最少交付文件数（用于"规模是否缩水"的观察项，
            **不计入完整度判定** —— 那是主观预期，不是客观缺陷）。
        expected_note: 预期形态说明。
    """

    need_id: str
    title: str
    prompt: str
    expected_min_files: int
    expected_note: str


NEEDS: tuple[Need, ...] = (
    Need(
        need_id="r1",
        title="简单：单页待办清单",
        prompt="做一个极简的待办清单页面，可以添加、勾选完成和删除条目。",
        expected_min_files=1,
        expected_note="单文件即可",
    ),
    Need(
        need_id="r2",
        title="中等：本地记账本（localStorage）",
        prompt=(
            "做一个本地记账本页面：能记录收入/支出条目（金额、分类、备注、日期），"
            "按月份汇总总收入、总支出和结余，数据存在浏览器 localStorage 里，刷新不丢。"
        ),
        expected_min_files=3,
        expected_note="预期 html+css+js 三件套",
    ),
    Need(
        need_id="r3",
        title="复杂：企业官网四页 + 数据文件",
        prompt=(
            "做一个企业官网，包含首页、产品页、关于我们、联系我们四个页面，"
            "产品列表要能从一个数据文件里读取并渲染，整体风格简洁专业。"
        ),
        expected_min_files=4,
        expected_note="预期 4 个页面 + 数据文件",
    ),
)

MODES: tuple[str, ...] = ("single", "multi", "agent")

# 入口文件里引用本地资源的两种写法；http(s)/data/mailto/#/javascript 一律不算本地引用
_REF_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "javascript:", "#")

# 只在**标记语言**里找引用：`<script>` / `<style>` 的**内容**（不是标签本身）与 HTML 注释不算引用。
# ⚠️ 注意区分"标签"与"内容"：`<script src="script.js">` 的 src 是**真引用**，
# 必须保留；只有标签之间的 JS/CSS 正文才要跳过。所以这里取的是捕获组的 span（内容区间）。
_IGNORE_CONTENT_RES = (
    re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<!--(.*?)-->", re.DOTALL),
)


@dataclass
class RunRow:
    """一次生成的完整观测记录（一行 = 一个 (需求, 模式, 第几次) 组合）。"""

    need_id: str
    mode: str
    attempt: int = 1
    task_uuid: str | None = None
    submit_seconds: float | None = None
    e2e_seconds: float | None = None
    status: str | None = None
    stage: str | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    file_list: list[str] = field(default_factory=list)
    file_sizes: dict[str, int] = field(default_factory=dict)
    integrity_ok: bool = False
    integrity_failures: list[str] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    scope_ok: bool = False
    preview_status: int | None = None
    preview_bytes: int = 0
    stage_track: list[str] = field(default_factory=list)
    error_msg: str | None = None
    plan: dict[str, Any] | None = None

    @property
    def delivered(self) -> bool:
        """是否做到"可用交付"：既成功结束，产物结构也完整。"""
        return self.status == "success" and self.integrity_ok


# ==================== 采集工具 ====================


def _register_and_login(client: httpx.Client) -> tuple[str, int]:
    """注册一个临时账号并登录，返回 (Bearer token, user_id)。"""
    account = f"cmp{uuid.uuid4().hex[:10]}"
    response = client.post(
        f"{API_BASE}/user/register",
        json={"user_account": account, "user_password": PASSWORD},
    )
    response.raise_for_status()
    response = client.post(
        f"{API_BASE}/user/login",
        json={"user_account": account, "user_password": PASSWORD},
    )
    response.raise_for_status()
    body = response.json()
    print(f"实验账号 {account}（user_id={body['user']['id']}）")
    return body["access_token"], body["user"]["id"]


def _submit(
    client: httpx.Client, headers: dict[str, str], need: Need, mode: str
) -> tuple[str, float, float]:
    """提交一次生成，返回 (task_uuid, 提交响应耗时秒, 建议轮询间隔秒)。

    提交耗时本身是观测项：异步化之后它应当是百毫秒级，
    而旧实现（阶段 0 之前）在这里要阻塞 30~157 秒。
    轮询间隔取服务端建议值（不写死在脚本里，改配置无需改脚本）。
    """
    started = time.perf_counter()
    response = client.post(
        f"{API_BASE}/generation/create",
        headers=headers,
        json={"prompt": need.prompt, "gen_type": mode},
    )
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    body = response.json()
    return body["task_uuid"], elapsed, max(1.0, body["poll_interval_ms"] / 1000)


def _poll(
    client: httpx.Client,
    headers: dict[str, str],
    task_uuid: str,
    interval: float,
    deadline: float,
) -> tuple[dict[str, Any] | None, list[str], float]:
    """轮询到终态，返回 (终态详情, 阶段轨迹, 端到端耗时秒)。

    终态为 None 表示轮询超时（多半是 worker 没起或任务卡住）。
    """
    started = time.perf_counter()
    track: list[str] = []
    while time.perf_counter() - started < deadline:
        time.sleep(interval)
        detail = client.get(f"{API_BASE}/generation/{task_uuid}", headers=headers).json()
        key = f"{detail['status']}/{detail['stage']}@{detail['progress']}"
        if not track or track[-1] != key:
            track.append(key)
        if detail["status"] != "running":
            return detail, track, time.perf_counter() - started
    return None, track, time.perf_counter() - started


def _local_refs(html: str) -> list[str]:
    """抽出 HTML 里引用的**本地**资源路径（去掉外链、锚点与 query/hash）。

    ⚠️ 必须跳过 ``<script>`` / ``<style>`` 的**正文**再抽：模型常用 JS 模板串渲染图片
    （``<img src="${esc(p.image)}">``），若连这些一起扫，就会把模板表达式当成"缺失的文件"，
    把一个**正常产物误判成不完整** —— 这正是本脚本第一版踩到的假阳性（2026-09-15）。
    只扫标记语言，判据才与"浏览器打开时会不会 404"一致。
    """
    ignored = [
        match.span(1)
        for pattern in _IGNORE_CONTENT_RES
        for match in pattern.finditer(html)
    ]

    refs: list[str] = []
    for match in _REF_RE.finditer(html):
        start = match.start()
        if any(low <= start < high for low, high in ignored):
            continue
        value = match.group(1).strip()
        if not value or value.startswith(_EXTERNAL_PREFIXES):
            continue
        if "${" in value or "{{" in value:  # 模板表达式：运行期才成形，不是静态引用
            continue
        value = value.split("?")[0].split("#")[0].strip()
        if value:
            refs.append(value)
    return refs


def _check_integrity(
    directory: Path, file_list: list[str], file_sizes: dict[str, int]
) -> tuple[bool, list[str], list[str]]:
    """判定产物完整度（**客观缺陷**，不含"文件数够不够"这种主观预期）。

    四条判据：
        1. 清单里声明的每个文件都真的落盘了（大小 >= 0）
        2. 清单里有入口文件 ``index.html``
        3. 入口文件是**完整的 HTML**（含 ``<html`` / ``</html>``，且不小于 200 字节）
           —— 模型输出被 token 上限截断时，最典型的现象就是这四条里最后这条不过
        4. 入口文件引用到的本地资源（css / js / 图片）都存在

    Returns:
        (是否完整, 失败原因列表, 缺失的引用列表)。
    """
    failures: list[str] = []
    missing_refs: list[str] = []

    for name, size in file_sizes.items():
        if size < 0:
            failures.append(f"清单声明的 {name} 未落盘")

    if ENTRY_FILE not in file_list:
        failures.append(f"清单里没有入口文件 {ENTRY_FILE}")

    entry_path = directory / ENTRY_FILE
    if not entry_path.is_file():
        failures.append(f"{ENTRY_FILE} 不存在，无法继续校验")
        return False, failures, missing_refs

    if entry_path.stat().st_size < 200:
        failures.append("入口文件小于 200 字节，疑似截断")

    html = entry_path.read_text(encoding="utf-8", errors="ignore")
    lowered = html.lower()
    if "<html" not in lowered:
        failures.append("入口文件不含 <html")
    if "</html>" not in lowered:
        failures.append("入口文件不含 </html>（疑似输出被截断）")

    root = entry_path.parent.resolve()
    for ref in _local_refs(html):
        target = (entry_path.parent / ref).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            missing_refs.append(f"{ref}（越出产物目录）")
            continue
        if not target.is_file():
            missing_refs.append(ref)
    if missing_refs:
        failures.append("入口引用的本地资源缺失：" + "、".join(missing_refs))

    return (not failures), failures, missing_refs


def _check_preview(
    client: httpx.Client, headers: dict[str, str], task_uuid: str
) -> tuple[int | None, int, str | None]:
    """签发票据并真实拉一次产物，返回 (预览 HTTP 状态, 正文字节数, 错误说明)。

    票据是 HttpOnly Cookie（httpx 的 cookie jar 会自动带上），
    这一步验证的是"用户点开预览真的看得到东西"，而不是"库里有个 URL"。
    """
    response = client.post(f"{API_BASE}/generation/{task_uuid}/preview-ticket", headers=headers)
    if response.status_code != 200:
        return response.status_code, 0, response.text[:200]
    preview_url = response.json()["preview_url"]
    preview = client.get(f"{WEB_BASE}{preview_url}")
    return preview.status_code, len(preview.text), preview_url


def _plan_reconciliation(task_uuid: str) -> dict[str, Any] | None:
    """读 agent 模式的规划对账行（PG）；表里没有则返回 None。

    读库失败不打断实验：对账只是附加证据，主指标（成功率 / 完整度 / token / 耗时）全在 HTTP 侧。
    """
    try:
        from app.core.pg_db import PgSessionLocal
        from app.repositories.agent.plan_repository import GenerationPlanRepository

        db = PgSessionLocal()
        try:
            plan = GenerationPlanRepository.get_latest_by_task_uuid(db, task_uuid)
            if plan is None:
                return None
            file_plan = plan.file_plan or {}
            return {
                "difficulty_declared": plan.difficulty_declared,
                "difficulty": plan.difficulty,
                "planned_file_count": plan.planned_file_count,
                "budget_steps": plan.budget_steps,
                "budget_output_tokens": plan.budget_output_tokens,
                "validation_warnings": plan.validation_warnings,
                "actual_steps": plan.actual_steps,
                "actual_file_count": plan.actual_file_count,
                "outcome_status": plan.outcome_status,
                "entry_file": file_plan.get("entry_file"),
                "planned_files": [f.get("name") for f in file_plan.get("files", [])],
            }
        finally:
            db.close()
    except Exception as error:  # noqa: BLE001 —— 对账失败不该让整场实验崩掉
        return {"error": f"{type(error).__name__}: {error}"}


def _run_once(
    client: httpx.Client,
    headers: dict[str, str],
    need: Need,
    mode: str,
    attempt: int,
    deadline: float,
) -> RunRow:
    """跑一个 (需求, 模式) 组合并采齐全部观测项。"""
    row = RunRow(need_id=need.need_id, mode=mode, attempt=attempt)
    try:
        row.task_uuid, row.submit_seconds, interval = _submit(client, headers, need, mode)
    except httpx.HTTPStatusError as error:
        row.status = f"http_{error.response.status_code}"
        row.error_msg = error.response.text[:200]
        return row
    except httpx.HTTPError as error:
        row.status = "submit_error"
        row.error_msg = f"{type(error).__name__}: {error}"
        return row

    detail, track, e2e = _poll(client, headers, row.task_uuid, interval, deadline)
    row.stage_track = track
    row.e2e_seconds = round(e2e, 1)
    if detail is None:
        row.status = "poll_timeout"
        row.error_msg = f"{deadline:.0f}s 内没有结束（worker 是否在跑？）"
        return row

    row.status = detail["status"]
    row.stage = detail["stage"]
    row.duration_ms = detail.get("duration_ms")
    row.input_tokens = detail.get("input_tokens")
    row.output_tokens = detail.get("output_tokens")
    row.reasoning_tokens = detail.get("reasoning_tokens")
    row.file_list = list(detail.get("file_list") or [])
    row.error_msg = detail.get("error_msg")

    result_dir = detail.get("result_dir")
    if not result_dir:
        # 没落盘就没有产物可校验；失败任务通常走到这里
        row.integrity_failures.append("没有 result_dir（产物未落盘）")
        if mode == "agent":
            row.plan = _plan_reconciliation(row.task_uuid)
        return row

    directory = storage_settings.generated_path / result_dir
    file_sizes: dict[str, int] = {}
    for name in row.file_list:
        path = directory / name
        file_sizes[name] = path.stat().st_size if path.is_file() else -1
    row.file_sizes = file_sizes
    row.integrity_ok, row.integrity_failures, row.missing_refs = _check_integrity(
        directory, row.file_list, file_sizes
    )
    row.scope_ok = len(row.file_list) >= need.expected_min_files

    if row.file_list:
        status, size, note = _check_preview(client, headers, row.task_uuid)
        row.preview_status = status
        row.preview_bytes = size
        if status != 200:
            row.integrity_failures.append(f"预览不可用（HTTP {status}：{note}）")
            row.integrity_ok = False

    if mode == "agent":
        row.plan = _plan_reconciliation(row.task_uuid)

    return row


# ==================== 汇总与打印 ====================


def _aggregate(rows: list[RunRow]) -> dict[str, dict[str, Any]]:
    """按模式汇总四个主指标。

    除了平均值，还给一个**决策真正关心的数**：`out_tokens_per_delivery`
    —— 每份"可用交付"平均烧掉多少输出 token（含失败任务的浪费）。
    只看成功率会漏掉"次次成功但代价高 3 倍"这种情况。
    """
    summary: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        subset = [r for r in rows if r.mode == mode]
        if not subset:
            continue
        delivered = [r for r in subset if r.delivered]
        outs = [r.output_tokens for r in subset if r.output_tokens is not None]
        ins = [r.input_tokens for r in subset if r.input_tokens is not None]
        durations = [r.duration_ms for r in subset if r.duration_ms is not None]
        e2es = [r.e2e_seconds for r in subset if r.e2e_seconds is not None]
        total_out = sum(outs) if outs else 0
        summary[mode] = {
            "runs": len(subset),
            "success": sum(1 for r in subset if r.status == "success"),
            "integrity_ok": sum(1 for r in subset if r.integrity_ok),
            "delivered": len(delivered),
            "success_rate": round(sum(1 for r in subset if r.status == "success") / len(subset), 2),
            "delivery_rate": round(len(delivered) / len(subset), 2),
            "avg_input_tokens": round(sum(ins) / len(ins)) if ins else None,
            "avg_output_tokens": round(sum(outs) / len(outs)) if outs else None,
            "out_tokens_per_delivery": round(total_out / len(delivered)) if delivered else None,
            "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
            "avg_e2e_seconds": round(sum(e2es) / len(e2es), 1) if e2es else None,
        }
    return summary


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    """打印一张按列宽对齐的 ASCII 表（表格里只用 ASCII，中文另作图例，避免宽度错位）。"""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def _print_report(rows: list[RunRow], summary: dict[str, dict[str, Any]]) -> None:
    """把明细、汇总与 agent 对账打到控制台。"""
    print("\n需求图例：")
    for need in NEEDS:
        print(f"  {need.need_id}  {need.title}（{need.expected_note}）")

    print("\n" + "=" * 100)
    print("1) 明细（integ=产物完整  scope=文件数达预期  e2e=提交到终态秒数）")
    print("=" * 100)
    detail_rows: list[list[Any]] = []
    for row in rows:
        detail_rows.append(
            [
                row.need_id,
                row.mode,
                row.status,
                "Y" if row.integrity_ok else "N",
                "Y" if row.scope_ok else "N",
                len(row.file_list),
                row.input_tokens,
                row.output_tokens,
                row.reasoning_tokens,
                f"{row.submit_seconds:.2f}" if row.submit_seconds is not None else "-",
                row.e2e_seconds,
                row.preview_status,
            ]
        )
    _print_table(
        ["need", "mode", "status", "integ", "scope", "files", "in", "out",
         "reason", "submit(s)", "e2e(s)", "preview"],
        detail_rows,
    )

    print("\n" + "=" * 100)
    print("2) 按模式汇总（delivery = 成功且产物完整；out/delivery = 每份可用交付的输出 token）")
    print("=" * 100)
    summary_rows: list[list[Any]] = []
    for mode, data in summary.items():
        summary_rows.append(
            [
                mode,
                data["runs"],
                data["success"],
                data["integrity_ok"],
                data["delivered"],
                data["avg_input_tokens"],
                data["avg_output_tokens"],
                data["out_tokens_per_delivery"],
                data["avg_duration_ms"],
                data["avg_e2e_seconds"],
            ]
        )
    _print_table(
        ["mode", "runs", "ok", "integ", "delivery", "avg_in", "avg_out",
         "out/delivery", "avg_ms", "avg_e2e(s)"],
        summary_rows,
    )

    failed = [row for row in rows if not row.delivered]
    if failed:
        print("\n未达成可用交付的用例（含失败原因与完整度诊断）：")
        for row in failed:
            reasons = row.error_msg or "；".join(row.integrity_failures) or "未知"
            print(f"  {row.need_id}/{row.mode}: {str(reasons)[:160]}")

    plan_rows = [row for row in rows if row.plan and "error" not in row.plan]
    if plan_rows:
        print("\n" + "=" * 100)
        print("3) agent 模式「预估 vs 实际」对账（用于校正难度档位）")
        print("=" * 100)
        recon_rows: list[list[Any]] = []
        for row in plan_rows:
            plan = row.plan or {}
            names = ",".join(plan.get("planned_files") or [])
            recon_rows.append(
                [
                    row.need_id,
                    plan.get("difficulty_declared"),
                    plan.get("difficulty"),
                    plan.get("planned_file_count"),
                    plan.get("budget_steps"),
                    plan.get("budget_output_tokens"),
                    plan.get("actual_steps"),
                    plan.get("actual_file_count"),
                    plan.get("outcome_status"),
                    plan.get("entry_file"),
                    names[:60],
                ]
            )
        _print_table(
            ["need", "declared", "final", "files_plan", "budget_steps", "budget_out",
             "steps_act", "files_act", "outcome", "entry", "planned_names"],
            recon_rows,
        )


def _find_artifact_dir(task_uuid: str) -> Path | None:
    """按 task_uuid 在产物根目录下定位落盘目录（``generated/{user_id}/{task_uuid}``）。

    刻意用 glob 而不是拼 ``user_id``：重判历史报告时 report 里未必记着 user_id，
    而磁盘结构本身就是唯一真源。
    """
    matches = list(storage_settings.generated_path.glob(f"*/{task_uuid}"))
    return matches[0] if matches else None


def _recheck(report_path: Path) -> int:
    """用**当前判据**重判一份历史报告（不调模型、不发请求）。

    存在意义：判据本身也可能写错（第一版把 JS 模板串当成缺失文件）。
    若判据修正后只能重跑一遍模型才能得到正确结论，那么"修判据"就等于再烧一次钱 ——
    于是大家会倾向于将错就错。产物还在磁盘上，所以正确做法是**离线重判**。

    Args:
        report_path: 之前 ``check_compare`` 落盘的报告 JSON。

    Returns:
        退出码（0=重判完成）。
    """
    report = json.loads(report_path.read_text(encoding="utf-8"))
    needs_by_id = {need.need_id: need for need in NEEDS}
    rows: list[RunRow] = []
    for raw in report["rows"]:
        row = RunRow(**raw)
        directory = _find_artifact_dir(row.task_uuid) if row.task_uuid else None
        if directory is None:
            row.integrity_ok = False
            row.integrity_failures = ["产物目录不存在，无法重判"]
        else:
            file_sizes = {}
            for name in row.file_list:
                path = directory / name
                file_sizes[name] = path.stat().st_size if path.is_file() else -1
            row.file_sizes = file_sizes
            row.integrity_ok, row.integrity_failures, row.missing_refs = _check_integrity(
                directory, row.file_list, file_sizes
            )
            if row.preview_status is not None and row.preview_status != 200:
                row.integrity_failures.append(f"预览不可用（HTTP {row.preview_status}）")
                row.integrity_ok = False
        need = needs_by_id.get(row.need_id)
        row.scope_ok = bool(need) and len(row.file_list) >= need.expected_min_files
        rows.append(row)

    summary = _aggregate(rows)
    print(f"重判报告：{report_path.name}（判据：只扫标记语言，忽略 script/style/注释/模板串）")
    _print_report(rows, summary)

    out_path = report_path.with_name(f"{report_path.stem}_recheck.json")
    out_path.write_text(
        json.dumps(
            {"meta": {**report.get("meta", {}), "rechecked_from": report_path.name},
             "rows": [asdict(row) for row in rows], "summary": summary},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n重判结果已落盘：{out_path}")
    return 0


# ==================== 入口 ====================


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="阶段 7：single / multi / agent 对照实验")
    parser.add_argument(
        "--needs",
        default="all",
        help="需求集：all 或逗号分隔的 id（如 r1,r3）",
    )
    parser.add_argument(
        "--modes",
        default=",".join(MODES),
        help=f"模式集：逗号分隔（可选 {','.join(MODES)}）",
    )
    parser.add_argument("--runs", type=int, default=1, help="每个组合重复几次（默认 1）")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_POLL_DEADLINE, help="单次轮询上限（秒）"
    )
    parser.add_argument("--out-dir", default="docs/experiments", help="报告落盘目录")
    parser.add_argument("--dry-run", action="store_true", help="只列出将执行的组合，不调用模型")
    parser.add_argument(
        "--recheck",
        default=None,
        help="用当前判据离线重判一份历史报告（不调模型），传报告 JSON 路径",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """跑完整场对照实验，返回退出码（0=所有组合都拿到终态）。"""
    args = _parse_args(argv)

    if args.recheck:
        path = Path(args.recheck)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.is_file():
            print(f"参数有误：找不到报告文件 {path}")
            return 2
        return _recheck(path)

    needs = (
        list(NEEDS)
        if args.needs == "all"
        else [need for need in NEEDS if need.need_id in {s.strip() for s in args.needs.split(",")}]
    )
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown or not needs or not modes:
        print(f"参数有误：需求 {[n.need_id for n in needs]}｜模式 {modes}｜未知模式 {unknown}")
        return 2

    combos = [(need, mode, attempt) for need in needs for mode in modes
              for attempt in range(1, args.runs + 1)]
    print("=" * 100)
    print(f"对照实验：{len(needs)} 个需求 × {len(modes)} 种模式 × {args.runs} 次 = {len(combos)} 次生成")
    print(f"需求：{'、'.join(f'{n.need_id}({n.title})' for n in needs)}")
    print(f"模式：{'、'.join(modes)}")
    print("[!!] 每次生成都会真实调用大模型并按 token 计费")
    if args.dry_run:
        for need, mode, attempt in combos:
            print(f"  [dry-run] {need.need_id} / {mode} / #{attempt}")
        print("dry-run 结束：没有调用模型。去掉 --dry-run 即开跑。")
        return 0

    rows: list[RunRow] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        try:
            token, _ = _register_and_login(client)
        except httpx.HTTPError as error:
            print(f"失败：API 不可用或注册/登录异常 -> {error}")
            print("处理：确认已执行 `uv run fastapi dev`")
            return 2
        headers = {"Authorization": f"Bearer {token}"}

        for index, (need, mode, attempt) in enumerate(combos, start=1):
            print(f"\n[{index}/{len(combos)}] {need.need_id} / {mode} / #{attempt} -> 提交…")
            row = _run_once(
                client, headers, need, mode, attempt, deadline=args.timeout,
            )
            rows.append(row)
            print(
                f"    status={row.status} stage={row.stage} files={row.file_list} "
                f"in={row.input_tokens} out={row.output_tokens} "
                f"reason={row.reasoning_tokens} e2e={row.e2e_seconds}s "
                f"integ={'Y' if row.integrity_ok else 'N'}"
            )
            if row.integrity_failures:
                for failure in row.integrity_failures:
                    print(f"      - {failure}")
            if row.error_msg and row.status != "success":
                print(f"      - error: {str(row.error_msg)[:200]}")

    summary = _aggregate(rows)
    _print_report(rows, summary)

    out_dir = BASE_DIR / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"stage7_compare_{stamp}.json"
    report_path.write_text(
        json.dumps(
            {
                "meta": {
                    "created_at": stamp,
                    "runs_per_combo": args.runs,
                    "needs": [asdict(need) for need in needs],
                    "modes": modes,
                    "note": "按出厂配置对照，不人为拉平思考/非思考客户端差异",
                },
                "rows": [asdict(row) for row in rows],
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n报告已落盘：{report_path}")

    unfinished = [row for row in rows if row.status == "poll_timeout"]
    if unfinished:
        print(f"警告：{len(unfinished)} 个组合未在期限内拿到终态，结论可能不完整")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
