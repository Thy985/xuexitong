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

# BLOCKED 熔断自动复位策略（cooldown / retry）。
# 语义：
#   - schedule 每轮调度机会计数 blocked_hits。
#   - 每累计到 blocked_retry_interval（连续被 BLOCKED 拒的调度次数）后，自动放行一次 probe/retry。
#   - manual 触发不受 cooldown 约束，永远允许立即 probe/retry。
# 可通过环境变量 XUE_BLOCKED_RETRY_INTERVAL 覆盖（默认 4），无需改代码即可调成 2/4/6...。
def _blocked_retry_interval() -> int:
    import os
    raw = os.environ.get("XUE_BLOCKED_RETRY_INTERVAL", "4")
    try:
        return max(1, int(raw))
    except Exception:
        return 4


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
    # ── BLOCKED 熔断 / cooldown 状态 ──────────────────────────────
    blocked_since: Optional[str] = None          # 进入 BLOCKED 的时间（ISO UTC）
    blocked_hits: int = 0                        # 进入 BLOCKED 后，被 schedule 拒的调度次数
    blocked_retry_interval: int = 0              # 0 = 使用默认/环境变量；>0 = 显式覆盖

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
    evidence: Optional[dict] = None      # 底层 runtime evidence（不能丢，见 E6.1 §11）
    chapters_attempted: list = field(default_factory=list)   # 本轮尝试的章节 id
    chapters_failed: list = field(default_factory=list)      # 本轮失败的章节 id（含 TIMEOUT）
    chapters_timed_out: list = field(default_factory=list)   # 本轮超时(watchdog)的章节 id

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
    """保存 scheduler 状态到 course state（per-course 锁内原子 RMW）。"""
    try:
        from state.course_state import update_course_state
        sd = ss.to_dict()

        def _merge(state):
            if state is None:
                return None
            if not hasattr(state, 'scheduler'):
                state.scheduler = {}
            state.scheduler.update(sd)
            return state

        update_course_state(course_key, _merge)
    except Exception as e:
        print(f"[scheduler] Error saving state: {e}", file=sys.stderr)


def _blocked_decision(active_key: str, ss: SchedulerState, trigger: TriggerType,
                      blocked_reason: str) -> tuple[SchedulerDecision, str]:
    """统一处置「课程处于 BLOCKED」的情况——显式决定何时允许解除 BLOCKED。

    规则（用户定案，不可用隐式门禁代替）：
        manual       = 人工主动干预，允许立即 probe/retry（return RUN）
        schedule     = 遵守 cooldown：每累计 blocked_retry_interval 次调度
                       机会，自动放行一次 retry（return RUN 并清零计数）；
                       否则只累计 blocked_hits 并 return BLOCKED。

    Args:
        active_key:      课程 identity key（用于持久化 cooldown 计数）
        ss:              当前 SchedulerState（会被改写并持久化）
        trigger:         manual / schedule
        blocked_reason:  进入 BLOCKED 的原因描述

    Returns:
        (decision, reason)
    """
    interval = int(ss.blocked_retry_interval or _blocked_retry_interval())
    interval = max(1, interval)

    # 记录首次进入 BLOCKED 的时间（用于诊断）。
    if not ss.blocked_since:
        ss.blocked_since = datetime.now(timezone.utc).isoformat()

    if trigger == "manual":
        # 人工主动干预：立即放行，无需等待 cooldown。
        # 不消费 blocked_hits（它只统计 schedule 的调度机会）。
        return "RUN", f"manual override: {blocked_reason}; allow immediate probe/retry"

    # schedule：遵守 cooldown
    ss.blocked_hits += 1
    if ss.blocked_hits >= interval:
        # 达到复位点：放行一次 probe/retry，并清零调度计数。
        ss.blocked_hits = 0
        save_scheduler_state(active_key, ss)
        return "RUN", (f"BLOCKED cooldown expired ({interval} schedules), "
                       "auto retry granted")

    save_scheduler_state(active_key, ss)
    return "BLOCKED", (f"{blocked_reason}; cooldown "
                       f"{ss.blocked_hits}/{interval} (schedule)")


