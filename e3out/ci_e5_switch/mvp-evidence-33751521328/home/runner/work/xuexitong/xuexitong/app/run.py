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
    load_active_course, save_course_state, activate_course,
    initialize_course, run_course as state_run_course,
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
    ap.add_argument("--action", choices=["initialize", "run", "switch"],
                    default="run", help="操作模式（默认 run）")
    ap.add_argument("--course-url", required=True,
                    help="学习通 studentstudy URL")
    ap.add_argument("--chapter-id", default=None,
                    help="要学习的章节 id（run 模式）")
    ap.add_argument("--max-chapters", type=int, default=1,
                    help="最多自动学习的视频任务点数量（默认 1）")
    ap.add_argument("--output", default="./evidence/run_<ts>.json")
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="视频 iframe/metadata 瞬态失败的最大尝试次数（默认 2）")
    ap.add_argument("--xvfb-display", default=os.environ.get("DISPLAY", ":99"))
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
    else:
        sys.exit(cmd_run(args))


if __name__ == "__main__":
    main()
