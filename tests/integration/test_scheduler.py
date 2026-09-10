"""Unit tests for E6 Scheduler."""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
from scheduler.scheduler import (
    determine_action,
    record_result,
    load_scheduler_state,
    get_scheduler_summary,
    generate_actions_summary,
    SchedulerState,
    ExecutionResult,
)
from state.course_state import (
    CourseState, CourseIdentity, CourseProgress,
    initialize_course, save_course_state,
)


@pytest.fixture
def tmp_state_dir(tmp_path):
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"):
        yield tmp_path


@pytest.fixture
def sample_identity():
    return CourseIdentity(
        course_id="265997861", clazz_id="151695658",
        cpi="506830460", title="计算机网络",
        raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
    )


# ── Tests: determine_action ───────────────────────────────────────
class TestDetermineAction:
    def test_no_active_course(self):
        dec, reason = determine_action(None, "schedule")
        assert dec == "NOOP"
        assert "No active course" in reason

    def test_blocked_course(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        state = CourseState(course_identity=sample_identity, status="BLOCKED")
        save_course_state(state)
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "BLOCKED"
        assert "BLOCKED" in reason

    def test_archived_course(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        from state.course_state import archive_course
        archive_course(sample_identity)
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "NOOP"
        assert "ARCHIVED" in reason

    def test_consecutive_failures_blocks(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        ss = SchedulerState(consecutive_failures=3)
        from scheduler.scheduler import save_scheduler_state
        save_scheduler_state(sample_identity.key(), ss)
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "BLOCKED"
        assert "consecutive failures" in reason.lower()

    def test_ready_to_run(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "RUN"
        assert "ready to run" in reason.lower()

    # ── P0: BLOCKED cooldown / auto-retry ──────────────────────────
    def test_manual_overrides_blocked_immediately(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        state = CourseState(course_identity=sample_identity, status="BLOCKED")
        save_course_state(state)
        dec, reason = determine_action(sample_identity.key(), "manual")
        assert dec == "RUN"
        assert "manual override" in reason.lower()
        # manual 不消费 blocked_hits
        from scheduler.scheduler import load_scheduler_state as _ls
        assert _ls(sample_identity.key()).blocked_hits == 0

    def test_blocked_schedule_cooldown_counts(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        state = CourseState(course_identity=sample_identity, status="BLOCKED")
        save_course_state(state)
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "BLOCKED"
        assert "1/4" in reason
        from scheduler.scheduler import load_scheduler_state as _ls
        assert _ls(sample_identity.key()).blocked_hits == 1

    def test_blocked_schedule_retries_after_interval(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        state = CourseState(course_identity=sample_identity, status="BLOCKED")
        save_course_state(state)
        # 前 3 次 schedule → BLOCKED；第 4 次 → 自动 retry RUN
        for i in range(3):
            dec, _ = determine_action(sample_identity.key(), "schedule")
            assert dec == "BLOCKED"
        dec, reason = determine_action(sample_identity.key(), "schedule")
        assert dec == "RUN"
        assert "cooldown expired" in reason
        from scheduler.scheduler import load_scheduler_state as _ls
        assert _ls(sample_identity.key()).blocked_hits == 0

    def test_course_status_returns_actived_after_reset(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        # 模拟之前进入过 BLOCKED（有残余计数），恢复后应清零
        from scheduler.scheduler import save_scheduler_state, SchedulerState
        save_scheduler_state(sample_identity.key(),
                             SchedulerState(blocked_since="2026-01-01T00:00:00Z",
                                            blocked_hits=2))
        dec, _ = determine_action(sample_identity.key(), "schedule")
        assert dec == "RUN"
        from scheduler.scheduler import load_scheduler_state as _ls
        ss = _ls(sample_identity.key())
        assert ss.blocked_since is None
        assert ss.blocked_hits == 0


# ── Tests: record_result ──────────────────────────────────────────
class TestRecordResult:
    def test_success_resets_failures(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        # 先设一个失败
        ss = SchedulerState(consecutive_failures=2)
        from scheduler.scheduler import save_scheduler_state
        save_scheduler_state(sample_identity.key(), ss)

        result = ExecutionResult(
            decision="RUN", result="SUCCESS", trigger="schedule",
            course_key=sample_identity.key(), run_id="123",
            timing_s=100.0, passed=True, verdict="PASS",
        )
        record_result(sample_identity.key(), result)

        loaded = load_scheduler_state(sample_identity.key())
        assert loaded.consecutive_failures == 0
        assert loaded.last_result == "SUCCESS"

    def test_failure_increments(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        result = ExecutionResult(
            decision="RUN", result="FAILED", trigger="schedule",
            course_key=sample_identity.key(), run_id="124",
            timing_s=50.0, passed=False, verdict="FAIL",
        )
        record_result(sample_identity.key(), result)
        loaded = load_scheduler_state(sample_identity.key())
        assert loaded.consecutive_failures == 1
        assert loaded.last_result == "FAILED"

    def test_three_failures_blocks_next(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        for i in range(3):
            record_result(sample_identity.key(), ExecutionResult(
                decision="RUN", result="FAILED", trigger="schedule",
                course_key=sample_identity.key(), run_id=f"fail_{i}",
                timing_s=50.0, passed=False, verdict="FAIL",
            ))
        dec, _ = determine_action(sample_identity.key(), "schedule")
        assert dec == "BLOCKED"


# ── Tests: get_scheduler_summary ──────────────────────────────────
class TestSchedulerSummary:
    def test_no_active_course_summary(self):
        s = get_scheduler_summary(None, "NOOP", "No active course")
        assert s["decision"] == "NOOP"
        # course 可能为 None 或空字符串，不强制 N/A
        assert s.get("decision") == "NOOP"

    def test_with_course_summary(self, sample_identity, tmp_state_dir):
        initialize_course(sample_identity)
        s = get_scheduler_summary(sample_identity.key(), "RUN", "test")
        assert s["course"] == "计算机网络"
        assert s["identity"] == sample_identity.key()
        assert s["status"] == "ACTIVE"


# ── Tests: generate_actions_summary ───────────────────────────────
class TestActionsSummary:
    def test_blocked_summary(self):
        md = generate_actions_summary({
            "trigger": "schedule", "decision": "BLOCKED",
            "reason": "3 consecutive failures",
            "course": "测试课程", "identity": "123_456",
            "consecutive_failures": 3,
        })
        assert "BLOCKED" in md
        assert "Manual intervention" in md

    def test_noop_summary(self):
        md = generate_actions_summary({
            "trigger": "schedule", "decision": "NOOP",
            "reason": "No active course",
        })
        assert "NOOP" in md
        assert "initialized" in md.lower()


# ── P1: run_scheduler 多章循环 ───────────────────────────────────
class TestRunSchedulerMultiChapter:
    def test_runs_multiple_chapters(self, sample_identity, tmp_state_dir, monkeypatch):
        initialize_course(sample_identity)
        save_course_state(CourseState(course_identity=sample_identity,
                                      status="ACTIVE"))
        # tmp_state_dir 已把 state 目录指到 tmp；load_active_course 读到 sample_identity。
        from scheduler import scheduler as sched
        # probe 依次返回两个章，第三轮返回 None（队列空）
        probe_calls = {"n": 0}
        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            seq = ["1217304701", "1217304702"]
            if probe_calls["n"] >= len(seq):
                return None
            ch = seq[probe_calls["n"]]
            probe_calls["n"] += 1
            return ch
        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        def fake_run_one(course_url, chapter_id, task_id, trigger, run_id, video_index=0, max_s=900):
            return {"passed": True, "verdict": "PASS",
                    "runtime_evidence": {"verdict": "PASS"},
                    "failure_stage": None, "exit_code": 0,
                    "timing_s": 1.0, "timed_out": False}
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)
        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="100", max_chapters=2)
        assert out.decision == "RUN"
        assert out.result == "SUCCESS"
        assert out.chapters_attempted == ["1217304701", "1217304702"]
        assert out.chapters_failed == []

    def test_stops_when_probe_returns_none(self, sample_identity, tmp_state_dir, monkeypatch):
        initialize_course(sample_identity)
        save_course_state(CourseState(course_identity=sample_identity,
                                      status="ACTIVE"))
        from scheduler import scheduler as sched
        monkeypatch.setattr(sched, "_run_tdvp_probe", lambda *a, **k: None)
        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="100", max_chapters=5)
        assert out.decision == "NOOP"  # 无 pending 任务
        assert out.chapters_attempted == []

    # 多章 re-probe 去重：确认循环把「已处理章节」传给 _run_tdvp_probe 的 exclude_chapters
    def test_reprobe_receives_excluded_set(self, sample_identity, tmp_state_dir, monkeypatch):
        initialize_course(sample_identity)
        save_course_state(CourseState(course_identity=sample_identity,
                                      status="ACTIVE"))
        probe_log = []
        calls = {"n": 0}
        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            probe_log.append((course_key, run_id, set(exclude_chapters or [])))
            seq = ["1217304701", "1217304702"]
            if calls["n"] >= len(seq):
                return None
            ch = seq[calls["n"]]; calls["n"] += 1
            return ch
        from scheduler import scheduler as sched
        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        def fake_run_one(course_url, chapter_id, task_id, trigger, run_id, video_index=0, max_s=900):
            return {"passed": True, "verdict": "PASS",
                    "runtime_evidence": {}, "failure_stage": None,
                    "exit_code": 0, "timing_s": 1.0, "timed_out": False}
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)
        sched.run_scheduler(course_url="", trigger="manual",
                            run_id="100", max_chapters=3)
        # 第一次 probe: exclude 为空；第二次 probe: 排除已处理过的 {1217304701}
        assert probe_log[0][2] == set()          # 首轮无排除
        assert probe_log[1][2] == {"1217304701"}  # re-probe 排除首章
        assert probe_log[-1][2] == {"1217304701", "1217304702"}

    # TIMEOUT 语义：watchdog 超时的章应进 chapters_timed_out、计为失败，
    # 但单章超时后循环仍可推进下一章（而不是整场卡死）。
    def test_timeout_chapter_advances_queue(self, sample_identity, tmp_state_dir, monkeypatch):
        initialize_course(sample_identity)
        save_course_state(CourseState(course_identity=sample_identity,
                                      status="ACTIVE"))
        calls = {"n": 0}
        def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
            seq = ["1217304701", "1217304702"]
            if calls["n"] >= len(seq):
                return None
            ch = seq[calls["n"]]; calls["n"] += 1
            return ch
        from scheduler import scheduler as sched
        monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
        def fake_run_one(course_url, chapter_id, task_id, trigger, run_id, video_index=0, max_s=900):
            if chapter_id == "1217304701":
                # 4701 超时（watchdog 判死）
                return {"passed": False, "verdict": "TIMEOUT",
                        "runtime_evidence": {}, "failure_stage": "watchdog_timeout",
                        "exit_code": 124, "timing_s": max_s, "timed_out": True}
            return {"passed": True, "verdict": "PASS", "runtime_evidence": {},
                    "failure_stage": None, "exit_code": 0, "timing_s": 1.0,
                    "timed_out": False}
        monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)
        out = sched.run_scheduler(course_url="", trigger="manual",
                                  run_id="100", max_chapters=2)
        assert out.decision == "RUN"
        assert out.chapters_timed_out == ["1217304701"]
        assert out.chapters_failed == ["1217304701"]   # TIMEOUT 会计为失败
        # 4702 成功 → 任一成功即 SUCCESS（部分推进）
        assert out.result == "SUCCESS"
        assert out.chapters_attempted == ["1217304701", "1217304702"]

    def test_apply_excluded_filters_same_chapter(self, tmp_state_dir):
        from scheduler import scheduler as sched
        # 展示队列 items 里有重复同章（本轮已处理）时应被剔除
        items = [
            {"task_id": "a", "chapter_id": "111", "priority": 0, "state": "READY", "course_key": "k"},
            {"task_id": "b", "chapter_id": "111", "priority": 1, "state": "READY", "course_key": "k"},
            {"task_id": "c", "chapter_id": "222", "priority": 2, "state": "READY", "course_key": "k"},
        ]
        # 构造 ExecutionQueue（借用 reconcile_queue 在不存在的课程上的空返回不可靠，
        # 这里直接用一个最小的有 items 属性的容器）
        class _Q:
            pass
        q = _Q(); q.items = list(items)
        filtered = sched._apply_excluded(q, {"111"})
        assert [i["task_id"] for i in filtered] == ["c"]      # 111 被剔除
        # 无排除时原样
        assert len(sched._apply_excluded(q, None)) == 3

    def test_apply_excluded_per_task_keeps_next_video(self, tmp_state_dir):
        """Options B：排除按「已完成 task_id」，不能把同章的下一个视频段一起排掉。

        4706 是多视频章（task 有 <cid>、<cid>:video2、...）。跑完 <cid>(video1)后，
        exclude set 里是 "4706"，但 _apply_excluded 必须保留 "4706:video2"。
        """
        from scheduler import scheduler as sched
        items = [
            {"task_id": "4706",       "chapter_id": "4706", "priority": 0, "state": "READY", "course_key": "k"},
            {"task_id": "4706:video2", "chapter_id": "4706", "priority": 1, "state": "READY", "course_key": "k"},
            {"task_id": "4706:video3", "chapter_id": "4706", "priority": 2, "state": "READY", "course_key": "k"},
        ]
        class _Q:
            pass
        q = _Q(); q.items = list(items)
        # 排除整章"4706"→ 只有基视频被剔除，剩下的 video2/video3 保留
        keep = sched._apply_excluded(q, {"4706"})
        assert [i["task_id"] for i in keep] == ["4706:video2", "4706:video3"]

    def test_split_video_target(self):
        from scheduler import scheduler as sched
        assert sched._split_video_target("4706") == ("4706", 1)
        assert sched._split_video_target("4706:video3") == ("4706", 3)
        assert sched._split_video_target("") == ("", 1)


class TestFallbackChapter:
    """目录抓空 / 探测异常时，_fallback_chapter 必须遵守 exclude_chapters。"""

    def test_url_chapter_returned_when_not_excluded(self, monkeypatch):
        from scheduler import scheduler as sched
        monkeypatch.setattr("resolvers.course_resolver._parse_url_params",
                            lambda url: {"chapter_id": "1217304706"})
        out = sched._fallback_chapter("http://x?chapterId=1217304706", "k", None)
        assert out == "1217304706"

    def test_url_chapter_excluded_falls_back_to_registry(self, monkeypatch):
        from scheduler import scheduler as sched
        monkeypatch.setattr("resolvers.course_resolver._parse_url_params",
                            lambda url: {"chapter_id": "1217304706"})
        # 一个可被 _fallback_chapter 当作真实 registry 记录的对象
        class _Rec:
            task_type = "video"
            status = "DISCOVERED"
            chapter_id = "1217304719"
            consecutive_failures = 0
            max_attempts = 3
        monkeypatch.setattr("e6.task_registry.load_registry",
                            lambda key: {"4719": _Rec()})
        monkeypatch.setattr("e6.task_registry.done_chapter_ids_from_registry",
                            lambda reg: set())
        class _Q:
            items = [{"task_id": "4719", "chapter_id": "1217304719",
                      "priority": 0, "state": "READY", "course_key": "k"}]
        monkeypatch.setattr("e6.task_registry.reconcile_queue",
                            lambda *a, **k: _Q())
        out = sched._fallback_chapter("http://x?chapterId=1217304706",
                                      "k", {"1217304706"})
        assert out == "1217304719"

    def test_all_excluded_returns_none(self, monkeypatch):
        from scheduler import scheduler as sched
        monkeypatch.setattr("resolvers.course_resolver._parse_url_params",
                            lambda url: {"chapter_id": "1217304706"})
        monkeypatch.setattr("e6.task_registry.load_registry",
                            lambda key: None)
        out = sched._fallback_chapter("http://x?chapterId=1217304706",
                                      "k", {"1217304706"})
        assert out is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
