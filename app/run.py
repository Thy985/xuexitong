#!/usr/bin/env python3
"""xuexitong MVP — E5 产品入口 (Initialize / Run / Switch)

E5 升级后支持三种模式：

  1. initialize — 解析课程 URL，创建/初始化课程状态（不执行学习）
  2. run        — 加载活跃课程状态，执行一次视频学习（默认）
  3. switch     — 解析新课程 URL，归档旧课程，激活新课程（不执行学习）

用法:
  # Initialize
  python app/run.py --action initialize --course-url "..."

  # Run (默认)
  python app/run.py --course-url "..." --chapter-id 1217304706

  # Switch
  python app/run.py --action switch --course-url "..."

输出:
  - Evidence JSON 到 --output
  - 课程状态更新到 state/ 目录（跨 Run 持久化）
  - Actions 日志中打印诊断信息
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 复用 e2 已验证的 10 项闭合验证引擎
_SELF = Path(__file__).resolve().parent.parent
_E2 = _SELF / "e2"
if str(_E2) not in sys.path:
    sys.path.insert(0, str(_E2))

# E5 模块路径（确保 CI 和本地都能导入）
for _p in [_SELF / "resolvers", _SELF / "state", _SELF / "e2"]:
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

# UTF-8 输出鲁棒性
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from e2_headed_gha import parse_course_url, run_test, DEMO_CHAPTER  # noqa: E402
from resolvers.course_resolver import resolve_course, detect_course_change  # noqa: E402
from state.course_state import (  # noqa: E402
    load_active_course, load_course_state, save_course_state,
    activate_course, archive_course, initialize_course,
    run_course as state_run_course,
    CourseIdentity as StateCourseIdentity, CourseProgress,
)


def _make_state_identity(resolved: dict) -> StateCourseIdentity:
    """从 resolve_course 结果构建 state 模块的 Identity。"""
    return StateCourseIdentity(
        course_id=resolved["course_id"],
        clazz_id=resolved["clazz_id"],
        cpi=resolved["cpi"],
        title=resolved.get("title", f"course_{resolved['course_id']}"),
        raw_url=resolved.get("raw_url", ""),
        resolved_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def _write_output(output: str, data: dict) -> None:
    """将结果 dict 原子写入 output 路径（evidence）。"""
    if not output:
        return
    try:
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # pragma: no cover
        print(f"[!] 无法写入输出文件 {output}: {e}", flush=True)


def cmd_initialize(args) -> int:
    """Initialize: 解析 URL，创建/初始化课程状态。"""
    print("[initialize] Resolving course URL …", flush=True)
    result = resolve_course(args.course_url)

    if not result.is_ok():
        print(f"[initialize] FAILED: {result.error}", flush=True)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 2

    identity = _make_state_identity(result.to_dict()["identity"])
    state = initialize_course(identity)

    out = {
        "action": "initialize",
        "status": "OK",
        "identity": identity.to_dict(),
        "state": state.to_dict(),
        "resolver_evidence": result.evidence,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    _write_output(args.output, out)
    return 0


def cmd_switch(args) -> int:
    """Switch: 解析新课程 URL，归档旧课程，激活新课程。"""
    print("[switch] Resolving new course URL …", flush=True)
    new_result = resolve_course(args.course_url)

    if not new_result.is_ok():
        print(f"[switch] FAILED: {new_result.error}", flush=True)
        return 2

    new_identity = _make_state_identity(new_result.to_dict()["identity"])

    # 检测切换类型
    active = load_active_course()
    detection = detect_course_change(args.course_url, active)

    print(f"[switch] Detection: {detection.kind}", flush=True)
    print(f"[switch] Details: {detection.details}", flush=True)

    # 激活新课程
    activate_course(new_identity)
    new_state = load_course_state(new_identity.key())

    out = {
        "action": "switch",
        "detection": detection.to_dict(),
        "new_identity": new_identity.to_dict(),
        "new_state": new_state.to_dict() if new_state else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    _write_output(args.output, out)
    return 0


def cmd_run(args) -> int:
    """Run: 加载活跃课程状态，执行视频学习。"""
    # 解析 URL（用于设置引擎参数）
    course = parse_course_url(args.course_url)
    missing = [k for k, v in course.items() if not v]
    if not course or missing:
        print(f"[run] 无法解析 URL，缺: {missing}", flush=True)
        return 2

    chapter = args.chapter_id or course["chapter_id"]

    # 设置引擎全局参数
    import e2_headed_gha as E
    E.COURSE_ID = course["course_id"]
    E.CLAZZ_ID = course["clazz_id"]
    E.CPI = course["cpi"]
    E.ENC = course["enc"]
    E.OPENR = course.get("openc")
    E.HIDETYPE = course.get("hidetype") or "0"

    # 加载或初始化课程状态
    active = load_active_course()
    if active:
        identity = _make_state_identity({
            "course_id": active.course_id,
            "clazz_id": active.clazz_id,
            "cpi": active.cpi,
            "title": active.title,
            "raw_url": active.raw_url,
            "resolved_at_utc": active.resolved_at_utc,
        })
    else:
        # 无活跃课程，自动初始化
        from resolvers.course_resolver import resolve_course as rc
        r = rc(args.course_url)
        if not r.is_ok():
            print(f"[run] 无法解析课程: {r.error}", flush=True)
            return 2
        identity = _make_state_identity(r.to_dict()["identity"])
        initialize_course(identity)
        active = load_active_course()

    print(f"[run] Active course: {identity.key()}", flush=True)

    # 执行学习
    out_path = args.output.replace("<ts>", str(int(time.time())))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    eargs = argparse.Namespace(
        chapter_id=chapter,
        output=out_path,
        xvfb_display=args.xvfb_display,
        debug_capture=False,
        course_id=course["course_id"],
        clazz_id=course["clazz_id"],
        cpi=course["cpi"],
        enc=course["enc"],
    )

    def retryable(verdict: str) -> bool:
        v = verdict or ""
        if "login failed" in v or "session kicked" in v:
            return False
        return any(k in v for k in (
            "video metadata not ready", "no_cards_frame",
            "ananas", "cards iframe", "Heartbeat dead", "currentTime",
        ))

    max_attempts = max(1, args.max_attempts)
    t0 = time.time()
    ev = None
    retry_count = 0
    crash_msg = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"  retry {attempt-1}/{max_attempts-1} …", flush=True)
        try:
            ev = run_test(eargs)
        except Exception as e:
            crash_msg = f"{type(e).__name__}: {e}"
            print(f"[!] run_test crashed: {crash_msg}", flush=True)
            ev = ev or {"verdict": "CRASH", "passed_count": 0, "errors": [crash_msg]}
            try:
                Path(out_path).write_text(
                    json.dumps({"result": {}, "evidence": ev},
                              ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            break
        v = ev.get("verdict", "")
        if attempt < max_attempts and retryable(v):
            retry_count += 1
            continue
        break

    total = time.time() - t0
    passed = ev.get("passed_count") == 10 if ev is not None else False
    exit_code = 0 if passed else 1
    verdict_str = (
        "PASS" if passed
        else ("DEGRADED" if ev and ev.get("passed_count", 0) >= 6 else "FAIL")
    )

    # 【架构变更】Postflight: 如果 PASS 且有 passed_object_ids，更新 Task Registry
    # 确保 done 状态来自 SERVER_VERIFIED 证据，而非 history
    if passed and identity:
        try:
            from e6.task_registry import load_registry, save_registry
            reg = load_registry(identity.key())
            passed_obj_ids = ev.get("passed_object_ids", [])
            if passed_obj_ids:
                # 找到匹配的 task 并标记为 SERVER_VERIFIED
                updated = False
                for t in reg.values():
                    if t.chapter_id == chapter:
                        t.mark_completed(
                            run_id=os.environ.get("GITHUB_RUN_ID", "local"),
                            evidence_level="SERVER_VERIFIED",
                            detail=f"passed_object_ids={len(passed_obj_ids)}",
                        )
                        updated = True
                        print(f"[run] Task {chapter} marked SERVER_VERIFIED "
                              f"(passed_object_ids={len(passed_obj_ids)})", flush=True)
                        break
                if updated:
                    save_registry(identity.key(), reg)
            else:
                # 无 passed_object_ids 但仍 PASS（可能是旧版引擎）
                # 降级为 UI 级别验证
                for t in reg.values():
                    if t.chapter_id == chapter:
                        t.mark_completed(
                            run_id=os.environ.get("GITHUB_RUN_ID", "local"),
                            evidence_level="UI",
                            detail="PASS but no passed_object_ids",
                        )
                        print(f"[run] Task {chapter} marked UI (no passed_object_ids)",
                              flush=True)
                        break
        except Exception as e:
            print(f"[run] Task registry update failed (non-fatal): {e}",
                  file=sys.stderr, flush=True)

    # 更新课程状态
    if identity:
        state = state_run_course(
            identity, chapter, passed, total, verdict_str
        )
    else:
        state = None

    res = {
        "app": "xuexitong-mvp",
        "action": "run",
        "target": {"course_url": args.course_url, "chapter_id": chapter},
        "course_key": identity.key() if identity else None,
        "env_runner": "github-actions" if "GITHUB_RUN_ID" in os.environ else "local",
        "timing_s": round(total, 1),
        "retry_count": retry_count,
        "exit_code": exit_code,
        "verdict": verdict_str,
        "passed_count": ev.get("passed_count") if ev else None,
        "failure_stage": (ev or {}).get("failure_stage"),
        "crash": crash_msg,
        "evidence_file": out_path,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    final = {"result": res, "evidence": ev or {}}
    if state:
        final["course_state"] = state.to_dict()

    Path(out_path).write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"Evidence saved: {out_path}")
    return exit_code


def main():
    ap = argparse.ArgumentParser(
        description="xuexitong MVP E5: Initialize / Run / Switch course learning"
    )
    ap.add_argument("--action", choices=["initialize", "run", "scheduler", "switch"],
                    default="run", help="操作模式（默认 run）")
    ap.add_argument("--course-url", default=None,
                    help="学习通 studentstudy URL（scheduler 模式下可选，从 state 读取）")
    ap.add_argument("--chapter-id", default=None,
                    help="要学习的章节 id（run 模式）")
    ap.add_argument("--max-chapters", type=int, default=1,
                    help="最多自动学习的视频任务点数量（默认 1）")
    ap.add_argument("--output", default="./evidence/run_<ts>.json")
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="视频 iframe/metadata 瞬态失败的最大尝试次数（默认 2）")
    ap.add_argument("--xvfb-display", default=os.environ.get("DISPLAY", ":99"))
    ap.add_argument("--trigger", default="manual",
                    choices=["manual", "schedule"],
                    help="触发类型（默认 manual）")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"),
                    help="GitHub run ID（用于记录）")
    args = ap.parse_args()

    # 校验 Secrets（run 模式需要）
    if args.action == "run" and ("CX_USER" not in os.environ or
                                 "CX_PASS" not in os.environ):
        print("[!] 缺少环境变量 CX_USER / CX_PASS。请在 env 或 GitHub Secrets 中设置。")
        sys.exit(2)

    if args.action == "initialize":
        sys.exit(cmd_initialize(args))
    elif args.action == "switch":
        sys.exit(cmd_switch(args))
    elif args.action == "scheduler":
        # Scheduler 模式：由 Scheduler 模块决定是否需要执行
        sys.exit(cmd_scheduler(args))
    else:
        sys.exit(cmd_run(args))


def cmd_scheduler(args) -> int:
    """Scheduler 模式：通过 scheduler 模块决定是否执行。"""
    from scheduler import run_scheduler

    trigger = getattr(args, 'trigger', 'schedule')
    run_id = getattr(args, 'run_id', os.environ.get('GITHUB_RUN_ID', 'local'))

    print(f"[scheduler] Trigger: {trigger}, Run ID: {run_id}", flush=True)
    result = run_scheduler(args.course_url, args.chapter_id or "",
                          trigger, run_id)

    out = {
        "action": "scheduler",
        "decision": result.decision,
        "result": result.result,
        "trigger": result.trigger,
        "course_key": result.course_key,
        "timing_s": result.timing_s,
        "verdict": result.verdict,
        "error": result.error,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

    # 写入 evidence 文件
    _write_output(args.output, out)

    # 如果是 RUN 且需要实际执行学习
    if result.decision == "RUN" and result.result in ("SUCCESS", "FAILED"):
        return 0 if result.passed else 1
    return 0  # NOOP/BLOCKED 不算失败


if __name__ == "__main__":
    main()
