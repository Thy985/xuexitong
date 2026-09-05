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

    if state.status == "BLOCKED" and trigger != "manual":
        return "BLOCKED", f"Course {active_key} is BLOCKED"

    if state.status == "ARCHIVED":
        return "NOOP", f"Course {active_key} is ARCHIVED"

    # 检查连续失败阈值（manual trigger 可 override）
    ss = load_scheduler_state(active_key)
    if ss.consecutive_failures >= 3 and trigger != "manual":
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
    next_chapter = _run_tdvp_probe(course_url, identity_key, run_id=run_id)
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

    # 打印子进程输出（便于 CI 诊断执行失败原因）
    if proc.stdout:
        print(proc.stdout[-2000:], flush=True)
    if proc.stderr:
        print(f"[stderr] {proc.stderr[-2000:]}", file=sys.stderr, flush=True)

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
            # 无视频节点（过渡页/无 cards）→ 自动跳过，标记 COMPLETED，
            # 避免 done_ids 修复后对这些不可学习节点无限重试。
            if not passed:
                ev = r.get("evidence", {}) or {}
                failure_stage = str(ev.get("failure_stage", "") or "")
                v_str = str(ev.get("verdict", "") or "").lower()
                no_video = (
                    failure_stage in ("NO_CARDS_IFRAME", "NO_VIDEO_IN_CARDS")
                    or any(s in v_str for s in (
                        "video metadata not ready", "no_cards_frame",
                        "no_video_in_cards", "cards iframe",
                    ))
                )
                if no_video:
                    try:
                        from e6.task_registry import load_registry, save_registry
                        reg = load_registry(identity_key)
                        changed = False
                        for t in reg.values():
                            if t.chapter_id == chapter_id:
                                t.status = "COMPLETED"
                                changed = True
                        if changed:
                            save_registry(identity_key, reg)
                            print(f"[scheduler] {chapter_id}: no-video node -> "
                                  f"marked COMPLETED (skip)", flush=True)
                    except Exception:
                        pass
                    passed = True
                    verdict = "SKIP(no video)"
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


