"""Fake `app.run` module — substitute for the real `python -m app.run` child.

Purpose (watchdog regression): `scheduler._run_one_chapter` spawns a REAL
subprocess via `python -m app.run ...`. For P0-01/P0-07 regression we must NOT
mock that subprocess away — we let the watchdog exercise the real
`Popen(start_new_session)` / `wait(timeout)` / `killpg` / exit-124 path.
This fake `app` package is injected onto PYTHONPATH *before* the real one so
`python -m app.run` resolves here instead. Behavior is selected by env vars:

  FAKE_RUN_BEHAVIOR=stuck  -> sleep forever (watchdog must kill + exit 124)
                      exit0 -> exit 0 (watchdog PASS path)
                      exit1 -> exit 1 (watchdog FAIL path)
                   evidence -> 写一份真实形状的 --output 产物后按 verdict 退出
                               （配 FAKE_RUN_VERDICT / FAKE_RUN_STAGE / FAKE_RUN_TITLE；
                                FAKE_RUN_CORRUPT=1 写一份截断产物）
             slow_announced -> 像真引擎那样先自报视频时长再跑完
                               （配 FAKE_RUN_ANNOUNCE=0 静默、FAKE_RUN_DURATION_S、
                                FAKE_RUN_SLEEP_S；R5 看门狗交接回归用）
  FAKE_RUN_DELAY_S         -> optional npop/emprec delay before acting

No real site is touched; no credentials are used.
"""
import os
import time
import sys


def _behavior():
    return os.environ.get("FAKE_RUN_BEHAVIOR", "exit0")


def _delay():
    try:
        return float(os.environ.get("FAKE_RUN_DELAY_S", "0"))
    except Exception:
        return 0.0


def _arg(name, default=""):
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
    return default


def _write_evidence():
    """behavior=evidence：按 FAKE_RUN_VERDICT/FAKE_RUN_STAGE 写一份真实形状的产物。

    用来验父进程是否原样传递子进程自述的 verdict / failure_stage，
    以及重投同一章时会不会抹掉上一轮产物。
    """
    import json
    import os
    from pathlib import Path

    verdict = os.environ.get("FAKE_RUN_VERDICT", "PASS")
    stage = os.environ.get("FAKE_RUN_STAGE", "")
    title = os.environ.get("FAKE_RUN_TITLE", "")
    out = _arg("--output")
    payload = {
        "result": {"verdict": verdict, "exit_code": 0 if verdict == "PASS" else 1,
                   "timing_s": 1.0, "passed_count": 10 if verdict == "PASS" else 9},
        "evidence": {"verdict": verdict, "failure_stage": stage or None},
    }
    if title:
        # 真站产物里有章标题、页面文案、console 行 —— 全是非 ASCII。
        payload["evidence"]["nextunit_title"] = title
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = "{" if os.environ.get("FAKE_RUN_CORRUPT") else \
            json.dumps(payload, ensure_ascii=False)
        p.write_text(body, encoding="utf-8")
    sys.exit(0 if verdict == "PASS" else 1)


def _slow_announced():
    """behavior=slow_announced：复刻真引擎的 Step F——自报时长后继续播。

    真 app 打的是 `Video ready: duration=1130s currentTime=... paused=False`
    （`app/e2_headed_gha.py` 的 `log()`，`flush=True`），看门狗读的正是这一行，
    所以这里的字面形状必须和真站一致，否则测的是解析器自己的想象。
    """
    dur = os.environ.get("FAKE_RUN_DURATION_S", "6")
    try:
        sleep_s = float(os.environ.get("FAKE_RUN_SLEEP_S", "4"))
    except Exception:
        sleep_s = 4.0
    if os.environ.get("FAKE_RUN_ANNOUNCE", "1") != "0":
        print(f"fake app.run Video ready: duration={dur}s currentTime=0.0 "
              f"paused=False", flush=True)
    time.sleep(sleep_s)
    print("fake app.run OK", flush=True)
    sys.exit(0)


def main():
    b = _behavior()
    delay = _delay()
    if delay:
        time.sleep(delay)
    if b == "stuck":
        # emulates the "subprocess never exits" failure mode of run 34311891898.
        while True:
            time.sleep(1)
    elif b == "evidence":
        _write_evidence()
    elif b == "slow_announced":
        _slow_announced()
    elif b == "exit1":
        print("fake app.run FAIL", flush=True)
        sys.exit(1)
    # default: exit 0
    print("fake app.run OK", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())