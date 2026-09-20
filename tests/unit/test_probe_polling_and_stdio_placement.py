"""时长探测必须等 metadata 到位；stdio 加固必须在入口而不是库导入。

两项都来自 2026-09-20 第 3 轮 M0 验证（ACCEPTANCE §4.5）：

(a) `_probe_video_duration_s` 打开页面后只 `wait_for_timeout(4000)` 就取 duration，
    4/4 次拿到 `{'currentTime': 0, 'duration': None, 'readyState': 0}` —— 即 P0-01 的
    自适应看门狗**从未生效**，一直在用静态 900s（838s 视频的 714 那轮属侥幸跑完）。
(b) `ensure_utf8_stdio()` 挂在 `scheduler.scheduler` 的模块导入上，而 `ci_local_run.py`
    在 import 之前就已经按 gbk 打过第一行 → 同一份父日志混合编码（字节统计
    `ci_local 第` utf8=2 / gbk=1）。证据文件本身变得不可单一解码。
"""

import ast
from pathlib import Path

from scheduler.scheduler import poll_video_duration

ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_PY = ROOT / "scheduler" / "scheduler.py"
CI_LOCAL_PY = ROOT / "scripts" / "ci_local_run.py"


# ── (a) poll_video_duration ───────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _run(read_states, deadline_s=20.0, sleep_s=1.0):
    """用注入的 clock/sleeper 驱动 poll_video_duration，返回 (结果, 调用次数)。"""
    seq = list(read_states)
    calls = {"n": 0}
    clk = FakeClock()

    def read_state():
        calls["n"] += 1
        return seq.pop(0) if seq else read_states[-1]

    def sleeper(s):
        clk.now += s

    out = poll_video_duration(read_state, deadline_s=deadline_s, sleep_s=sleep_s,
                              clock=clk, sleeper=sleeper)
    return out, calls["n"]


def test_returns_immediately_when_duration_present():
    out, n = _run([{"found": True, "duration": 655.978, "readyState": 4}])
    assert out == (655.978, None)
    assert n == 1


def test_keeps_polling_until_metadata_arrives():
    out, n = _run([{"duration": None, "readyState": 0},
                   {"duration": None, "readyState": 0},
                   {"duration": 838.0, "readyState": 1}])
    assert out == (838.0, None)
    assert n == 3


def test_gives_up_with_a_diagnosable_reason_at_deadline():
    out, n = _run([{"found": True, "duration": None, "readyState": 0,
                    "currentTime": 0, "paused": True}], deadline_s=3.0, sleep_s=1.0)
    dur, reason = out
    assert dur is None
    assert reason and "duration" in reason, f"原因必须可读，实际 {reason!r}"
    assert n == 4                      # 3s 预算 / 1s 间隔 → 轮询到预算用尽


def test_never_becomes_a_new_failure_point():
    """探测本身绝不许把 scheduler 拖崩 —— 读状态抛异常要转成原因。"""
    def boom():
        raise RuntimeError("frame detached")

    clk = FakeClock()
    dur, reason = poll_video_duration(boom, deadline_s=20.0, sleep_s=1.0,
                                      clock=clk, sleeper=lambda s: setattr(clk, "now", clk.now + s))
    assert dur is None
    assert "RuntimeError" in reason


def test_bounded_even_if_the_clock_never_advances():
    """轮询必须有硬次数上限：时钟不前进（注入/回退）时也不许死循环。"""
    from scheduler.scheduler import PROBE_MAX_POLLS

    clk = FakeClock()
    calls = {"n": 0}

    def read_state():
        calls["n"] += 1
        return {"duration": None}

    dur, reason = poll_video_duration(read_state, deadline_s=1.0, sleep_s=1.0,
                                      clock=clk, sleeper=lambda s: None)
    assert dur is None and reason
    assert calls["n"] <= PROBE_MAX_POLLS


# ── (b) 加固位置 ──────────────────────────────────────────────────

def _module_level_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        getattr(n.value.func, "id", None) or getattr(n.value.func, "attr", None)
        for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    }


def test_library_module_does_not_reconfigure_shared_stdio_at_import():
    """库模块在 import 时改 sys.stdout → 谁先打印谁就留在旧编码里，日志混编。"""
    assert "ensure_utf8_stdio" not in _module_level_calls(SCHEDULER_PY)


def test_ci_local_entry_hardens_stdio():
    assert "ensure_utf8_stdio" in _module_level_calls(CI_LOCAL_PY)


def test_ci_local_hardening_precedes_its_first_print():
    src = CI_LOCAL_PY.read_text(encoding="utf-8")
    harden = src.index("ensure_utf8_stdio()")
    first_print = src.index("print(")
    assert harden < first_print, "加固必须早于入口的第一次输出，否则首行仍按 gbk 落盘"
