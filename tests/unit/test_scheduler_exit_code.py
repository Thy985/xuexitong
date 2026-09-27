"""issue #3 — scheduler CLI 退出语义：NOOP→0 / BLOCKED / ERROR / RUN-FAILED→1。

回归背景（CVE-对应的运行层 bug）：`app/run.py::cmd_scheduler` 旧实现
`return 0  # NOOP/BLOCKED 不算失败` 把 **NOOP 与 BLOCKED（熔断/需人工介入）统统当成功（绿）**，
导致——
  - BLOCKED = 调度器已主动停摆（连续失败达阈值 / 课程被熔断），却与 SUCCESS 同色，
    从 Actions 颜色上无法识别「卡死 / 未推进」；
  - ERROR（解析/调度异常）同样静默绿。
修复后语义：
  - decision=RUN 且 SUCCESS/FAILED  → 按 result.passed 判 0 / 1（既有逻辑不变）；
  - decision=NOOP                   → 0（确无可推进工作：课程已完成 / 无 active 课程）——绿；
  - decision=BLOCKED / ERROR        → 1（非成功状态：熔断 / 需人工处理 / 异常）——红，
    避免 CI 全绿掩盖「未推进 / 已熔断 / 异常」。
本测试只针对 app.run 层的退出码映射；调度器内部的决策判定由 test_scheduler / 各 BLOCKED 单测负责。
"""
import sys
from types import SimpleNamespace

from scheduler.scheduler import ExecutionResult


def _res(decision, result="NOOP", passed=False, verdict="", error=""):
    return ExecutionResult(
        decision=decision, result=result, trigger="manual", course_key="k",
        run_id="r", timing_s=0.0, passed=passed, verdict=verdict, error=error,
    )


class TestSchedulerExitCodeIssue3:
    def _exit(self, monkeypatch, decision, result="NOOP", passed=False,
              verdict="", error=""):
        import app.run as run_mod
        import scheduler as sched_mod
        fake = _res(decision, result, passed, verdict, error)
        monkeypatch.setattr(sched_mod, "run_scheduler", lambda *a, **kw: fake)
        args = SimpleNamespace(
            course_url=None, chapter_id="", trigger="schedule",
            run_id="r", max_chapters=1, output="/dev/null",
        )
        return run_mod.cmd_scheduler(args)

    def test_noop_green(self, monkeypatch):
        assert self._exit(monkeypatch, "NOOP") == 0

    def test_blocked_red(self, monkeypatch):
        assert self._exit(monkeypatch, "BLOCKED",
                          verdict="Course k is BLOCKED; cooldown ...") == 1

    def test_error_red(self, monkeypatch):
        assert self._exit(monkeypatch, "ERROR",
                          error="Resolve failed: boom") == 1

    def test_run_success_green(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="SUCCESS", passed=True) == 0

    def test_run_success_not_passed_red(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="SUCCESS", passed=False) == 1

    def test_run_failed_red(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="FAILED", passed=False) == 1
    def _exit(self, monkeypatch, decision, result="NOOP", passed=False,
              verdict="", error=""):
        import app.run as run_mod
        import scheduler as sched_mod
        fake = _res(decision, result, passed, verdict, error)
        monkeypatch.setattr(sched_mod, "run_scheduler", lambda *a, **kw: fake)
        args = SimpleNamespace(
            course_url=None, chapter_id="", trigger="schedule",
            run_id="r", max_chapters=1, output="/dev/null",
        )
        return run_mod.cmd_scheduler(args)

    def test_noop_green(self, monkeypatch):
        assert self._exit(monkeypatch, "NOOP") == 0

    def test_blocked_red(self, monkeypatch):
        assert self._exit(monkeypatch, "BLOCKED",
                          verdict="Course k is BLOCKED; cooldown ...") == 1

    def test_error_red(self, monkeypatch):
        assert self._exit(monkeypatch, "ERROR",
                          error="Resolve failed: boom") == 1

    def test_run_success_green(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="SUCCESS", passed=True) == 0

    def test_run_success_not_passed_red(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="SUCCESS", passed=False) == 1

    def test_run_failed_red(self, monkeypatch):
        assert self._exit(monkeypatch, "RUN", result="FAILED", passed=False) == 1