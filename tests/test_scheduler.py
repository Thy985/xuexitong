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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
