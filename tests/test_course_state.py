"""Unit tests for Persistent Course State (E5)."""
import json
import pytest
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
from state.course_state import (
    CourseState,
    CourseIdentity,
    CourseProgress,
    CourseStatus,
    load_active_course,
    load_course_state,
    save_course_state,
    activate_course,
    archive_course,
    update_state_after_run,
    get_courses_list,
    initialize_course,
    run_course,
    STATE_DIR,
    ACTIVE_FILE,
    COURSES_DIR,
)


# ── Fixtures ──────────────────────────────────────────────────────
@pytest.fixture
def sample_identity():
    return CourseIdentity(
        course_id="265997861",
        clazz_id="151695658",
        cpi="506830460",
        title="计算机网络-2025级",
        raw_url="https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=test&mooc2=1",
        resolved_at_utc="2026-09-03T10:00:00Z",
    )


@pytest.fixture
def sample_state(sample_identity):
    return CourseState(
        schema_version=1,
        course_identity=sample_identity,
        status="ACTIVE",
        progress=CourseProgress(completed=11, total=102),
        last_run="2026-09-03T10:00:00Z",
        last_success="2026-09-03T10:00:00Z",
        run_count=5,
        success_count=4,
        failure_count=1,
    )


@pytest.fixture
def state_tmp_dir(tmp_path):
    """使用临时目录替代真实 state/ 目录。"""
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"):
        yield tmp_path


# ── Tests: CourseState serialization ──────────────────────────────
class TestCourseStateSerialization:
    def test_to_dict_roundtrip(self, sample_state):
        d = sample_state.to_dict()
        restored = CourseState.from_dict(d)
        assert restored.course_identity.key() == sample_state.course_identity.key()
        assert restored.status == sample_state.status
        assert restored.run_count == sample_state.run_count
        assert restored.success_count == sample_state.success_count
        assert restored.progress.completed == 11

    def test_minimal_state(self):
        state = CourseState()
        d = state.to_dict()
        assert d["schema_version"] == 1
        assert d["status"] == "NEW"
        assert d["run_count"] == 0

    def test_history_truncation(self):
        state = CourseState()
        for i in range(60):
            state.history.append({"i": i})
        d = state.to_dict()
        assert len(d["history"]) == 50  # 最多保留 50 条


