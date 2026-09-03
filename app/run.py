#!/usr/bin/env python3
"""xuexitong MVP — 产品入口 (Fork → Secrets → Course URL → Run)

单个文件的 MVP CLI：给定一个"学习通 课程/章节 URL"，用真实的
headed 浏览器(Chromium+Xvfb)登录并自然播放到任务点完成，输出
"Evidence + verdict"。复用自 e2_headed_gha.run_test（已参数化，
支持任意 instructor/课程 URL），并保持 E-series 约束不变：
不构造/不伪造/不重放 multimedia/log，不改 enc/playingTime/_t 等。

用法（在带 Xvfb 的 Linux/CI 环境）:
  export CX_USER=... CX_PASS=...
  python app/run.py \
      --course-url "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=...&courseId=...&clazzid=...&cpi=...&enc=..."
      --output ./evidence/run_<ts>.json     [--chapter-id ...] [--max-chapters 1]

任意学员/Fork 用户凭自己的课程 URL 接入同一引擎。Evidence 产物与
verdict 供审计/复现；E-series(e2/e3) 仍作为内部实验/验证入口保留。
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

# UTF-8 输出鲁棒性（Windows GBK 控制台也能显示中文/符号）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from e2_headed_gha import (parse_course_url, run_test, DEMO_CHAPTER)  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="xuexitong MVP: 真实浏览器自然学习一个视频任务点并输出证据")
    ap.add_argument("--course-url", required=True,
                    help="学习通 student study 页 URL（含 courseId/clazzid/cpi/enc/chapterId）")
    ap.add_argument("--chapter-id", default=None,
                    help="要学习的章节 id（缺省用 URL 中 chapterId；再缺省回退 demo 章节）")
    ap.add_argument("--max-chapters", type=int, default=1,
                    help="最多自动学习的视频任务点数量（MVP 默认 1 个；>1 才跨章节推进）")
    ap.add_argument("--output", default="./evidence/run_<ts>.json")
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="对视频 iframe/metadata 瞬态失败的最大尝试次数（含首次；默认 2，含 1 次重试）")
    ap.add_argument("--xvfb-display", default=os.environ.get("DISPLAY", ":99"))
    args = ap.parse_args()

    # 校验必备 Secrets
    if "CX_USER" not in os.environ or "CX_PASS" not in os.environ:
        print("[!] 缺少环境变量 CX_USER / CX_PASS。请在 env 或 GitHub Secrets 中设置。")
        sys.exit(2)

    # 解析 URL → 课程参数
    course = parse_course_url(args.course_url)
    missing = [k for k, v in course.items() if not v]
    if not course or missing:
        print("[!] 无法从 course-url 解析完整课程参数，缺:", missing or "全部")
        sys.exit(2)
    chapter = args.chapter_id or course["chapter_id"]

    # 把课程参数落到引擎模块全局（build_base_url 需要）
    import e2_headed_gha as E
    E.COURSE_ID = course["course_id"]
    E.CLAZZ_ID = course["clazz_id"]
    E.CPI = course["cpi"]
    E.ENC = course["enc"]
    # openc / hidetype 决定 cards iframe 是否渲染（缺则 no_cards_frame）
    E.OPENR = course.get("openc")
    E.HIDETYPE = course.get("hidetype") or "0"

    out_path = args.output.replace("<ts>", str(int(time.time())))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    eargs = argparse.Namespace(
        chapter_id=chapter,
        output=out_path,
        xvfb_display=args.xvfb_display,
        debug_capture=False,
        course_id=course["course_id"], clazz_id=course["clazz_id"],
        cpi=course["cpi"], enc=course["enc"],
    )

    # ── 带限次重试的循环（针对已知"视频 iframe/metadata 初始化"偶发失败）──
    # 约束：login 失败/kick(账密/会话) 不重试；仅对 frame/metadata/起播类瞬时
    # 重现重试，并如实记录 retry_count。证据模式(e2/e3)保持单次，不受影响。
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
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"  retry {attempt-1}/{max_attempts-1} …", flush=True)
        ev = run_test(eargs)            # 每次都是全新浏览器会话
        v = ev.get("verdict", "")
        if attempt < max_attempts and retryable(v):
            retry_count += 1
            continue                     # 瞬态失败 → 重试
        break                            # 通过，或不可重试，或已达上限
    total = time.time() - t0

    # 若重试后仍失败，保留最后一次（也是最接近成功）的 evidence 供审计
    passed = ev.get("passed_count") == 10 if ev is not None else False
    res = {
        "app": "xuexitong-mvp",
        "target": {"course_url": args.course_url, "chapter_id": chapter},
        "env_runner": "github-actions" if "GITHUB_RUN_ID" in os.environ else "local",
        "timing_s": round(total, 1),
        "retry_count": retry_count,
        "verdict": "PASS" if passed else ("DEGRADED" if ev and ev.get("passed_count", 0) >= 6 else "FAIL"),
        "passed_count": ev.get("passed_count") if ev else None,
        "failure_stage": (ev or {}).get("failure_stage"),
        "evidence_file": out_path,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    final = {"result": res, "evidence": ev or {}}
    Path(out_path).write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"Evidence saved: {out_path}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()