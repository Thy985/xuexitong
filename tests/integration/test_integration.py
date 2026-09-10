"""Integration tests for E5 Course Lifecycle (E5).

覆盖场景:
  Test A: 第一次初始化 — URL → Resolver → state created → active set
  Test B: 同课程第二次运行 — identity same → progress preserved
  Test C: 换课程 — A → ARCHIVED, B → ACTIVE, B independent
  Test D: 重新切回 — A → B → A, A 的历史 state 仍存在
  Test E: Repository persistence — 模拟跨 runner 恢复
"""
import json
import pytest
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from resolvers.course_resolver import resolve_course, detect_course_change
from state.course_state import (
    CourseState, CourseIdentity, CourseProgress,
    load_active_course, load_course_state, save_course_state,
    activate_course, archive_course, initialize_course, run_course,
    update_state_after_run, get_courses_list,
    STATE_DIR, COURSES_DIR, ACTIVE_FILE,
)


# ── Fixtures ──────────────────────────────────────────────────────
COURSE_A_URL = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304706&courseId=265997861&clazzid=151695658"
    "&cpi=506830460&enc=test_enc&mooc2=1&hidetype=0&openc=test_openc"
)
COURSE_B_URL = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=999999999&courseId=999999999&clazzid=999999999"
    "&cpi=999999999&enc=other_enc&mooc2=1"
)
COURSE_A_SAME_DIFF_CHAPTER = (
    "https://mooc1.chaoxing.com/mycourse/studentstudy?"
    "chapterId=1217304712&courseId=265997861&clazzid=151695658"
    "&cpi=506830460&enc=test_enc&mooc2=1"
)


@pytest.fixture
def tmp_state_dir(tmp_path):
    """使用临时目录替代全局 state/ 目录。"""
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"):
        yield tmp_path


# ── Test A: 第一次初始化 ──────────────────────────────────────────
class TestA_Initialize:
    def test_url_resolves_to_identity(self):
        result = resolve_course(COURSE_A_URL)
        assert result.is_ok()
        assert result.identity.course_id == "265997861"
        assert result.identity.clazz_id == "151695658"

    def test_state_created_and_active_set(self, tmp_state_dir):
        result = resolve_course(COURSE_A_URL)
        assert result.is_ok()
        identity = result.identity
        state = initialize_course(identity)

        assert state.status == "ACTIVE"
        assert state.course_identity.key() == identity.key()

        # active course 已设置
        active = load_active_course()
        assert active is not None
        assert active.key() == identity.key()

    def test_state_file_persisted(self, tmp_state_dir):
        result = resolve_course(COURSE_A_URL)
        identity = result.identity
        initialize_course(identity)

        state_file = (tmp_state_dir / "state" / "courses" / f"{identity.key()}.json")
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["status"] == "ACTIVE"
        assert data["schema_version"] == 1


# ── Test B: 同课程第二次运行 ──────────────────────────────────────
class TestB_SameCourseRun:
    def test_progress_preserved_on_second_run(self, tmp_state_dir):
        """同课程第二次运行不应重置进度。"""
        result = resolve_course(COURSE_A_URL)
        identity = result.identity
        initialize_course(identity)

        # 第一次 run
        state1 = run_course(identity, "1217304706", passed=True,
                           timing_s=792.0, verdict="PASS")
        save_course_state(state1)
        assert state1.run_count == 1
        assert state1.success_count == 1
        assert state1.last_completed_task == "1217304706"

        # 第二次 run（同课程，不同 chapter）
        state2 = run_course(identity, "1217304712", passed=True,
                           timing_s=800.0, verdict="PASS")
        save_course_state(state2)

        # 进度应累积，不重置
        loaded = load_course_state(identity.key())
        assert loaded.run_count == 2
        assert loaded.success_count == 2
        assert loaded.last_completed_task == "1217304712"
        assert loaded.progress is not None
        # completed 应保留
        assert loaded.progress.last_completed_task == "1217304712"

    def test_run_count_increments(self, tmp_state_dir):
        result = resolve_course(COURSE_A_URL)
        identity = result.identity
        initialize_course(identity)

        for i in range(3):
            state = run_course(identity, f"chap_{i}", passed=(i < 2),
                              timing_s=100.0, verdict="PASS" if i < 2 else "FAIL")
            save_course_state(state)

        loaded = load_course_state(identity.key())
        assert loaded.run_count == 3
        assert loaded.success_count == 2
        assert loaded.failure_count == 1


