"""TDVP 集成测试：被动探测 + 注册表 + 同步"""
import json
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from tvdp.tdvp import (
    discover_course,
    save_task_registry,
    load_task_registry,
    sync_progress_to_course_state,
)
from state.course_state import save_course_state, CourseIdentity, CourseProgress, CourseState


REAL_HTML = """
<div class="task-item"><span class="title">1.1 互联网概述</span><span class="status">1</span></div>
<div class="task-item"><span class="title">1.2 互联网的组成</span><span class="status">1</span></div>
<div class="task-item"><span class="title">1.3 计算机网络的概念与类别</span><span class="status">0</span></div>
<div class="task-item"><span class="title">1.4 计算机网络的拓扑结构</span><span class="status">1</span></div>
<div class="task-item"><span class="title">1.5 计算机网络的性能</span><span class="status">2</span></div>
<div class="task-item"><span class="title">1.6 计算机网络的体系结构</span><span class="status">0</span></div>
"""

COURSE_URL = "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=abc&mooc2=1&hidetype=0&openc=xyz"


def _make_state(tmp_path):
    """创建临时 course state。"""
    from state.course_state import STATE_DIR, COURSES_DIR, ACTIVE_FILE
    import tvdp.tdvp as tdvp_mod
    # 临时替换路径
    tdvp_mod.TASKS_FILE = tmp_path / "tdvp_tasks.json"
    STATE_DIR.__class__.__setitem__(STATE_DIR.__class__, STATE_DIR, tmp_path)
    COURSES_DIR.__class__.__setitem__(COURSES_DIR.__class__, COURSES_DIR, tmp_path / "courses")
    ACTIVE_FILE.__class__.__setitem__(ACTIVE_FILE.__class__, ACTIVE_FILE, tmp_path / "active_course.json")
    (tmp_path / "courses").mkdir(parents=True, exist_ok=True)

    ci = CourseIdentity("265997861", "151695658", "506830460", "test_course", COURSE_URL, "2026-09-04T00:00:00+00:00")
    cs = CourseState(
        schema_version=1, course_identity=ci, status="ACTIVE",
        progress=CourseProgress(completed=0, total=None, last_completed_task=None, active_task=None),
        run_count=0, success_count=0, failure_count=0,
    )
    save_course_state(cs)
    return cs


class TestIntegration:
    def test_full_pipeline(self, tmp_path, monkeypatch):
        """端到端：发现 → 注册 → 同步到 course state → 验证。"""
        import tvdp.tdvp as tdvp_mod
        tdvp_mod.TASKS_FILE = tmp_path / "tdvp_tasks.json"
        import state.course_state as cs_mod
        monkeypatch.setattr(cs_mod, "STATE_DIR", tmp_path)
        monkeypatch.setattr(cs_mod, "COURSES_DIR", tmp_path / "courses")

        # 1. 发现
        disc = discover_course(COURSE_URL, REAL_HTML, chapter_id="1217304706")
        assert disc.course_key == "265997861_151695658"
        assert len(disc.all_tasks) > 0

        # 2. 注册
        registry = {t.task_id: t for t in disc.all_tasks}
        save_task_registry(disc.course_key, registry)
        loaded = load_task_registry(disc.course_key)
        assert len(loaded) == len(registry)

        # 3. 创建 course state 并同步
        from state.course_state import save_course_state, CourseState, CourseIdentity, CourseProgress
        ci = CourseIdentity("265997861", "151695658", "506830460", "test_course", COURSE_URL, "2026-09-04T00:00:00+00:00")
        cs = CourseState(
            schema_version=1, course_identity=ci, status="ACTIVE",
            progress=CourseProgress(completed=0, total=None, last_completed_task=None, active_task=None),
            run_count=0, success_count=0, failure_count=0,
        )
        monkeypatch.setattr(cs_mod, "COURSES_DIR", tmp_path / "courses")
        save_course_state(cs)

        result = sync_progress_to_course_state(disc.course_key, disc)
        assert result["course_key"] == disc.course_key
        assert result["total"] == len(disc.all_tasks)
        assert result["completed"] == len(disc.completed_tasks)
        assert result["pending"] == len(disc.pending_tasks)
        assert result["task_queue"] is not None

    def test_pending_tasks_sort_order(self, tmp_path, monkeypatch):
        """待验证任务按 task_id 排序。"""
        import tvdp.tdvp as tdvp_mod
        tdvp_mod.TASKS_FILE = tmp_path / "tdvp_tasks.json"

        from tvdp.tdvp import TaskInfo, TaskEvidence, save_task_registry, get_pending_tasks
        tasks = {
            "t3": TaskInfo("t3", "c1", "Task3", "video", "UNKNOWN", "UNKNOWN", "", TaskEvidence("UNKNOWN", "UNKNOWN", "")),
            "t1": TaskInfo("t1", "c1", "Task1", "video", "COMPLETED", "UI", "marker=1", TaskEvidence("COMPLETED", "UI", "marker=1")),
            "t2": TaskInfo("t2", "c1", "Task2", "video", "PENDING", "UI", "marker=0", TaskEvidence("PENDING", "UI", "marker=0")),
        }
        save_task_registry("key", tasks)
        pending = get_pending_tasks("key")
        assert pending[0].task_id == "t2"
        assert pending[1].task_id == "t3"