def determine_action(
    active_key: Optional[str],
    trigger: TriggerType,
) -> tuple[SchedulerDecision, str]:
    """决定本次是否允许。

    显式区分「什么时候允许自动解除 BLOCKED」（见 _blocked_decision）：
      - manual  → 立即放行（人工干预）
      - schedule → 遵守 blocked_retry_interval 的 cooldown

    Returns:
        (decision, reason)
    """
    if not active_key:
        return "NOOP", "No active course configured"

    from state.course_state import load_course_state
    state = load_course_state(active_key)
    if not state:
        return "NOOP", f"No state for active course {active_key}"

    if state.status == "ARCHIVED":
        return "NOOP", f"Course {active_key} is ARCHIVED"

    # 读取 scheduler 熔断状态（在 BLOCKED 时会被改写并持久化）
    ss = load_scheduler_state(active_key)

    # 课程状态级 BLOCKED
    if state.status == "BLOCKED":
        return _blocked_decision(active_key, ss, trigger,
                                 f"Course {active_key} is BLOCKED")

    # 连续失败阈值级 BLOCKED（recent 连续失败 ≥ 3）
    if ss.consecutive_failures >= 3:
        return _blocked_decision(
            active_key, ss, trigger,
            f"Course {active_key} has {ss.consecutive_failures} consecutive failures")

    # 非阻塞状态：复位 BLOCKED 熔断计数（一旦恢复正常即清零）
    if ss.blocked_since or ss.blocked_hits:
        ss.blocked_since = None
        ss.blocked_hits = 0
        save_scheduler_state(active_key, ss)

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
        # 连续失败 ≥ 阈值 → 进入 BLOCKED 熔断（by _blocked_decision 下次 schedule 生效）
        if ss.consecutive_failures >= 3 and not ss.blocked_since:
            ss.blocked_since = result.timestamp_utc or \
                datetime.now(timezone.utc).isoformat()
            ss.blocked_hits = 0
    else:
        ss.consecutive_failures = 0
        # 恢复正常 → 清除 BLOCKED 熔断计数
        ss.blocked_since = None
        ss.blocked_hits = 0

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
        # GitHub Actions 仓库名（github.repository = "owner/repo"）；本地环境取不到时为 ""
        "repo": os.environ.get("GITHUB_REPOSITORY", "") or "",
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
    repo = (summary.get('repo') or '').strip()
    if summary.get('last_run_id'):
        if repo:
            lines.append(f"**Last Run ID**: [{summary['last_run_id']}]("
                         f"https://github.com/{repo}/actions/runs/{summary['last_run_id']})")
        else:
            lines.append(f"**Last Run ID**: `{summary['last_run_id']}`")
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
def _kill_proc_tree(pid: int) -> None:
    """尽力杀进程树（含 Playwright 派生的 Chromium/child）。

    调用方须用 `start_new_session=True` 启动子进程，使其成为独立进程组组长，
    这样 killpg(pid, ...) 能连同其所有子进程（browser/page）一并清理。
    GHA runner 是 Linux：先 SIGTERM（graceful），随后 SIGKILL 兜底。
    """
    if not pid:
        return
    import signal as _signal
    import time as _t
    try:
        os.killpg(pid, _signal.SIGTERM)
        _t.sleep(1)
        try:
            os.killpg(pid, _signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    except (AttributeError, OSError):
        # 无 killpg 或组已消亡 → 直接 SIGKILL 该 pid
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def _run_one_chapter(course_url: str, chapter_id: str, task_id: str = "",
                     trigger: TriggerType = "manual", run_id: str = "",
                     video_index: int = 0, max_s: int = 900) -> dict:
    """隔离执行单章学习（subprocess + wall-clock 超时），返回聚合结果 dict。

    设计动机（见事故 run 34311891898）：cmd_run → run_test 的 Playwright 播放
    循环在「nextUnit 切换但已记录 passed_object_id」等状态下可能**永不退出**。
    若在同一主线程内同步调用，外层 Python 循环即使有 budget 也拦不住一次
    cmd_run 内部的永久阻塞（browser 还活着）。因此：

      * 把每次 cmd_run 放进独立**子进程**（app.run --action run）。
      * 用 subprocess.wait(timeout=max_s) 提供**墙钟硬上限**。
      * 超时 → 杀进程树（不再可能泄漏 Chromium）→ 标记 TIMEOUT（区别于 FAIL）。

    返回:
      {
        "passed": bool,
        "verdict": str,            # PASS / FAIL / TIMEOUT
        "runtime_evidence": dict,
        "failure_stage": str | None,
        "exit_code": int,          # 0 成功；非 0 失败/崩溃；124=本 watchdog 超时
        "timing_s": float,
        "timed_out": bool,
      }
    """
    import time
    import json as json_mod
    import subprocess as sp

    evidence_path = f"./evidence/chapter_{task_id or chapter_id}.json"
    stdout_path = f"./evidence/chapter_{task_id or chapter_id}.scheduler.stdout.log"
    try:
        Path(stdout_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    cmd = [
        sys.executable, "-m", "app.run",
        "--action", "run",
        "--course-url", course_url,
        "--chapter-id", chapter_id,
        "--output", evidence_path,
        "--xvfb-display", os.environ.get("DISPLAY", ":99"),
        "--max-attempts", "2",
        "--video-index", str(int(video_index or 0)),
    ]
    t0 = time.time()
    exit_code = 1
    timed_out = False
    stdout_fh = open(stdout_path, "w", encoding="utf-8", errors="replace")
    try:
        try:
            proc = sp.Popen(cmd, stdout=stdout_fh, stderr=sp.STDOUT,
                        start_new_session=True)  # 独立进程组 → killpg 可连 browser 一并清理
            exit_code = proc.wait(timeout=max_s)
        except sp.TimeoutExpired:
            timed_out = True
            print(f"[scheduler] chapter {chapter_id} exceeded {max_s}s "
                  f"(watchdog), killing pid {proc.pid}", flush=True)
            _kill_proc_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            exit_code = 124  # watchdog 超时 exit（与 GNU timeout 一致）
    except Exception as e:
        print(f"[scheduler] chapter {chapter_id} subprocess error: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        exit_code = 1
    finally:
        try:
            stdout_fh.close()
        except Exception:
            pass
    timed = time.time() - t0

    passed = (not timed_out) and (exit_code == 0)
    verdict = "TIMEOUT" if timed_out else ("PASS" if passed else "FAIL")

    # 读取运行生成的 evidence / failure_stage
    runtime_evidence = None
    failure_stage = None
    try:
        if Path(evidence_path).exists():
            with open(evidence_path) as f:
                r = json_mod.load(f)
            res = r.get("result", {})
            if res.get("verdict", ""):
                verdict = res.get("verdict", verdict)
            passed = res.get("exit_code", 1) == 0 if not timed_out else False
            runtime_evidence = r.get("evidence") or {}
            failure_stage = runtime_evidence.get("failure_stage") \
                if isinstance(runtime_evidence, dict) else None
    except Exception:
        pass

    print(f"[scheduler] chapter {chapter_id}: verdict={verdict} "
          f"timing_s={round(timed,1)} exit_code={exit_code} "
          f"timed_out={timed_out}", flush=True)
    return {
        "passed": passed, "verdict": verdict,
        "runtime_evidence": runtime_evidence,
        "failure_stage": failure_stage, "exit_code": exit_code,
        "timing_s": timed, "timed_out": timed_out,
    }


def run_scheduler(course_url: Optional[str] = None, chapter_id: str = "",
                  trigger: TriggerType = "manual", run_id: str = "local",
                  max_chapters: int = 1) -> ExecutionResult:
    """Scheduler 入口：从 state/active_course.json 读取课程，内置 TDVP 探测 + 多章执行。

    - 手动触发（workflow_dispatch）：可选传 course_url，用于切换课程
    - 定时触发（schedule）：course_url 为 None，完全从 state 读取
    - TDVP Passive Probe 在后台静默执行，不暴露给用户
    - max_chapters：一次调度最多自动推进多个视频任务点（默认 1）
    """
    import time
    import os as _os

    # ── 多章参数收敛 ──────────────────────────────────────────────
    try:
        max_chapters = max(1, int(max_chapters))
    except (TypeError, ValueError):
        max_chapters = 1
    try:
        budget_s = int(_os.environ.get("XUE_SCHEDULER_BUDGET_S", "1500"))
    except Exception:
        budget_s = 1500
    try:
        failure_budget = max(1, int(_os.environ.get(
            "XUE_SCHEDULER_FAILURE_BUDGET", "1")))
    except Exception:
        failure_budget = 1
    # 单章执行器（cmd_run 子进程）的 wall-clock 硬上限；超时按 TIMEOUT 处理并杀进程树。
    try:
        chapter_max_s = max(60, int(_os.environ.get("XUE_CHAPTER_MAX_S", "900")))
    except Exception:
        chapter_max_s = 900

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

    # ── Step 2: 决定 action（含 BLOCKED cooldown/retry）──────────
    decision, reason = determine_action(identity_key, trigger)

    if decision != "RUN":
        summary = get_scheduler_summary(identity_key, decision, reason)
        _write_summary(summary)
        return ExecutionResult(
            decision=decision, result="NOOP" if decision == "NOOP" else "BLOCKED",
            trigger=trigger, course_key=identity_key, run_id=run_id,
            timing_s=0, passed=False, verdict=reason,
        )

    # ── Step 2.5: BLOCKED cooldown 复位点只给最小推进（1 章）────────
    if "cooldown expired" in reason:
        max_chapters = 1

    # ── Step 3: 多章循环执行 ─────────────────────────────────────
    t0 = time.time()
    chapters_attempted: list[str] = []
    chapters_failed: list[str] = []
    last_verdict = "NOOP"
    last_failure_stage = None
    runtime_evidence = None

    # 首次探测：无显式 chapter_id 时自动选下一个 pending 任务。
    next_task = chapter_id or _run_tdvp_probe(course_url, identity_key,
                                              run_id=run_id)
    if not next_task:
        # 队列为空 → 没有可执行任务
        summary = get_scheduler_summary(identity_key, "NOOP", "No pending task")
        _write_summary(summary)
        return ExecutionResult(
            decision="NOOP", result="NOOP", trigger=trigger,
            course_key=identity_key, run_id=run_id, timing_s=0,
            passed=False, verdict="No pending task to execute",
        )

    consecutive_fail_in_run = 0
    executed = 0
    chapters_timed_out: list = []
    while executed < max_chapters:
        if time.time() - t0 > budget_s:
            print(f"[scheduler] budget exceeded ({budget_s}s), "
                  f"stopping after {executed} chapters", flush=True)
            break
        if not next_task:
            break

        # Options B：把选中的 task（<cid> 或 <cid>:videoN）解析成 (chapter, video_index)
        run_cid, run_vidx = _split_video_target(next_task)
        chapters_attempted.append(next_task)
        one = _run_one_chapter(course_url, run_cid, task_id=next_task,
                               trigger=trigger, run_id=run_id,
                               video_index=run_vidx, max_s=chapter_max_s)
        passed = one["passed"]
        verdict = one["verdict"]
        rt_ev = one["runtime_evidence"]
        fail_stage = one["failure_stage"]
        timed_out = one["timed_out"]
        executed += 1
        last_verdict = verdict
        runtime_evidence = runtime_evidence or rt_ev
        if fail_stage:
            last_failure_stage = fail_stage
        if timed_out:
            chapters_timed_out.append(next_task)

        if passed:
            consecutive_fail_in_run = 0
        elif timed_out:
            # TIMEOUT：执行器未能可靠终止。记为失败（计入 chapters_failed，供
            # 聚合结果用），但不计入「连续失败熔断 budget」——避免单个 watchdog
            # 超时把整轮多章 run 熔断得"连下一章也不跑"，继续推进下一章。
            chapters_failed.append(next_task)
        else:
            chapters_failed.append(next_task)
            consecutive_fail_in_run += 1
            # 连续失败达到 budget → 熔断本轮，不再跑剩余章。
            if consecutive_fail_in_run >= failure_budget:
                print(f"[scheduler] {consecutive_fail_in_run} consecutive "
                      f"failure(s) this run (budget {failure_budget}); "
                      "stopping multi-chapter loop", flush=True)
                break

        # 跑完一段后重新探测下一任务（registry 已更新）。
        # 按 task_id 排除本轮已处理过的——允许同一章的下一个视频段被接着选中。
        if executed < max_chapters:
            next_task = _run_tdvp_probe(
                course_url, identity_key, run_id=run_id,
                exclude_chapters=set(chapters_attempted))
        else:
            next_task = None

    timing = time.time() - t0

    # 汇总（P0-06）：不得用「任一章成功」归一成全局 SUCCESS 掩盖真实失败。
    #   * real_failures：非 TIMEOUT 的真实失败章（TIMEOUT 由 watchdog 单独记账，见下）。
    #   * successful：本次真正推进成功的章（不在 chapters_failed 中）。
    # 规则：
    #   - 有真实失败 → 全局 FAILED（不归一 SUCCESS，也不让 record_result 清零失败 budget）
    #   - 无真实失败但没有任何成功章（全超时/全失败）→ FAILED
    #   - 否则（部分推进，可含零星 TIMEOUT）→ SUCCESS
    real_failures = [c for c in chapters_failed if c not in chapters_timed_out]
    successful = [c for c in chapters_attempted if c not in chapters_failed]
    if real_failures:
        agg_result = "FAILED"
        agg_passed = False
    elif not successful:
        agg_result = "FAILED"
        agg_passed = False
    else:
        agg_result = "SUCCESS"
        agg_passed = True

    exec_result = ExecutionResult(
        decision="RUN",
        result=agg_result,
        trigger=trigger,
        course_key=identity_key,
        run_id=run_id,
        timing_s=round(timing, 1),
        passed=agg_passed,
        verdict=last_verdict,
        failure_stage=last_failure_stage,
        evidence=runtime_evidence,
    )
    # 汇总补充字段（供 CI / 摘要）
    exec_result.chapters_attempted = chapters_attempted
    exec_result.chapters_failed = chapters_failed
    exec_result.chapters_timed_out = chapters_timed_out

    # 记录结果（aggregate 后 commit 一次）
    record_result(identity_key, exec_result)

    # 生成 summary
    summary = get_scheduler_summary(identity_key, "RUN", "Executing course task")
    summary["result"] = exec_result.result
    summary["timing_s"] = exec_result.timing_s
    summary["verdict"] = exec_result.verdict
    summary["chapters_attempted"] = chapters_attempted
    summary["chapters_failed"] = chapters_failed
    summary["chapters_timed_out"] = chapters_timed_out
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


def _split_video_target(task_or_cid) -> tuple[str, int]:
    """把 task_id / 章节 id 拆成 (chapter_id, video_index)。

    Options B 逐段视频 dispatch：task_id 形如 `<chapterId>`(=video1) 或
    `<chapterId>:video<N>`。返回视频段序号用于 app.run --video-index。
    非 video 后缀或非 string → index=1（按第一段处理）。
    """
    s = str(task_or_cid or "")
    if s.endswith(":video") or ":video" not in s:
        return s, 1
    cid, _, part = s.partition(":video")
    try:
        return cid, max(1, int(part))
    except (TypeError, ValueError):
        return s.split(":")[0], 1


def _apply_excluded(queue, exclude_chapters) -> list:
    """从 ExecutionQueue items 中剔除「本轮 run 已处理过」的章节，返回过滤后的 items 列表。

    exclude_chapters: set[str] 的本轮已处理章节 id。用于多章循环中避免
    「同一章在本轮内被重复 re-probe 重选」（如一门既含视频任务点、又含
    非视频任务点导致服务端 job_remaining>0 的章）。
    返回过滤后的 items；队列已空则返回空列表。
    """
    if not exclude_chapters:
        return list(queue.items or [])
    ex = {str(c) for c in (exclude_chapters or [])}
    out = []
    for it in (queue.items or []):
        tid = str(it.get("task_id") or "")
        cid = str(it.get("chapter_id") or "")
        drop = tid in ex
        if cid in ex:
            # 排除整章：仅对 base 视频（task_id 形如 <cid>，无 :videoN 后缀）生效，
            # 让同章的下一个视频段（<cid>:video2...）保留 → 逐段视频 dispatch。
            if ":" not in tid:
                drop = True
        if not drop:
            out.append(it)
    return out


def _fallback_chapter(course_url: str, course_key: str,
                      exclude_chapters=None) -> Optional[str]:
    """目录抓取为空/探测异常时的回退选章。

    优先：URL 里的 chapterId —— 但仅当它**未被本轮处理**（否则会无限重放同一章）。
    否则：回退到**已持久化的 registry 队列**，取第一个还没完成、也没被本轮
    处理的视频章（保证 catalog 抓空时仍能推进到真正的新章，而不是死磕当前章）。

    Args:
        exclude_chapters: 本轮已处理章节名集合（去重）。
    Returns:
        选中的 chapter_id；无可推进章节则 None。
    """
    ex = {str(c) for c in (exclude_chapters or [])}
    from resolvers.course_resolver import _parse_url_params
    params = _parse_url_params(course_url)
    url_cid = params.get("chapter_id")
    if url_cid and url_cid not in ex:
        return url_cid
    # URL 章已在本轮处理过（或取不到）→ 从持久化 registry/队列找下一个真正 pending 的章
    try:
        from e6.task_registry import (
            load_registry, done_chapter_ids_from_registry, reconcile_queue,
        )
        reg = load_registry(course_key)
        if reg:
            done = set(done_chapter_ids_from_registry(reg))
            q = reconcile_queue(course_key, reg, done, points_map={})
            for it in (q.items or []):
                cid = str(it.get("chapter_id") or "")
                if cid and cid not in ex and cid not in done:
                    rec = reg.get(it.get("task_id", ""))
                    if rec is not None and \
                            (getattr(rec, "task_type", "video") or "video") == "video" \
                            and rec.status in ("PENDING", "READY", "FAILED",
                                              "DISCOVERED", "UNKNOWN"):
                        return cid
    except Exception as e:
        print(f"[scheduler] TDVP: registry fallback failed: {e}", file=sys.stderr)
    return None


def _run_tdvp_probe(course_url: str, course_key: str,
                    run_id: str = "local",
                    exclude_chapters=None) -> Optional[str]:
    """E6.1 TDVP Probe: Discovery → Reconcile Registry → Reconcile Queue → pick next task.

    Args:
        exclude_chapters: 可选 set[str]，本轮 run 已处理过的章节 id，
            选择下一个任务时排除它们（多章 re-probe 去重）。

    Returns:
        chapter_id string, or None if all chapters done / all excluded.
    """
    if exclude_chapters:
        exclude_chapters = {str(c) for c in exclude_chapters}
    try:
        from tvdp.tdvp import fetch_course_discovery, build_tasks_from_discovery
        from tvdp.tdvp import live_verify_chapter
        from e6.task_registry import load_registry, save_registry
        from e6.reconcile import reconcile_registry
        from e6.task_registry import done_chapter_ids_from_registry, reconcile_queue
        from e6.click_probe import click_probe_chapter_id
        from resolvers.course_resolver import _parse_url_params

        params = _parse_url_params(course_url)

        # 洞3：先读现有 registry 预测一个「疑似队首章」。
        from e6.task_registry import load_chapter_points, merge_done_with_points
        _pts_map = load_chapter_points(course_key)
        _current_reg = load_registry(course_key)
        _pred_head = params.get("chapter_id")
        if _current_reg:
            try:
                _done0 = merge_done_with_points(
                    done_chapter_ids_from_registry(_current_reg), set(), _pts_map)
                _q0 = reconcile_queue(course_key, _current_reg, _done0,
                                      points_map=_pts_map)
                if _q0.items:
                    _c0 = _q0.items[0].get("chapter_id") or ""
                    if _c0:
                        _pred_head = _c0
            except Exception:
                pass

        combined = None
        from tvdp.tdvp import fetch_course_detail_and_verify
        combined = fetch_course_detail_and_verify(course_url, _pred_head)
        if combined is not None:
            chapters_raw = combined.get("chapters") or []
            _combined_points = combined.get("points") or []
        else:
            from tvdp.tdvp import fetch_course_discovery
            chapters_raw = fetch_course_discovery(course_url)
            _combined_points = []
        # 回退：目录拉不到 → 用 URL/持久化 registry 选一个未处理章（遵守 exclude）
        if not chapters_raw:
            print("[scheduler] TDVP: fetch returned empty, falling back "
                  f"(exclude={sorted(set(exclude_chapters or []))})", file=sys.stderr)
            return _fallback_chapter(course_url, course_key,
                                     exclude_chapters=exclude_chapters)

        # 1.5 服务器端 DOM 渲染状态 map
        dom_status = {}
        for ch in (chapters_raw or []):
            cid = str(ch.get("chapter_id") or "")
            if cid:
                dom_status.setdefault(cid, ch.get("status", "unknown"))

        # 2. 构建 discovery 任务 → Reconcile canonical registry
        tasks = build_tasks_from_discovery(chapters_raw)
        reg_before = load_registry(course_key)
        existing, report = reconcile_registry(course_key, reg_before, tasks, dom_status)
        save_registry(course_key, existing)
        print(f"[scheduler] TDVP: reconcile → {len(existing)} tasks "
              f"(upcoming={report.upcoming} kept={report.kept_completed} "
              f"downgraded={report.downgraded} upgraded_ui={report.upgraded_ui})",
              flush=True)

        # 2.5 E6.2：COMPLETED 但真实仍有未完成任务点的章，降级 STALE 重新入队。
        #     来源两路：
        #      (a) 目录层 job_remaining>0（L1，廉价）
        #      (b) 点级快照显示还有 video 点未 finish（洞2，已持久化的前一棵树）
        from e6.reconcile import (stale_completed_by_catalog,
                                  stale_completed_by_points)
        from e6.task_registry import load_chapter_points
        stale1 = stale_completed_by_catalog(existing, chapters_raw)
        stale2 = stale_completed_by_points(existing, load_chapter_points(course_key))
        stale_ids = list(dict.fromkeys(stale1 + stale2))
        if stale_ids:
            from e6.task_registry import TaskRecord
            for sid in stale_ids:
                rec = existing.get(sid)
                if rec is not None:
                    rec.mark_stale(detail="chapter has unfinished points")
            save_registry(course_key, existing)
            print(f"[scheduler] TDVP: stale={len(stale_ids)} "
                  f"chapters re-queued: {sorted({existing[s].chapter_id for s in stale_ids if s in existing})}",
                  flush=True)

        # 3. done_ids 仅为 derived cache（canonical 状态在 registry.completion）。
        #    洞2：用持久化的点级快照校准——凡有快照显示"还有 video 点未 finish"的章，
        #    即使 registry 把它记为 COMPLETED，也不放行（不会当 done 跳过）。
        from e6.task_registry import load_chapter_points, merge_done_with_points
        pts_map = load_chapter_points(course_key)
        done_ids = merge_done_with_points(
            done_chapter_ids_from_registry(existing), set(), pts_map)
        print(f"[scheduler] TDVP: done={len(done_ids)} chapters: {sorted(done_ids)}",
              flush=True)

        # 4. Reconcile Queue（派生物）
        queue = reconcile_queue(course_key, existing, done_ids, points_map=pts_map)
        print(f"[scheduler] TDVP: queue has {len(queue.items)} READY tasks", flush=True)

        # 4.5 E6.2：对候选目标章做 L2 live 复核，把「多视频章」拆成逐个 video task，
        #     并让「当前未完成的视频」不被提前当作完成（4708 双视频只播 1 个的问题）。
        if queue.items:
            head = queue.items[0]
            head_cid = str((existing.get(head.get("task_id", "")) or
                            next((t.chapter_id for t in tasks
                                  if t.task_id == head.get("task_id")), "")) or "")
            if head_cid:
                from tvdp.tdvp import chapter_video_summary, build_live_pending
                verify = None
                # 洞3：目录发现阶段已顺带读到的该章点级（同一次浏览器），直接复用，
                #     避免再开一次浏览器（live_verify_chapter）去重复深读。
                if _combined_points and head_cid == _pred_head:
                    tv, tf = chapter_video_summary(_combined_points)
                    verify = {
                        "video_total": tv,
                        "video_finished": tf,
                        "live_pending": build_live_pending(_combined_points),
                    }
                if verify is None:
                    verify = live_verify_chapter(
                        head_cid,
                        params.get("course_id", ""),
                        params.get("clazz_id", ""),
                        params.get("cpi", ""),
                        os.environ.get("CX_USER", ""),
                        os.environ.get("CX_PASS", ""),
                    )
                if verify is not None:
                    total_v = verify.get("video_total", 0)
                    live_pending = verify.get("live_pending") or set()
                    # 洞2：点级真源快照存进 registry（缓存层）；done 由点级校准。
                    from e6.task_registry import (
                        set_chapter_point_snapshot, load_chapter_points,
                        merge_done_with_points,
                    )
                    set_chapter_point_snapshot(
                        course_key, head_cid,
                        video_total=total_v,
                        video_finished=verify.get("video_finished", 0),
                        has_video=total_v > 0,
                    )
                    # 重建 discovery：真正的视频点数量 + live pending
                    video_counts = {head_cid: total_v} if total_v > 0 else None
                    tasks2 = build_tasks_from_discovery(chapters_raw, video_counts=video_counts)
                    existing2, report2 = reconcile_registry(
                        course_key, existing, tasks2, dom_status, live_pending=live_pending)
                    save_registry(course_key, existing2)
                    existing = existing2
                    pts_map = load_chapter_points(course_key)
                    done_ids = merge_done_with_points(
                        done_chapter_ids_from_registry(existing), set(), pts_map)
                    queue = reconcile_queue(course_key, existing, done_ids, points_map=pts_map)
                    print(f"[scheduler] TDVP: E6.2 after live refine {head_cid} "
                          f"video_total={total_v} finished_video = "
                          f"{verify.get('video_finished')} tasks={len(existing2)} "
                          f"queue={len(queue.items)}", flush=True)
                    # 该"目标章"实际没有任何视频点（纯文本/知识扩展章，如 4705）：
                    # 不应把它当作 video 运行——把它从 video READY 排除，避免白白
                    # 播一个不存在的视频而 DEGRADED/BLOCKED。
                    if total_v == 0:
                        head_tid = head.get("task_id", "")
                        rec = existing2.get(head_tid)
                        if rec is not None and (getattr(rec, "task_type", "video") or "video") == "video":
                            rec.task_type = "other"
                            rec.status = "PENDING"      # 非视频 → 不执行
                            save_registry(course_key, existing2)
                            done_ids = merge_done_with_points(
                                done_chapter_ids_from_registry(existing2), set(),
                                load_chapter_points(course_key))
                            queue = reconcile_queue(course_key, existing2, done_ids, points_map=load_chapter_points(course_key))
                            print(f"[scheduler] TDVP: {head_cid} has no video, "
                                  f"dropped as run target", flush=True)

        # 5. 选下一个任务（剔除本轮已处理过的章节，避免 re-probe 重复重选同一章）
        candidates = _apply_excluded(queue, exclude_chapters)
        if not candidates:
            print("[scheduler] TDVP: queue empty (or all remaining chapters "
                  "already handled this run)", flush=True)
            return None

        next_item = candidates[0]
        next_tid = next_item.get("task_id", "")
        next_rec = existing.get(next_tid)
        if not next_rec:
            print(f"[scheduler] TDVP: next task {next_tid} not in registry", flush=True)
            return None

        # 有 chapter_id → 直接返回（返回 task_id，调用方用 _split_video_target 取 index）
        if next_rec.chapter_id:
            print(f"[scheduler] TDVP: next_task={next_tid} "
                  f"({next_rec.title})", flush=True)
            return next_tid

        # 无 chapter_id → click_probe 获取
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
                queue2 = reconcile_queue(course_key, existing, done_ids, points_map=pts_map)
                candidates2 = _apply_excluded(queue2, exclude_chapters)
                if not candidates2:
                    return None
                rec2 = existing.get(candidates2[0]["task_id"])
                if rec2 and rec2.chapter_id and rec2.chapter_id not in done_ids:
                    print(f"[scheduler] TDVP: click-probe resolved -> {rec2.task_id}",
                          flush=True)
                    return rec2.task_id
                elif rec2 and rec2.chapter_id and rec2.chapter_id in done_ids:
                    next_tid = candidates2[0]["task_id"]
                    next_rec = existing.get(next_tid)
                    if not next_rec or next_rec.chapter_id:
                        break
                    continue
                else:
                    next_tid = candidates2[0]["task_id"]
                    next_rec = existing.get(next_tid)
                    if not next_rec or next_rec.chapter_id:
                        break
                    continue
            else:
                print("[scheduler] TDVP: click-probe failed, trying fallback", flush=True)
                break

        # fallback: 取候选队列第二个任务
        queue3 = reconcile_queue(course_key, existing, done_ids, points_map=pts_map)
        candidates3 = _apply_excluded(queue3, exclude_chapters)
        if len(candidates3) > 1:
            second = candidates3[1]
            rec2 = existing.get(second["task_id"])
            if rec2 and rec2.chapter_id:
                print(f"[scheduler] TDVP: fallback to 2nd task: {rec2.task_id}",
                      flush=True)
                return rec2.task_id

        print("[scheduler] TDVP: no executable task with chapter_id found", flush=True)
        return None

    except Exception as e:
        print(f"[scheduler] TDVP probe failed (non-fatal): {e}", file=sys.stderr)
        return _fallback_chapter(course_url, course_key,
                                 exclude_chapters=exclude_chapters)



def sync_tdvp_on_switch(new_identity, course_url: str) -> None:
    """课程切换时同步任务登记表（TDVP/E6 清空，新课程从零开始）。

    架构：调度器实际使用的是 e6.task_registry（state/registry/<key>/tasks.json）。
    旧版误写在已弃用的 tvdp_tasks.json，导致切换课程后 e6 registry 残留旧任务。
    """
    try:
        from e6.task_registry import save_registry
        # 清空新课程（即将激活）的任务登记；旧课程 registry 保留作诊断
        save_registry(new_identity.key(), {})
    except Exception:
        pass


# 导入 os/Path
import os
from pathlib import Path


def _chapter_info_for_task(t) -> "object":
    """为单个 TaskInfo 构造 ChapterInfo（用于 sync_progress_to_course_state）。"""
    from tvdp.tdvp import ChapterInfo
    return ChapterInfo(chapter_id=t.chapter_id, title=t.title, tasks=[t])
