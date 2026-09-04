"""Scheduler Module for E6

Decides WHEN to run based on persistent course state.
Uses existing E5 runtime via app/run.py.

Execution result types:
    RUN     - There is work to do, invoke runtime
    NOOP    - No work, not an error
    BLOCKED - Cannot execute, needs manual intervention
    ERROR   - Runtime/infrastructure error

Concurrency:
    Uses GitHub Actions concurrency group per active course.
   同一 active course 同一时间只能有一个执行实例。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

# 类型定义
SchedulerResult = Literal["SUCCESS", "NOOP", "BLOCKED", "FAILED"]
SchedulerDecision = Literal["RUN", "NOOP", "BLOCKED", "ERROR"]
TriggerType = Literal["manual", "schedule"]


@dataclass
class SchedulerState:
    """Scheduler 运行状态（写入 course state 的 scheduler 字段）。"""
    last_scheduled_at: Optional[str] = None      # ISO UTC
    last_started_at: Optional[str] = None        # ISO UTC
    last_finished_at: Optional[str] = None       # ISO UTC
    last_result: Optional[SchedulerResult] = None
    last_run_id: Optional[str] = None            # GitHub run ID
    last_trigger: Optional[TriggerType] = None
    consecutive_failures: int = 0
    execution_id: Optional[str] = None           # 本次执行唯一 ID
    attempt: int = 0                             # 当前尝试次数

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SchedulerState":
        return cls(**{k: v for k, v in d.items()
                     if k in cls.__dataclass_fields__})


@dataclass
class ExecutionResult:
    """单次执行的完整结果。"""
    decision: SchedulerDecision
    result: SchedulerResult
    trigger: TriggerType
    course_key: str
    run_id: str
    timing_s: float
    passed: bool
    verdict: str
    failure_stage: Optional[str] = None
    error: Optional[str] = None
    timestamp_utc: str = ""

    def __post_init__(self):
        if not self.timestamp_utc:
            self.timestamp_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def _state_file(course_key: str) -> Path:
    """获取课程状态文件路径。"""
    from state.course_state import COURSES_DIR
    return COURSES_DIR / f"{course_key}.json"


def load_scheduler_state(course_key: str) -> Optional[SchedulerState]:
    """加载课程的 scheduler 状态。"""
    try:
        from state.course_state import load_course_state
        state = load_course_state(course_key)
        if state and hasattr(state, 'scheduler') and state.scheduler:
            return SchedulerState.from_dict(state.scheduler)
    except Exception:
        pass
    return SchedulerState()


def save_scheduler_state(course_key: str, ss: SchedulerState) -> None:
    """保存 scheduler 状态到 course state。"""
    try:
        from state.course_state import load_course_state, save_course_state
        state = load_course_state(course_key)
        if not state:
            return
        # 将 scheduler 字段合并到 state
        sd = ss.to_dict()
        if not hasattr(state, 'scheduler'):
            state.scheduler = {}
        state.scheduler.update(sd)
        save_course_state(state)
    except Exception as e:
        print(f"[scheduler] Error saving state: {e}", file=sys.stderr)


def determine_action(
    active_key: Optional[str],
    trigger: TriggerType,
) -> tuple[SchedulerDecision, str]:
    """决定本次是否执行。

    Returns:
        (decision, reason)
    """
    if not active_key:
        return "NOOP", "No active course configured"

    from state.course_state import load_course_state
    state = load_course_state(active_key)
    if not state:
        return "NOOP", f"No state for active course {active_key}"

    if state.status == "BLOCKED":
        return "BLOCKED", f"Course {active_key} is BLOCKED"

    if state.status == "ARCHIVED":
        return "NOOP", f"Course {active_key} is ARCHIVED"

    # 检查连续失败阈值
    ss = load_scheduler_state(active_key)
    if ss.consecutive_failures >= 3:
        return "BLOCKED", (f"Course {active_key} has {ss.consecutive_failures} "
                          "consecutive failures")

    return "RUN", f"Active course {active_key} ready to run"


def record_result(
    course_key: str,
    result: ExecutionResult,
) -> None:
    """记录执行结果并更新 scheduler 状态。"""
    ss = load_scheduler_state(course_key)

    ss.last_scheduled_at = result.timestamp_utc
    ss.last_started_at = result.timestamp_utc
    ss.last_finished_at = datetime.now(timezone.utc).isoformat()
    ss.last_result = result.result
    ss.last_run_id = result.run_id
    ss.last_trigger = result.trigger
    ss.execution_id = result.run_id
    ss.attempt += 1

    # 更新连续失败计数
    if result.result == "FAILED":
        ss.consecutive_failures += 1
    else:
        ss.consecutive_failures = 0

    save_scheduler_state(course_key, ss)


def get_scheduler_summary(
    active_key: Optional[str],
    decision: SchedulerDecision,
    reason: str,
) -> dict:
    """生成 Actions Summary 用的摘要。"""
    summary = {
        "trigger": "scheduled" if "schedule" in reason.lower() else "manual",
        "decision": decision,
        "reason": reason,
    }
    if active_key:
        from state.course_state import load_course_state
        state = load_course_state(active_key)
        if state and state.course_identity:
            summary["course"] = state.course_identity.title
            summary["identity"] = active_key
            summary["status"] = state.status
            ss = load_scheduler_state(active_key)
            summary["last_result"] = ss.last_result
            summary["consecutive_failures"] = ss.consecutive_failures
            summary["last_run_id"] = ss.last_run_id
    return summary


def generate_actions_summary(summary: dict) -> str:
    """生成 GitHub Actions Summary Markdown。"""
    lines = ["## Xuexitong Scheduler", ""]
    lines.append(f"**Trigger**: {summary.get('trigger', '?')}")
    lines.append(f"**Course**: {summary.get('course', 'N/A')}")
    lines.append(f"**Identity**: `{summary.get('identity', 'N/A')}`")
    lines.append(f"**Status**: {summary.get('status', '?')}")
    lines.append(f"**Decision**: `{summary.get('decision', '?')}`")
    lines.append(f"**Reason**: {summary.get('reason', '')}")
    lines.append("")

    if summary.get('last_result'):
        lines.append(f"**Previous Run**: {summary['last_result']}")
    if summary.get('last_run_id'):
        lines.append(f"**Last Run ID**: [{summary['last_run_id']}]("
                     f"https://github.com/{summary.get('repo', '')}/actions/runs/{summary['last_run_id']})")
    if summary.get('consecutive_failures') is not None:
        cf = summary['consecutive_failures']
        lines.append(f"**Consecutive Failures**: {cf}"
                     f"({'⚠️ BLOCKED threshold reached' if cf >= 3 else ''})")
    lines.append("")

    if summary.get('decision') == 'BLOCKED':
        lines.append("> ⚠️ Scheduler blocked. Manual intervention required.")
        lines.append("> Check course state and fix underlying issues.")
    elif summary.get('decision') == 'NOOP':
        lines.append("> ℹ️ No action taken. Check if course is initialized.")

    return "\n".join(lines)


# 便捷函数供 run.py 调用
def run_scheduler(course_url: Optional[str] = None, chapter_id: str = "",
                  trigger: TriggerType = "manual", run_id: str = "local") -> ExecutionResult:
    """Scheduler 入口：从 state/active_course.json 读取课程，内置 TDVP 探测。

    - 手动触发（workflow_dispatch）：可选传 course_url，用于切换课程
    - 定时触发（schedule）：course_url 为 None，完全从 state 读取
    - TDVP Passive Probe 在后台静默执行，不暴露给用户
    """
    from resolvers.course_resolver import resolve_course, detect_course_change
    from state.course_state import load_active_course, load_course_state, activate_course

    # ── Step 1: 确定课程 identity ────────────────────────────────
    active = load_active_course()

    if course_url:
        # 手动触发：解析传入的 URL
        result = resolve_course(course_url)
        if not result.is_ok():
            return ExecutionResult(
                decision="ERROR", result="FAILED", trigger=trigger,
                course_key="", run_id=run_id, timing_s=0,
                passed=False, verdict=f"Resolve failed: {result.error}",
                error=result.error,
            )
        identity_key = result.identity.key()

        # 检测是否需要切换
        if active and active.key() != identity_key:
            det = detect_course_change(course_url, active)
            if det.kind in ("COURSE_CHANGED", "NEW_COURSE"):
                from resolvers.course_resolver import CourseIdentity as SCI
                new_id = SCI(
                    course_id=result.identity.course_id,
                    clazz_id=result.identity.clazz_id,
                    cpi=result.identity.cpi,
                    title=result.identity.title,
                    raw_url=course_url,
                    resolved_at_utc=result.identity.resolved_at_utc,
                )
                activate_course(new_id)
                # 同步 TDVP 状态
                sync_tdvp_on_switch(new_id, course_url)
    else:
        # 定时触发：从 state 读取
        if not active:
            return ExecutionResult(
                decision="NOOP", result="NOOP", trigger=trigger,
                course_key="", run_id=run_id, timing_s=0,
                passed=False, verdict="No active course configured (run initialize first)",
            )
        identity_key = active.key()
        course_url = active.raw_url  # 使用 state 中保存的 URL

    # ── Step 2: TDVP Passive Probe（后台静默执行）────────────────
    next_chapter = _run_tdvp_probe(course_url, identity_key)
    if next_chapter:
        chapter_id = next_chapter  # 用发现的 next_task 覆盖传入的 chapter_id

    # ── Step 3: 决定 action ─────────────────────────────────────
    decision, reason = determine_action(identity_key, trigger)

    if decision != "RUN":
        summary = get_scheduler_summary(identity_key, decision, reason)
        _write_summary(summary)
        return ExecutionResult(
            decision=decision, result="NOOP" if decision == "NOOP" else "BLOCKED",
            trigger=trigger, course_key=identity_key, run_id=run_id,
            timing_s=0, passed=False, verdict=reason,
        )

    # 执行 run
    import subprocess
    import time
    t0 = time.time()
    cmd = [
        sys.executable, "app/run.py",
        "--action", "run",
        "--course-url", course_url,
        "--chapter-id", chapter_id,
        "--output", "./evidence/result.json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
    timing = time.time() - t0

    passed = proc.returncode == 0
    verdict = "PASS" if passed else "FAIL"

    # 解析实际 verdict
    try:
        import json as json_mod
        if Path("./evidence/result.json").exists():
            with open("./evidence/result.json") as f:
                r = json_mod.load(f)
            res = r.get("result", {})
            verdict = res.get("verdict", verdict)
            passed = res.get("exit_code", 1) == 0
    except Exception:
        pass

    exec_result = ExecutionResult(
        decision="RUN",
        result="SUCCESS" if passed else "FAILED",
        trigger=trigger,
        course_key=identity_key,
        run_id=run_id,
        timing_s=round(timing, 1),
        passed=passed,
        verdict=verdict,
    )

    # 记录结果
    record_result(identity_key, exec_result)

    # 生成 summary
    summary = get_scheduler_summary(identity_key, "RUN", "Executing course task")
    summary["result"] = exec_result.result
    summary["timing_s"] = exec_result.timing_s
    summary["verdict"] = exec_result.verdict
    _write_summary(summary)

    return exec_result


def _write_summary(summary: dict) -> None:
    """将 summary 写入 Actions summary 文件。"""
    try:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary_path:
            md = generate_actions_summary(summary)
            Path(summary_path).write_text(md, encoding="utf-8")
    except Exception:
        pass


def _run_tdvp_probe(course_url: str, course_key: str) -> Optional[str]:
    """内置 TDVP Passive Probe：抓取 studentstudy 页面 HTML，解析任务列表。

    流程：
      1. 用 Playwright 登录后抓取页面 HTML（约 10-15s）
      2. 解析 HTML 获取所有任务点状态（已完成/待完成/未知）
      3. 更新 state/tdvp_tasks.json 任务注册表
      4. 同步进度到 state/courses/<key>.json
      5. 返回第一个 PENDING/UNKNOWN 任务的 chapter_id
    """
    try:
        from tvdp.tdvp import (
            load_task_registry, save_task_registry,
            get_pending_tasks, sync_progress_to_course_state,
            fetch_course_discovery, build_tasks_from_discovery,
        )
        from resolvers.course_resolver import _parse_url_params

        params = _parse_url_params(course_url)

        # ── 1. 在浏览器 DOM 中提取目录树章节及状态 ────────────────
        chapters_raw = fetch_course_discovery(course_url)
        if not chapters_raw:
            print("[scheduler] TDVP: fetch_course_discovery returned empty, "
                   "falling back to URL chapter_id", file=sys.stderr)
            return params.get("chapter_id")

        tasks = build_tasks_from_discovery(chapters_raw,
                                           fallback_chapter=params.get("chapter_id") or "")
        print(f"[scheduler] TDVP: DOM found {len(chapters_raw)} chapters, "
              f"{len(tasks)} tasks", flush=True)

        # ── 2. 更新 task registry ─────────────────────────────────
        registry = {t.task_id: t for t in tasks}
        save_task_registry(course_key, registry)

        # ── 3. 同步进度到 course state ────────────────────────────
        # 构造 CourseDiscovery 供 sync 使用
        from tvdp.tdvp import CourseDiscovery
        disc = CourseDiscovery(
            course_id=params.get("course_id", ""),
            clazz_id=params.get("clazz_id", ""),
            course_key=course_key,
            chapters=[_chapter_info_for_task(t) for t in tasks],
        )
        sync_result = sync_progress_to_course_state(course_key, disc)
        print(f"[scheduler] TDVP: synced — total={sync_result.get('total')}, "
              f"completed={sync_result.get('completed')}, "
              f"pending={sync_result.get('pending')}, "
              f"next_task={sync_result.get('next_task')}", flush=True)

        # ── 4. 返回第一个 pending/unknown 任务的 chapter_id ───────
        pending = get_pending_tasks(course_key, registry)
        if pending:
            # 按页面 DOM 顺序（非排序）取第一个未完成章节
            for t in tasks:
                if t.status in ("PENDING", "UNKNOWN"):
                    return t.chapter_id

        # 没有 pending → 全部完成
        print("[scheduler] TDVP: no pending tasks found — all done", flush=True)
        return None

    except Exception as e:
        print(f"[scheduler] TDVP probe failed (non-fatal): {e}", file=sys.stderr)
        # fallback：从 URL 取 chapter_id
        try:
            from resolvers.course_resolver import _parse_url_params
            params = _parse_url_params(course_url)
            return params.get("chapter_id")
        except Exception:
            return None


def sync_tdvp_on_switch(new_identity, course_url: str) -> None:
    """课程切换时同步 TDVP 状态。"""
    try:
        from tvdp.tdvp import save_task_registry
        # 清空旧任务的 registry（新课程从零开始）
        save_task_registry(new_identity.key(), {})
    except Exception:
        pass


# 导入 os/Path
import os
from pathlib import Path


def _chapter_info_for_task(t) -> "object":
    """为单个 TaskInfo 构造 ChapterInfo（用于 sync_progress_to_course_state）。"""
    from tvdp.tdvp import ChapterInfo
    return ChapterInfo(chapter_id=t.chapter_id, title=t.title, tasks=[t])