def _run_tdvp_probe(course_url: str, course_key: str,
                    run_id: str = "local") -> Optional[str]:
    """E6 TDVP Probe: Discovery -> Sync E6 Registry -> Reconcile Queue -> pick next task.

    Returns:
        chapter_id string, or None if all chapters done.
    """
    try:
        from tvdp.tdvp import (
            fetch_course_discovery, build_tasks_from_discovery,
        )
        from e6.task_registry import load_registry, save_registry, reconcile_queue
        from e6.click_probe import click_probe_chapter_id
        from resolvers.course_resolver import _parse_url_params
        from state.course_state import load_course_state

        params = _parse_url_params(course_url)

        # 1. DOM 提取目录树
        chapters_raw = fetch_course_discovery(course_url)
        if not chapters_raw:
            print("[scheduler] TDVP: fetch returned empty, "
                  "falling back to URL chapter_id", file=sys.stderr)
            return params.get("chapter_id")

        # 2. Preflight Reconciliation: 校准历史状态
        # 【架构变更 v2】不能把所有 COMPLETED(NONE) 一律降级——DOM 的 completed 标记
        # （posCatalog_finish class / "已完成"文字）是服务器端渲染的真实完成状态，
        # 只有 nextunit URL 跳变导致的假完成才需要降级重跑。
        # 校准依据 = 服务器端 DOM 渲染的章节状态（本次 discovery 实时提取）：
        #   - DOM status == completed → 服务器端确认完成 → 保留 COMPLETED，
        #     证据升级为 UI（服务器 DOM 渲染标记）
        #   - DOM status != completed（pending/unknown）→ 服务器端未确认 →
        #     降级为 DISCOVERED（需要重新播放验证）
        dom_status = {}
        for ch in (chapters_raw or []):
            cid = str(ch.get("chapter_id") or "")
            if cid:
                dom_status.setdefault(cid, ch.get("status", "unknown"))
        reconciliation_count = 0
        retain_count = 0
        try:
            from e6.task_registry import TaskRecord, Verification
            reg_check = load_registry(course_key)
            for tid, t in reg_check.items():
                if t.status == "COMPLETED" and t.verification.level == "NONE":
                    srv = dom_status.get(t.chapter_id, "unknown")
                    if srv == "completed":
                        # 服务器端 DOM 渲染标记该章节已完成 → 保留，证据升级为 UI
                        t.verification = Verification(
                            level="UI",
                            verified_at_utc=datetime.now(timezone.utc).isoformat(),
                            run_id=run_id,
                            source_detail="server DOM completed marker",
                        )
                        t.updated_at_utc = datetime.now(timezone.utc).isoformat()
                        retain_count += 1
                        print(f"[scheduler] Reconcile: {tid} ({t.title}) "
                              f"COMPLETED(NONE) → COMPLETED(UI) [server DOM completed]",
                              flush=True)
                    else:
                        # 服务器端未确认完成 → 降级 DISCOVERED
                        reg_check[tid] = TaskRecord(
                            task_id=t.task_id,
                            chapter_id=t.chapter_id,
                            title=t.title,
                            task_type=t.task_type,
                            status="DISCOVERED",
                            priority=t.priority,
                            _ch_idx=t._ch_idx,
                            _cell_idx=t._cell_idx,
                        )
                        reconciliation_count += 1
                        print(f"[scheduler] Reconcile: {tid} ({t.title}) "
                              f"COMPLETED(NONE) → DISCOVERED "
                              f"[server DOM='{srv}']", flush=True)
            if reconciliation_count > 0 or retain_count > 0:
                save_registry(course_key, reg_check)
                print(f"[scheduler] Reconciled {reconciliation_count} downgraded, "
                      f"{retain_count} retained (server DOM completed)", flush=True)
        except Exception as e:
            print(f"[scheduler] Reconciliation failed (non-fatal): {e}",
                  file=sys.stderr, flush=True)

        # 3. 构建/合并 E6 Task Registry
        tasks = build_tasks_from_discovery(chapters_raw)
        existing = load_registry(course_key)
        new_tids = {t.task_id for t in tasks}

        def _dom_is_completed(cid: str) -> bool:
            """服务器端 DOM 渲染标记该章节已完成？（仅接受显式 completed）"""
            return bool(cid) and dom_status.get(cid) == "completed"

        # 清理旧 registry 中 title 匹配但 task_id 格式已变的条目（格式迁移）
        for old_tid, old_rec in list(existing.items()):
            if old_tid not in new_tids:
                matched = next((t for t in tasks if t.title == old_rec.title), None)
                if matched:
                    # 【架构变更 v2】迁移时：SERVER_VERIFIED 或服务器端 DOM completed →
                    # 保留 COMPLETED，其余降级为 DISCOVERED。
                    from e6.task_registry import TaskRecord
                    srv_done = (old_rec.verification.level == "SERVER_VERIFIED"
                                or _dom_is_completed(matched.chapter_id or ""))
                    init_status = "COMPLETED" if srv_done else "DISCOVERED"
                    tr = TaskRecord(
                        task_id=matched.task_id,
                        chapter_id=matched.chapter_id or "",
                        title=matched.title,
                        task_type="video",
                        status=init_status,
                        priority=len(existing),
                        _ch_idx=matched._ch_idx,
                        _cell_idx=matched._cell_idx,
                    )
                    existing[matched.task_id] = tr
                    del existing[old_tid]
        for t in tasks:
            tid = t.task_id
            if tid not in existing:
                # 【架构变更 v2】新发现任务：若服务器端 DOM 标记 completed 则保留
                # COMPLETED(UI)（这是服务器渲染的真实状态）；否则 DISCOVERED。
                # 绝不由 nextunit URL 跳变推断完成。
                from e6.task_registry import TaskRecord, Verification
                dom_done = _dom_is_completed(t.chapter_id or "")
                if dom_done:
                    tr = TaskRecord(
                        task_id=tid,
                        chapter_id=t.chapter_id or "",
                        title=t.title,
                        task_type="video",
                        status="COMPLETED",
                        priority=len(existing),
                        _ch_idx=getattr(t, '_ch_idx', 0),
                        _cell_idx=getattr(t, '_cell_idx', 0),
                        verification=Verification(
                            level="UI",
                            verified_at_utc=datetime.now(timezone.utc).isoformat(),
                            run_id=run_id,
                            source_detail="server DOM completed marker",
                        ),
                    )
                    print(f"[scheduler] TDVP: new task {tid} ({t.title}) "
                          f"COMPLETED(UI) [server DOM completed]", flush=True)
                else:
                    tr = TaskRecord(
                        task_id=tid,
                        chapter_id=t.chapter_id or "",
                        title=t.title,
                        task_type="video",
                        status="DISCOVERED",
                        priority=len(existing),
                        _ch_idx=getattr(t, '_ch_idx', 0),
                        _cell_idx=getattr(t, '_cell_idx', 0),
                    )
                existing[tid] = tr
            else:
                existing[tid].title = t.title
                existing[tid]._ch_idx = getattr(t, '_ch_idx', existing[tid]._ch_idx)
                existing[tid]._cell_idx = getattr(t, '_cell_idx', existing[tid]._cell_idx)
                # 【架构变更 v2】不直接用 nextunit/DOM 推断覆盖强证据。
                # 已有 SERVER_VERIFIED → 保持 COMPLETED；
                # 服务器端 DOM completed → 保持 COMPLETED（升级为 UI 证据）；
                # 服务器端未确认 → 标记为 DISCOVERED（需要重新验证）。
                if existing[tid].verification.level == "SERVER_VERIFIED":
                    continue
                if _dom_is_completed(t.chapter_id or ""):
                    if existing[tid].status != "COMPLETED":
                        existing[tid].status = "COMPLETED"
                        existing[tid].verification = Verification(
                            level="UI",
                            verified_at_utc=datetime.now(timezone.utc).isoformat(),
                            run_id=run_id,
                            source_detail="server DOM completed marker",
                        )
                        existing[tid].updated_at_utc = datetime.now(timezone.utc).isoformat()
                else:
                    existing[tid].status = "DISCOVERED"
        save_registry(course_key, existing)
        print(f"[scheduler] TDVP: registry has {len(existing)} tasks", flush=True)
        for t in list(existing.values())[:8]:
            print(f"    - [{t.status}] cid={t.chapter_id or '?'} {t.title}", flush=True)

        # 3. 读取已完成的 chapter_id 集合
        #    【架构变更 v2】done_ids 来源于 Task Registry 中 status=COMPLETED 且有
        #    证据的任务（SERVER_VERIFIED 或服务器端 DOM completed 的 UI 证据）。
        #    绝不由 nextunit URL 跳变推断完成；history.passed 仅用于诊断统计。
        done_ids = set()
        done_sv = set()
        try:
            from e6.task_registry import load_registry
            reg = load_registry(course_key)
            for t in reg.values():
                if t.chapter_id and t.status == "COMPLETED":
                    if t.verification.level == "SERVER_VERIFIED":
                        done_ids.add(t.chapter_id)
                        done_sv.add(t.chapter_id)
                    elif t.verification.level == "UI":
                        # 服务器端 DOM 渲染 completed 标记 → 视为完成
                        done_ids.add(t.chapter_id)
        except Exception:
            pass
        print(f"[scheduler] TDVP: done={len(done_ids)} chapters "
              f"(SERVER_VERIFIED={len(done_sv)}, UI-serverDOM={len(done_ids)-len(done_sv)}): "
              f"{sorted(done_ids)}", flush=True)

        # 4. Reconcile Queue
        queue = reconcile_queue(course_key, existing, done_ids)
        print(f"[scheduler] TDVP: queue has {len(queue.items)} READY tasks", flush=True)

        # 5. 选下一个任务
        if not queue.items:
            print("[scheduler] TDVP: queue empty - all chapters done", flush=True)
            return None

        next_item = queue.items[0]
        next_tid = next_item.get("task_id", "")
        next_rec = existing.get(next_tid)
        if not next_rec:
            print(f"[scheduler] TDVP: next task {next_tid} not in registry", flush=True)
            return None

        # 有 chapter_id -> 直接返回
        if next_rec.chapter_id:
            print(f"[scheduler] TDVP: next_chapter={next_rec.chapter_id} "
                  f"({next_rec.title})", flush=True)
            return next_rec.chapter_id

        # 无 chapter_id -> click_probe 获取
        # 循环尝试无 cid 节点，跳过已在 done_ids 里的
        max_probe_attempts = 10
        for probe_i in range(max_probe_attempts):
            ch_idx = next_rec._ch_idx
            cell_idx = next_rec._cell_idx
            print(f"[scheduler] TDVP: no cid, click-probe ci={ch_idx} si={cell_idx} "
                  f"({next_rec.title})", flush=True)
            resolved_cid = click_probe_chapter_id(course_url, ch_idx, cell_idx)
            if resolved_cid:
                next_rec.chapter_id = resolved_cid
                save_registry(course_key, existing)
                # 重新 reconcile，找下一个有 cid 的任务
                queue2 = reconcile_queue(course_key, existing, done_ids)
                if queue2.items:
                    rec2 = existing.get(queue2.items[0]["task_id"])
                    if rec2 and rec2.chapter_id and rec2.chapter_id not in done_ids:
                        print(f"[scheduler] TDVP: click-probe resolved -> {rec2.chapter_id}",
                              flush=True)
                        return rec2.chapter_id
                    elif rec2 and rec2.chapter_id and rec2.chapter_id in done_ids:
                        # 这个 cid 也完成了，继续尝试下一个无 cid 节点
                        next_tid = queue2.items[0]["task_id"]
                        next_rec = existing.get(next_tid)
                        if not next_rec or next_rec.chapter_id:
                            break  # 没有更多无 cid 节点了
                        continue
                    else:
                        # 下一个任务仍无 cid，继续循环
                        next_tid = queue2.items[0]["task_id"]
                        next_rec = existing.get(next_tid)
                        if not next_rec or next_rec.chapter_id:
                            break
                        continue
            else:
                print("[scheduler] TDVP: click-probe failed, trying fallback", flush=True)
                break

        # fallback: 取队列第二个任务
        queue3 = reconcile_queue(course_key, existing, done_ids)
        if len(queue3.items) > 1:
            second = queue3.items[1]
            rec2 = existing.get(second["task_id"])
            if rec2 and rec2.chapter_id:
                print(f"[scheduler] TDVP: fallback to 2nd task: {rec2.chapter_id}", flush=True)
                return rec2.chapter_id

        print("[scheduler] TDVP: no executable task with chapter_id found", flush=True)
        return None

    except Exception as e:
        print(f"[scheduler] TDVP probe failed (non-fatal): {e}", file=sys.stderr)
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
