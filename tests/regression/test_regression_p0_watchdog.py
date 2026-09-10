"""P0-01 + P0-07：真实 subprocess watchdog + TIMEOUT 写回。

历史事故链（同一防挂保护）：
    子进程卡死 → watchdog → TIMEOUT → 状态写回 → scheduler 可恢复

Regression: run 34311891898
Failure mode: cmd_run → run_test 的 Playwright 播放循环在真站下可能**永不退出**；
    若同步调用，外层循环即使有 budget 也拦不住一次 cmd_run 内部永久阻塞。
Expected invariant:
    watchdog 用真实子进程 + 墙钟硬上限；卡死 → kill 进程树 → exit 124 → verdict=TIMEOUT
    → 会计为失败（不藏、不清 zero budget） → 下一轮调度可恢复（不留 RUNNING/VERIFYING 卡点）。
Evidence: ACTION_HISTORY_AUDIT.md (§C1) / REGRESSION_MATRIX P0-01 / P0-07

纪律：这里**不 mock 掉被测主体** —— 用真实 subprocess + 可注入的“假 app.run”子命令
（tests/fixtures/fake_app，注入 PYTHONPATH），让 watchdog 的真实
Popen(start_new_session) / wait(timeout) / killpg / exit-124 路径被真实执行。
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scheduler.scheduler import _run_one_chapter, run_scheduler
from scheduler.scheduler import load_scheduler_state
from state.course_state import (
    CourseState, CourseIdentity, initialize_course, save_course_state,
)

FAKE_APP = Path(__file__).resolve().parents[1] / "fixtures" / "fake_app"
REGRESSION_RUN = "34311891898"  # 事故 run（主循环永不退出）
FAKE_RUN = "1217304001"


def _run_child_env(monkeypatch, tmp_path):
    """让子进程 `python -m app.run` 解析到假 app。

    - monkeypatch.chdir(tmp_path)：子进程 cwd 无真实 `app/`，`-m app.run` 改由 PYTHONPATH 提供。
    - PYTHONPATH 前置 fake_app → 子进程 import 到 tests/fixtures/fake_app/app/run.py。
    - 不 launch 浏览器 / 不连真站。
    """
    monkeypatch.chdir(tmp_path)
    prev = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{FAKE_APP}{os.pathsep}{prev}")


@pytest.fixture
def child_env(monkeypatch, tmp_path):
    """同时配好假 app 环境 + 临时 state/registry。"""
    _run_fake_env_prep(monkeypatch, tmp_path)


# 便捷：让子进程解析到假 app
def _run_fake_env_prep(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    prev = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{FAKE_APP}{os.pathsep}{prev}")


@pytest.fixture
def state_and_fake(monkeypatch, tmp_path):
    """scoped fixture：临时 state 目录 + 假 app 子进程环境 + 激活课程。"""
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"):
        identity = CourseIdentity(
            course_id="265997861", clazz_id="151695658", cpi="506830460",
            title="计算机网络", raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        )
        initialize_course(identity)
        save_course_state(CourseState(course_identity=identity, status="ACTIVE"))
        _run_fake_env_prep(monkeypatch, tmp_path)
        yield identity


class TestWatchdogRealSubprocess:
    """P0-01：真实子进程卡死 → watchdog 必须超时终止（不是 mock 出来的 TIMEOUT）。"""

    def test_stuck_child_timesout_124(self, monkeypatch, tmp_path):
        _run_fake_env_prep(monkeypatch, tmp_path)
        monkeypatch.setenv("FAKE_RUN_BEHAVIOR", "stuck")  # 永不退出
        res = _run_one_chapter(
            "http://x?courseId=1&clazzId=2&cpi=3",
            "1217304701", task_id="1217304701", max_s=2)
        assert res["timed_out"] is True       # 卡死被墙钟判死
        assert res["exit_code"] == 124        # watchdog 超时退出码
        assert res["verdict"] == "TIMEOUT"    # 明确区别于 FAIL
        assert res["passed"] is False

    def test_fast_child_returns_pass(self, monkeypatch, tmp_path):
        _run_fake_env_prep(monkeypatch, tmp_path)
        monkeypatch.delenv("FAKE_RUN_BEHAVIOR", raising=False)  # 默认 exit0
        res = _run_one_chapter(
            "http://x?courseId=1&clazzId=2&cpi=3",
            "1217304701", task_id="1217304701", max_s=10)
        assert res["exit_code"] == 0
        assert res["verdict"] == "PASS"
        assert res["timed_out"] is False
        assert res["passed"] is True

    def test_exit1_child_returns_fail(self, monkeypatch, tmp_path):
        _run_fake_env_prep(monkeypatch, tmp_path)
        monkeypatch.setenv("FAKE_RUN_BEHAVIOR", "exit1")
        res = _run_one_chapter(
            "http://x?courseId=1&clazzId=2&cpi=3",
            "1217304701", task_id="1217304701", max_s=10)
        assert res["verdict"] == "FAIL"
        assert res["passed"] is False
        assert res["exit_code"] == 1


class TestP07TimeoutWriteback:
    """P0-07：TIMEOUT → 会计为失败（不藏、不清 budget）→ 下轮可恢复（不留 RUNNING 卡死）。"""

    def test_timeout_accounted_and_budget_not_cleared(
        self, state_and_fake, monkeypatch
    ):
        from scheduler import scheduler as sched

        identity = state_and_fake
        calls = {"n": 0}

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            # 首轮选该章；该章超时后续轮不再返回（模拟 registry 把它从队首清掉 / recover 可前进）
            ch = FAKE_RUN if calls["n"] == 0 else None
            calls["n"] += 1
            return ch

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setenv("FAKE_RUN_BEHAVIOR", "stuck")
        # 关键：run_scheduler 内部 watchdog 上限来自 XUE_CHAPTER_MAX_S（默认 900s）。
        # 不压小会导致本测试真等 900s —— 必须压到秒级。
        monkeypatch.setenv("XUE_CHAPTER_MAX_S", "2")
        monkeypatch.setenv("XUE_SCHEDULER_BUDGET_S", "30")

        out = sched.run_scheduler(
            course_url="",  # 用 ACTIVE course（state），不触发 resolve_course 网络路径
            trigger="manual", run_id="t7", max_chapters=1)

        assert out.chapters_timed_out == [FAKE_RUN]
        assert FAKE_RUN in out.chapters_failed
        # TIMEOUT 为失败语义 → 不作为干净 SUCCESS 清零失败 budget
        assert out.result == "FAILED"
        assert out.passed is False

        ss = load_scheduler_state(identity.key())
        # 失败会计已写回（假 SUCCESS 不会把它清零）
        assert ss.consecutive_failures >= 1
        assert ss.last_result == "FAILED"