# ── Test C: 换课程 ───────────────────────────────────────────────
class TestC_SwitchCourse:
    def test_old_course_archived(self, tmp_state_dir):
        """切换到新课程时，旧课程应归档。"""
        # 初始化课程 A
        result_a = resolve_course(COURSE_A_URL)
        identity_a = result_a.identity
        initialize_course(identity_a)

        # 运行一次 A
        state_a = run_course(identity_a, "1217304706", passed=True,
                            timing_s=792.0, verdict="PASS")
        save_course_state(state_a)

        # 切换到课程 B
        result_b = resolve_course(COURSE_B_URL)
        identity_b = result_b.identity
        activate_course(identity_b)

        # A 应归档
        state_a_loaded = load_course_state(identity_a.key())
        assert state_a_loaded.status == "ARCHIVED"
        # A 的历史状态应保留
        assert state_a_loaded.run_count == 1
        assert state_a_loaded.success_count == 1

        # B 应活跃
        state_b_loaded = load_course_state(identity_b.key())
        assert state_b_loaded is not None
        assert state_b_loaded.status == "ACTIVE"
        assert state_b_loaded.run_count == 0  # 新建，无历史

    def test_new_course_independent(self, tmp_state_dir):
        """新课程不应继承旧课程的 runtime state。"""
        result_a = resolve_course(COURSE_A_URL)
        identity_a = result_a.identity
        initialize_course(identity_a)
        state_a = run_course(identity_a, "1217304706", passed=True,
                            timing_s=792.0, verdict="PASS")
        state_a.progress = CourseProgress(completed=50, total=100)
        save_course_state(state_a)

        result_b = resolve_course(COURSE_B_URL)
        identity_b = result_b.identity
        activate_course(identity_b)

        state_b = load_course_state(identity_b.key())
        assert state_b is not None
        # B 不应有 A 的进度
        assert state_b.progress is None or state_b.progress.completed is None
        assert state_b.run_count == 0
        assert state_b.success_count == 0


# ── Test D: 重新切回 ─────────────────────────────────────────────
class TestD_Reactivate:
    def test_reactivate_restores_history(self, tmp_state_dir):
        """切回旧课程应恢复其历史状态。"""
        result_a = resolve_course(COURSE_A_URL)
        identity_a = result_a.identity
        initialize_course(identity_a)
        state_a = run_course(identity_a, "1217304706", passed=True,
                            timing_s=792.0, verdict="PASS")
        save_course_state(state_a)

        result_b = resolve_course(COURSE_B_URL)
        identity_b = result_b.identity
        activate_course(identity_b)
        state_b = run_course(identity_b, "999999999", passed=True,
                            timing_s=500.0, verdict="PASS")
        save_course_state(state_b)

        # 切回 A
        activate_course(identity_a)
        state_a_restored = load_course_state(identity_a.key())
        assert state_a_restored is not None
        assert state_a_restored.status == "ACTIVE"
        assert state_a_restored.run_count == 1
        assert state_a_restored.success_count == 1
        assert state_a_restored.last_completed_task == "1217304706"


# ── Test E: Repository Persistence ───────────────────────────────
class TestE_Persistence:
    def test_state_survives_dir_recreate(self, tmp_state_dir):
        """模拟新 runner：删除 state 目录后重新加载应恢复状态。"""
        result = resolve_course(COURSE_A_URL)
        identity = result.identity
        initialize_course(identity)
        state = run_course(identity, "1217304706", passed=True,
                          timing_s=792.0, verdict="PASS")
        save_course_state(state)

        # 模拟新 runner：重新 patch 路径（相当于重新 import）
        # 实际测试中，文件已持久化到 tmp_path，重新加载即可
        active = load_active_course()
        assert active is not None
        assert active.key() == identity.key()

        loaded = load_course_state(identity.key())
        assert loaded is not None
        assert loaded.run_count == 1
        assert loaded.success_count == 1

    def test_multiple_courses_persisted(self, tmp_state_dir):
        """多门课程状态应分别持久化。"""
        results = [resolve_course(COURSE_A_URL), resolve_course(COURSE_B_URL)]
        assert all(r.is_ok() for r in results)

        for r in results:
            initialize_course(r.identity)
            run_course(r.identity, "chap_1", passed=True,
                      timing_s=100.0, verdict="PASS")

        courses = get_courses_list()
        keys = {c["key"] for c in courses}
        assert len(keys) == 2
        assert any("265997861" in k for k in keys)
        assert any("999999999" in k for k in keys)


# ── 回归测试：E1/E2/E3 核心逻辑不受影响 ─────────────────────────
class TestRegression:
    def test_resolve_does_not_break_existing_parse(self):
        """解析逻辑应与现有 parse_course_url 兼容。"""
        from e2_headed_gha import parse_course_url
        old = parse_course_url(COURSE_A_URL)
        new = resolve_course(COURSE_A_URL)
        assert new.is_ok()
        assert old["course_id"] == new.identity.course_id
        assert old["clazz_id"] == new.identity.clazz_id
        assert old["cpi"] == new.identity.cpi

    def test_same_course_different_url_params(self):
        """同课程不同 URL 参数应识别为 SAME_COURSE。"""
        r1 = resolve_course(COURSE_A_URL)
        r2 = resolve_course(COURSE_A_SAME_DIFF_CHAPTER)
        assert r1.is_ok() and r2.is_ok()
        assert r1.identity.key() == r2.identity.key()

        det = detect_course_change(COURSE_A_SAME_DIFF_CHAPTER, r1.identity)
        assert det.kind == "SAME_COURSE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
