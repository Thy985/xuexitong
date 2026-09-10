"""P0-05 + P0-06：多章 progression（dedup） + SUCCESS/FAIL 不得聚合成全局 SUCCESS。

── P0-05 ─────────────────────────────────────────────────────────
历史事故：考古 1217304706 重复选中 + 28ffdaf dedup 后仍暴露 e2 hang。
Regression: 28ffdaf（多章 re-probe 去重）
Failure mode: 同一运行内重复 re-probe 会把「已处理章」当作下一候选再次选中，
    多章不前进、复读同一章。
Expected invariant: A → 成功 → registry 更新 → re-probe 选 B → A 不再被选。
Assert：chapters_attempted 单调且无重复，首章只出现一次。

── P0-06 ─────────────────────────────────────────────────────────
Failure mode: 多章 A SUCCESS + B FAIL 时，原 `any_success` 归一为全局 SUCCESS，
    → 隐藏了 B 的失败 → 连续失败 budget 被清零（熔断被解除）。
Expected invariant: 任一章节真实 FAIL → 全局结果不得是干净 SUCCESS；
    失败不得被聚合 SUCCESS 掩盖；consecutive_failures 不被清零。

纪律：这里按需 mock 端口（_run_tdvp_probe / _run_one_chapter），
保持 scheduler 主循环、队列、registry/会计逻辑真实 —— 真实 watchdog
子进程已由 P0-01/P0-07 用 fake app 覆盖。
"""

from unittest.mock import patch

import pytest

from scheduler.scheduler import run_scheduler, load_scheduler_state
from state.course_state import (
    CourseState, CourseIdentity, initialize_course, save_course_state,
)

CH_A = "1217304701"
CH_B = "1217304702"


@pytest.fixture
def act(monkeypatch, tmp_path):
    """临时 state：激活一个课程。"""
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"):
        identity = CourseIdentity(
            course_id="265997861", clazz_id="151695658", cpi="506830460",
            title="计算机网络", raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        )
        initialize_course(identity)
        save_course_state(CourseState(course_identity=identity, status="ACTIVE"))
        yield identity


def _pass(ch):
    return {"passed": True, "verdict": "PASS", "runtime_evidence": {},
            "failure_stage": None, "exit_code": 0, "timing_s": 1.0, "timed_out": False}


def _fail_hard(ch):
    return {"passed": False, "verdict": "FAIL", "runtime_evidence": {},
            "failure_stage": "PLAYBACK", "exit_code": 1, "timing_s": 1.0,
            "timed_out": False}


class TestP05MultiChapterProgressionDedup:
    """P0-05：A 成功 → B 被选中 → A 不再被选（28ffdaf dedup 行为固化）。"""

    def test_a_then_b_no_repeats(self, act, monkeypatch):
        from scheduler import scheduler as sched

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            # 尊重 exclude：已处理的章不再返回（模拟 registry 完成态）
            for ch in (CH_A, CH_B):
                if ch not in set(exclude_chapters or []):
                    return ch
            return None

        order = []

        def fake_run_one(course_url, chapter_id, task_id, trigger, run_id,
                         video_index=0, max_s=900):
            order.append(task_id)
            return _pass(task_id)

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)

        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="p5", max_chapters=3)
        assert out.decision == "RUN"
        # A 只被跑一次，B 被跑；单调且无重复
        assert out.chapters_attempted == [CH_A, CH_B]
        assert out.chapters_attempted.count(CH_A) == 1
        assert len(out.chapters_attempted) == len(set(out.chapters_attempted))


class TestP06NoMaskedGlobalSuccess:
    """P0-6：A SUCCESS + B FAIL → 全局不得 SUCCESS；failure budget 不得清零。"""

    def test_b_fail_does_not_hide_failure(self, act, monkeypatch):
        from scheduler import scheduler as sched

        plan = {CH_A: _pass(CH_A), CH_B: _fail_hard(CH_B)}

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            for ch in (CH_A, CH_B):
                if ch not in set(exclude_chapters or []):
                    return ch
            return None

        def fake_run_one(course_url, chapter_id, task_id, trigger, run_id,
                         video_index=0, max_s=900):
            return plan[task_id or chapter_id]

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)

        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="p6", max_chapters=2)
        assert out.chapters_attempted == [CH_A, CH_B]
        assert CH_B in out.chapters_failed
        # 关键不变式：不能因为 A 成功就把 B 的失败归一成全局 SUCCESS
        assert out.result != "SUCCESS"
        assert out.passed is False
        # 失败预算未「被 SUCCESS 清零」
        assert load_scheduler_state(act.key()).consecutive_failures >= 1

    def test_all_pass_still_success(self, act, monkeypatch):
        from scheduler import scheduler as sched

        plan = {CH_A: _pass(CH_A), CH_B: _pass(CH_B)}

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            for ch in (CH_A, CH_B):
                if ch not in set(exclude_chapters or []):
                    return ch
            return None

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setattr(sched, "_run_one_chapter",
                            lambda *a, **k: _pass(k.get("task_id")))
        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="p6ok", max_chapters=2)
        # 全成功 → SUCCESS 是合理的
        assert out.result == "SUCCESS"
        assert out.chapters_failed == []
        assert out.decision == "RUN"