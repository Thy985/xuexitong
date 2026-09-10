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


def main():
    b = _behavior()
    delay = _delay()
    if delay:
        time.sleep(delay)
    if b == "stuck":
        # emulates the "subprocess never exits" failure mode of run 34311891898.
        while True:
            time.sleep(1)
    elif b == "exit1":
        print("fake app.run FAIL", flush=True)
        sys.exit(1)
    # default: exit 0
    print("fake app.run OK", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())