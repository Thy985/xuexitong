"""环境/会话级失败熔断 —— 系统级故障不得伪装成章节级 BLOCKED（2026-10-06 定案）。

用户报障链路：登录风控/会话被踢这类环境问题曾一路计入章节 consecutive_failures，
3 次后该章 BLOCKED，调度器跳下一章 → 下一章同样环境失败 → 又 BLOCKED……
最终整门课被逐章冻住、零播放成功，用户看到的就是「一直下一章、视频不放」。

修复后语义：
  - ENV 类失败（failure_class_policy）→ 子进程不 mark_failed；父进程把本轮
    终止为 decision=ERROR / result=ENV_FAILED，loop 侧 30 分钟退避 + 连续 5 轮退出。
  - CHAPTER 类失败 → 原路径（mark_failed / cf+1 / 3 次 BLOCKED）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from state.course_state import (
    CourseState, CourseIdentity, CourseProgress, initialize_course,
    save_course_state,
)

KEY = "265997861_151695658"
CID = "1217304719"
TID = CID


@pytest.fixture
def act(monkeypatch, tmp_path):
    """临时 state 根 + ACTIVE 课程；registry 真实读写（不 mock 业务层）。"""
    from app.registry import task_registry as tr
    monkeypatch.delenv("CX_USER", raising=False)
    monkeypatch.delenv("CX_PASS", raising=False)
    monkeypatch.delenv("XUE_VIDEO_DURATION_S", raising=False)
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"), \
         patch.object(tr, "TASKS_DIR", tmp_path / "state" / "registry"):
        identity = CourseIdentity(
            course_id="265997861", clazz_id="151695658", cpi="506830460",
            title="计算机网络", raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        )
        initialize_course(identity)
        save_course_state(CourseState(
            course_identity=identity, status="ACTIVE",
            progress=CourseProgress(completed=0, total=None,
                                    last_completed_task=None, active_task=None)))
        yield identity


def _seed_pending_task():
    """registry 里一章一条 PENDING video 任务（本章尚未失败过）。"""
    from app.registry.task_registry import TaskRecord, save_registry, load_registry
    reg = {TID: TaskRecord(task_id=TID, chapter_id=CID, title="第3章",
                           task_type="video", status="PENDING", priority=0)}
    save_registry(KEY, reg)
    return reg


def _env_failure_ev():
    return {"passed": False, "verdict": "FAIL(session kicked during playback)",
            "runtime_evidence": {"failure_stage": "SESSION_KICKED"},
            "failure_stage": "SESSION_KICKED",
            "exit_code": 1, "timing_s": 1.0, "timed_out": False}


def _chapter_failure_ev():
    return {"passed": False, "verdict": "FAIL — passed=0/10(obs), max_ct=0s",
            "runtime_evidence": {"failure_stage": "PLAYBACK_STALLED"},
            "failure_stage": "PLAYBACK_STALLED",
            "exit_code": 1, "timing_s": 1.0, "timed_out": False}


# ── 父进程侧：run_scheduler 的轮级熔断 ──────────────────────────────

class TestSchedulerEnvCircuit:
    def test_env_failure_stops_round_as_error(self, act, monkeypatch):
        """环境失败 → decision=ERROR / result=ENV_FAILED,不烧章节与课程失败预算。"""
        from scheduler import scheduler as sched
        from app.registry.task_registry import load_registry

        _seed_pending_task()
        probe_calls, run_calls = [], []

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            probe_calls.append(1)
            return TID

        def fake_run_one(*a, **k):
            run_calls.append(1)
            return _env_failure_ev()

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)

        out = sched.run_scheduler(course_url="", trigger="schedule",
                                  run_id="t-env", max_chapters=3)

        # 本轮立刻终止,不继续烧后续章
        assert out.decision == "ERROR"
        assert out.result == "ENV_FAILED"
        assert out.chapters_env_failed == [TID]
        assert out.chapters_failed == []
        assert len(run_calls) == 1, "环境失败后不得继续投递后续章"
        assert len(probe_calls) == 1, "环境失败后不得再探测下一候选"
        # 课程级失败预算不受污染（record_result 只对 FAILED 计 cf）
        assert sched.load_scheduler_state(KEY).consecutive_failures == 0
        # 任务原状保留（真实 mark_failed 在子进程,而子进程对 ENV 也不记 —— 见下）
        task = load_registry(KEY).get(TID)
        assert task is not None and task.status == "PENDING"
        assert task.consecutive_failures == 0

    def test_chapter_failure_keeps_legacy_path(self, act, monkeypatch):
        """内容级失败(PLAYBACK_STALLED) → 原路径:FAILED 聚合,decision 仍 RUN。"""
        from scheduler import scheduler as sched

        _seed_pending_task()

        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            return TID

        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        monkeypatch.setattr(sched, "_run_one_chapter", lambda *a, **k: _chapter_failure_ev())

        out = sched.run_scheduler(course_url="", trigger="schedule",
                                  run_id="t-ch", max_chapters=3)

        assert out.decision == "RUN"
        assert out.result == "FAILED"
        assert out.chapters_failed == [TID]
        assert out.chapters_env_failed == []
        assert sched.load_scheduler_state(KEY).consecutive_failures == 1

    def test_success_still_wins_over_env_failure(self, act, monkeypatch):
        """同轮先成功一章、再遇环境失败 → 聚合 SUCCESS,decision=ERROR 退避。"""
        from scheduler import scheduler as sched
        from app.registry.task_registry import TaskRecord, save_registry

        _seed_pending_task()
        cid2 = "1217304720"
        save_registry(KEY, {
            TID: TaskRecord(task_id=TID, chapter_id=CID, title="第3章",
                            task_type="video", status="PENDING", priority=0),
            cid2: TaskRecord(task_id=cid2, chapter_id=cid2, title="第4章",
                             task_type="video", status="PENDING", priority=1),
        })
        evs = [  # 章序与 probe 返回序对齐
            {"passed": True, "verdict": "PASS", "runtime_evidence": {},
             "failure_stage": None, "exit_code": 0, "timing_s": 1.0,
             "timed_out": False},
            _env_failure_ev(),
        ]
        probe_seq = iter([TID, cid2])
        monkeypatch.setattr(sched, "_run_tdvp_probe",
                            lambda *a, **k: next(probe_seq))
        monkeypatch.setattr(sched, "_run_one_chapter", lambda *a, **k: evs.pop(0))

        out = sched.run_scheduler(course_url="", trigger="schedule",
                                  run_id="t-mix", max_chapters=2)

        assert out.result == "SUCCESS"
        assert out.decision == "ERROR"
        assert out.chapters_env_failed == [cid2]
        assert out.chapters_attempted == [TID, cid2]


# ── 子进程侧：app.run cmd_run 的 registry 标记 ───────────────────────

def _run_cmd_run(monkeypatch, tmp_path, ev, out_path):
    """驱动 app.run.cmd_run 走一次失败 run（run_test 打桩,registry 真实）。"""
    import app.run as run_mod
    from app.registry import task_registry as tr

    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"), \
         patch.object(tr, "TASKS_DIR", tmp_path / "state" / "registry"), \
         patch.object(run_mod, "parse_course_url",
                      lambda url: {"course_id": "265997861", "clazz_id": "151695658",
                                   "cpi": "506830460", "enc": "abc",
                                   "chapter_id": CID, "openc": "777136",
                                   "hidetype": "0"}), \
         patch.object(run_mod, "load_active_course",
                      lambda: SimpleNamespace(
                          course_id="265997861", clazz_id="151695658",
                          cpi="506830460", title="计算机网络", raw_url="",
                          resolved_at_utc="2026-01-01T00:00:00Z")), \
         patch.object(run_mod, "run_test", lambda *a, **k: dict(ev)):
        args = SimpleNamespace(
            course_url="http://x", chapter_id=CID, output=str(out_path),
            xvfb_display=":99", debug_capture=False, video_index=0,
            max_attempts=2)
        return run_mod.cmd_run(args)


class TestChildDoesNotBurnChapterBudgetOnEnvFailure:
    def test_env_failure_skips_mark_failed(self, act, monkeypatch, tmp_path):
        """SESSION_KICKED → 任务保持 PENDING、cf 不增（BLOCKED 不会因此产生）。"""
        from app.registry.task_registry import load_registry
        _seed_pending_task()
        ev = {"verdict": "FAIL(session kicked during playback)",
              "failure_stage": "SESSION_KICKED", "business_verdict": None,
              "passed_object_ids": [], "passed_count": 0}
        code = _run_cmd_run(monkeypatch, tmp_path, ev, tmp_path / "env.json")
        assert code == 1
        task = load_registry(KEY)[TID]
        assert task.status == "PENDING"
        assert task.consecutive_failures == 0
        assert task.failure.stage == ""

    def test_chapter_failure_still_marks_failed(self, act, monkeypatch, tmp_path):
        """对照：内容级失败照常 mark_failed（cf+1,3 次才 BLOCKED）。"""
        from app.registry.task_registry import load_registry
        _seed_pending_task()
        ev = {"verdict": "FAIL — passed=0/10(obs), max_ct=0s",
              "failure_stage": "PLAYBACK_STALLED", "business_verdict": None,
              "passed_object_ids": [], "passed_count": 0}
        code = _run_cmd_run(monkeypatch, tmp_path, ev, tmp_path / "ch.json")
        assert code == 1
        task = load_registry(KEY)[TID]
        assert task.status == "FAILED"
        assert task.consecutive_failures == 1
        assert task.failure.stage == "PLAYBACK_STALLED"

    def test_crash_evidence_counts_as_env(self, act, monkeypatch, tmp_path):
        """子进程崩溃（无 stage,verdict=CRASH）→ 环境类,不烧章节预算。"""
        from app.registry.task_registry import load_registry
        _seed_pending_task()
        ev = {"verdict": "CRASH", "failure_stage": None,
              "business_verdict": None, "passed_object_ids": [],
              "passed_count": 0, "errors": ["RuntimeError: boom"]}
        code = _run_cmd_run(monkeypatch, tmp_path, ev, tmp_path / "crash.json")
        assert code == 1
        task = load_registry(KEY)[TID]
        assert task.status == "PENDING"
        assert task.consecutive_failures == 0