# ── Tests: save / load ────────────────────────────────────────────
class TestStatePersistence:
    def test_save_and_load(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        loaded = load_course_state(sample_state.course_identity.key())
        assert loaded is not None
        assert loaded.course_identity.key() == sample_state.course_identity.key()
        assert loaded.status == "ACTIVE"

    def test_load_nonexistent(self, state_tmp_dir):
        loaded = load_course_state("999_999")
        assert loaded is None

    def test_save_without_identity_raises(self, state_tmp_dir):
        state = CourseState()  # 无 course_identity
        with pytest.raises(ValueError):
            save_course_state(state)

    def test_atomic_write(self, sample_state, state_tmp_dir):
        """验证原子写入：临时文件 rename 后原文件存在。"""
        save_course_state(sample_state)
        key = sample_state.course_identity.key()
        assert (state_tmp_dir / "state" / "courses" / f"{key}.json").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows permission model differs")
    def test_state_dir_mode(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        state_dir = state_tmp_dir / "state"
        courses_dir = state_dir / "courses"
        # 权限应被设置（仅 Unix）
        assert courses_dir.stat().st_mode & 0o777 == 0o700


# ── Tests: activate_course ────────────────────────────────────────
class TestActivateCourse:
    def test_activate_new_course(self, sample_identity, state_tmp_dir):
        activate_course(sample_identity)
        active = load_active_course()
        assert active is not None
        assert active.key() == sample_identity.key()

        state = load_course_state(sample_identity.key())
        assert state is not None
        assert state.status == "ACTIVE"

    def test_activate_switch_course(self, sample_identity, state_tmp_dir):
        """激活新课程应归档旧课程。"""
        old_iden = CourseIdentity(
            course_id="111111111", clazz_id="222222222",
            cpi="333333333", title="旧课程",
            raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        )
        activate_course(old_iden)
        activate_course(sample_identity)

        # 旧课程应归档
        old_state = load_course_state(old_iden.key())
        assert old_state is not None
        assert old_state.status == "ARCHIVED"

        # 新课程应活跃
        new_state = load_course_state(sample_identity.key())
        assert new_state is not None
        assert new_state.status == "ACTIVE"

    def test_activate_restores_archived(self, sample_identity, state_tmp_dir):
        """重新激活已归档课程应恢复为 ACTIVE。"""
        activate_course(sample_identity)
        archive_course(sample_identity)
        state = load_course_state(sample_identity.key())
        assert state.status == "ARCHIVED"

        activate_course(sample_identity)
        state = load_course_state(sample_identity.key())
        assert state.status == "ACTIVE"

    def test_activate_preserves_progress(self, sample_state, state_tmp_dir):
        """激活已有进度的课程不应重置。"""
        save_course_state(sample_state)
        activate_course(sample_state.course_identity)
        restored = load_course_state(sample_state.course_identity.key())
        assert restored.progress.completed == 11
        assert restored.run_count == 5


# ── Tests: archive_course ─────────────────────────────────────────
class TestArchiveCourse:
    def test_archive(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        archive_course(sample_state.course_identity)
        state = load_course_state(sample_state.course_identity.key())
        assert state.status == "ARCHIVED"

    def test_archive_nonexistent_no_error(self, state_tmp_dir):
        archive_course(CourseIdentity(
            course_id="999", clazz_id="999", cpi="", title="",
            raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        ))
        # 不应报错


# ── Tests: update_state_after_run ─────────────────────────────────
class TestUpdateStateAfterRun:
    def test_success_updates_state(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        updated = load_course_state(sample_state.course_identity.key())
        update_state_after_run(updated, passed=True, timing_s=792.0,
                               chapter_id="1217304712", verdict="PASS")
        assert updated.run_count == 6
        assert updated.success_count == 5
        assert updated.last_completed_task == "1217304712"
        assert updated.last_success is not None
        assert updated.active_task is None
        assert len(updated.history) == 1
        assert updated.history[0]["passed"] is True
        save_course_state(updated)

    def test_failure_updates_state(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        updated = load_course_state(sample_state.course_identity.key())
        update_state_after_run(updated, passed=False, timing_s=85.0,
                               chapter_id="1217304708", verdict="FAIL")
        assert updated.run_count == 6
        assert updated.failure_count == 2
        assert updated.last_failure is not None
        assert updated.active_task == "1217304708"
        save_course_state(updated)

    def test_three_failures_blocks(self, state_tmp_dir):
        identity = CourseIdentity(
            course_id="265997861", clazz_id="151695658",
            cpi="506830460", title="test", raw_url="",
            resolved_at_utc="2026-01-01T00:00:00Z",
        )
        state = CourseState(course_identity=identity, status="ACTIVE",
                            failure_count=2)
        save_course_state(state)
        updated = load_course_state(identity.key())
        update_state_after_run(updated, passed=False, timing_s=43.0,
                               chapter_id="1217304706", verdict="FAIL")
        assert updated.status == "BLOCKED"
        save_course_state(updated)


# ── Tests: initialize_course / run_course ─────────────────────────
class TestConvenienceAPI:
    def test_initialize_course(self, sample_identity, state_tmp_dir):
        state = initialize_course(sample_identity)
        assert state.status == "ACTIVE"
        assert state.course_identity.key() == sample_identity.key()
        active = load_active_course()
        assert active.key() == sample_identity.key()

    def test_run_course_creates_if_needed(self, sample_identity, state_tmp_dir):
        state = run_course(sample_identity, "1217304706",
                           passed=True, timing_s=792.0, verdict="PASS")
        assert state.run_count == 1
        assert state.success_count == 1


# ── Tests: get_courses_list ───────────────────────────────────────
class TestGetCoursesList:
    def test_empty_list(self, state_tmp_dir):
        result = get_courses_list()
        assert result == []

    def test_list_with_courses(self, sample_state, state_tmp_dir):
        save_course_state(sample_state)
        result = get_courses_list()
        assert len(result) == 1
        assert result[0]["key"] == sample_state.course_identity.key()
        assert result[0]["status"] == "ACTIVE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
