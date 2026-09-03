"""E3-C single-run CI runner (reuses validated E2 headed-browser core).

E3-C verifies that the E2-approved end-to-end automation chain is repeatable
when run as *independent* GitHub Actions runs (each on a fresh runner /
browser context). It reuses `e2/e2_headed_gha.py::run_test` unchanged (the
10-check natural playback→multimedia/log→isPassed→recheck chain), and wraps
the resulting evidence into the unified **E3 Experiment Result Schema** with a
**Failure Taxonomy** label so all runs are horizontally comparable.

Constraints preserved (inherited from E2/E1.2): legal login only, no direct
call/forgery of multimedia/log, no modification of enc/attDurationEnc/
videoFaceCaptureEnc/playingTime/_t, no replay, no forged completion, no
skipping real playback. Actually triggers CI once per invocation.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Make E2 module importable (repo-relative sibling)
_REPO = Path(__file__).resolve().parent.parent
_E2_DIR = _REPO / "e2"
if str(_E2_DIR) not in sys.path:
    sys.path.insert(0, str(_E2_DIR))

from e2_headed_gha import run_test as e2_run_test  # noqa: E402
from e2_headed_gha import _write as e2_write        # noqa: E402

# Failure Taxonomy: E2 verification_10 key -> best-fit E3 failure stage
# (keys must match exactly what e2_headed_gha.py writes)
_FAILURE_MAP = {
    "1_login_ok":                "LOGIN_FAILED",
    "2_studentstudy_loaded":     "PAGE_LOAD_FAILED",
    "3_cards_iframe_loaded":     "CARDS_IFRAME_FAILED",
    "4_recursive_ananas_iframe": "ANANAS_IFRAME_FAILED",
    "5_video_duration_ok":       "VIDEO_METADATA_FAILED",
    "6_playback_started":        "PLAYBACK_START_FAILED",
    "7_currentTime_growing":     "PLAYBACK_STALLED",
    "8_ml_log_natural":          "MULTIMEDIA_LOG_FAILED",
    "9_isPassed_true":           "IS_PASSED_FALSE",
    "10_post_verification":      "POST_RECHECK_FAILED",
}

# ordered for first-failed-check scan, lowest stage first in pipeline order
_ORDER = list(_FAILURE_MAP.keys())


def _browser_version(history):                      # reserved
    b = history.get("browser") or {}
    return b.get("version")


def _derive_failure(ev: dict) -> str:
    """Map the first failing E2 check to an E3 failure stage (or NONE)."""
    ver = ev.get("verification_10") or {}
    if ev.get("passed_count", 0) == 10:
        return "NONE"
    if "session kicked" in (ev.get("verdict") or ""):
        return "SESSION_KICKED"
    if not ver:
        return "UNKNOWN"
    for key in _ORDER:
        if not ver.get(key):
            return _FAILURE_MAP[key]
    return "UNKNOWN"


def build_e3_result(label: str, chapter_id: str, ev: dict, total_s: float,
                    retry_count: int = 0) -> dict:
    """Wrap an E2 evidence dict into the uniform E3 schema."""
    checks = ev.get("checks") or {}
    verification = ev.get("verification_10") or {}
    browser = ev.get("browser") or {}
    task = {
        "course_id": "265997861",
        "clazz_id":  "151695658",
        "chapter_id": chapter_id,
        "object_id":  "E2-observed (jobid/objectId in ml_log)",
    }
    exec_map = {
        "login":           "PASS" if verification.get("1_login_ok") else "FAIL",
        "studentstudy":    "PASS" if verification.get("2_studentstudy_loaded") else "FAIL",
        "cards_iframe":    "PASS" if verification.get("3_cards_iframe_loaded") else "FAIL",
        "ananas_iframe":   "PASS" if verification.get("4_recursive_ananas_iframe") else "FAIL",
        "video_discovery": "PASS" if verification.get("4_recursive_ananas_iframe") else "FAIL",
        "duration":        ev.get("video_duration"),
        "playback_started":"PASS" if verification.get("6_playback_started") else "FAIL",
        "currentTime_growing": "PASS" if verification.get("7_currentTime_growing") else "FAIL",
        "multimedia_log":  "PASS" if verification.get("8_ml_log_natural") else "FAIL",
        "is_passed":       bool(verification.get("9_isPassed_true")),
        "independent_recheck": "PASS" if verification.get("10_post_verification") else "FAIL",
        "next_unit":       "PASS" if ev.get("nextunit_triggered") else (
                           "SKIP" if ev.get("nextunit_triggered") is None else "FAIL"),
    }
    result = {
        "experiment_id": label,
        "task": task,
        "environment": {
            "runner":          "github-actions" if "GITHUB_RUN_ID" in os.environ else "local",
            "browser":         browser.get("channel"),
            "browser_version": browser.get("version"),
            "headed":          not browser.get("headless"),
            "xvfb":            ev.get("xvfb_status") == "available",
        },
        "execution": exec_map,
        "timing": {"total_seconds": round(total_s, 1)},
        "retry_count": retry_count,
        "failure_stage": _derive_failure(ev),
        "evidence_raw": {
            "verdict": ev.get("verdict"),
            "passed_count": ev.get("passed_count"),
            "total_checks": ev.get("total_checks"),
            "verification_10": verification,
            "ml_log_count": ev.get("ml_log_count"),
            "max_currentTime": ev.get("max_currentTime"),
            "video_duration": ev.get("video_duration"),
            "nextunit_chapterId": ev.get("nextunit_chapterId"),
            "loop_seconds": ev.get("loop_seconds"),
        },
    }
    return result


def main():
    ap = argparse.ArgumentParser(description="E3-C: single reliability run on CI")
    ap.add_argument("--chapter-id", default=os.environ.get("CHAPTER_ID", "1217304705"))
    ap.add_argument("--label", default="E3-C-001")
    ap.add_argument("--output", default="./evidence_E3-C-001.json")
    ap.add_argument("--xvfb-display", default=None)
    args = ap.parse_args()

    t0 = time.time()
    # build a minimal args object expected by run_test
    e2_args = argparse.Namespace(
        chapter_id=args.chapter_id,
        output=args.output,
        xvfb_display=args.xvfb_display,
        debug_capture=False,
    )
    ev = e2_run_test(e2_args)     # runs full headed natural-playback verification
    total = time.time() - t0
    e3 = build_e3(args.label, args.chapter_id, ev, total, retry_count=0)
    # embed the meta for debuggability
    e3.setdefault("meta", {}).update({
        "e3_test": "E3-C",
        "github_run_id":    os.environ.get("GITHUB_RUN_ID", "local"),
        "github_run_number": os.environ.get("GITHUB_RUN_NUMBER", "local"),
        "github_sha":       os.environ.get("GITHUB_SHA", "local"),
        "finished_at":      ev.get("finished_at_utc"),
    })
    out_path = args.output
    Path(out_path).write_text(json.dumps(e3, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[E3-C] wrote {out_path}")
    print(f"[E3-C] {args.label}: verdict={ev.get('verdict')} "
          f"passed={ev.get('passed_count')}/10 failure_stage={e3['failure_stage']} "
          f"total={total:.1f}s")
    # reuse e2 exit convention for CI success/failure
    sys.exit(0 if ev.get("passed_count") == 10 else 1)


if __name__ == "__main__":
    main()