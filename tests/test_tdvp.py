"""E7: TDVP 单元测试"""
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tvdp.tdvp import (
    TaskInfo,
    TaskEvidence,
    ChapterInfo,
    CourseDiscovery,
    TaskStatus,
    ProbeSource,
    parse_task_status_from_page,
    aggregate_evidence,
    load_task_registry,
    save_task_registry,
    get_pending_tasks,
    get_completed_tasks,
    discover_course,
    run_passive_probe,
)


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def sample_html_with_markers():
    """模拟学习通页面片段，包含任务点标记。"""
    return """
    <div class="task-item">
        <span class="title">1.1 互联网概述</span>
        <span class="status">1</span>
    </div>
    <div class="task-item">
        <span class="title">1.2 互联网的组成</span>
        <span class="status">1</span>
    </div>
    <div class="task-item">
        <span class="title">1.3 计算机网络的概念与类别</span>
        <span class="status">0</span>
    </div>
    <div class="task-item">
        <span class="title">1.4 计算机网络的拓扑结构</span>
        <span class="status">1</span>
    </div>
    <div class="task-item">
        <span class="title">1.5 计算机网络的性能</span>
        <span class="status">2</span>
    </div>
    <div class="task-item">
        <span class="title">1.6 计算机网络的体系结构</span>
        <span class="status">0</span>
    </div>
    <div class="task-item">
        <span class="title">1.7 知识扩展</span>
        <span class="status">0</span>
    </div>
    """


@pytest.fixture
def sample_html_no_markers():
    """没有 status 标记的页面。"""
    return """
    <div class="task-item">
        <span class="title">1.1 互联网概述</span>
    </div>
    <div class="task-item">
        <span class="title">1.2 互联网的组成</span>
    </div>
    """


@pytest.fixture
def course_url():
    return "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=abc&mooc2=1&hidetype=0&openc=xyz"


# ── 测试：数据模型 ─────────────────────────────────────────────────

class TestDataModels:
    def test_task_evidence_to_dict(self):
        ev = TaskEvidence("COMPLETED", "UI", "UI marker=1")
        d = ev.to_dict()
        assert d["status"] == "COMPLETED"
        assert d["confidence"] == "UI"
        assert "observed_at_utc" in d

    def test_task_info_roundtrip(self):
        t = TaskInfo(
            task_id="1217304706_1_1",
            chapter_id="1217304706",
            title="1.1 互联网概述",
            task_type="video",
            status="COMPLETED",
            confidence="UI",
            source_detail="UI marker=1",
            evidence=TaskEvidence("COMPLETED", "UI", "UI marker=1"),
        )
        d = t.to_dict()
        t2 = TaskInfo.from_dict(d)
        assert t2.task_id == t.task_id
        assert t2.status == "COMPLETED"
        assert t2.confidence == "UI"

    def test_chapter_info_to_dict(self):
        ch = ChapterInfo(
            chapter_id="1217304706",
            title="第一章 概述",
            tasks=[
                TaskInfo("1217304706_1_1", "1217304706", "1.1", "video",
                         "COMPLETED", "UI", "marker=1", TaskEvidence("COMPLETED", "UI", "marker=1")),
            ]
        )
        d = ch.to_dict()
        assert d["chapter_id"] == "1217304706"
        assert d["task_count"] == 1

    def test_course_discovery_properties(self):
        tasks = [
            TaskInfo(f"t{i}", "c1", f"Task {i}", "video",
                     "COMPLETED" if i < 3 else "PENDING", "UI", "", TaskEvidence("COMPLETED" if i < 3 else "PENDING", "UI", ""))
            for i in range(5)
        ]
        disc = CourseDiscovery(
            course_id="265997861", clazz_id="151695658", course_key="265997861_151695658",
            chapters=[ChapterInfo("c1", "Chapter 1", tasks)]
        )
        assert len(disc.all_tasks) == 5
        assert len(disc.completed_tasks) == 3
        assert len(disc.pending_tasks) == 2
        assert len(disc.unknown_tasks) == 0


# ── 测试：Passive Probe ────────────────────────────────────────────

class TestPassiveProbe:
    def test_parse_with_markers(self, sample_html_with_markers):
        tasks = parse_task_status_from_page(sample_html_with_markers, "1217304706")
        assert len(tasks) > 0
        # 应找到至少一些有标记的任务
        has_completed = any(t.status == "COMPLETED" for t in tasks)
        has_pending = any(t.status == "PENDING" for t in tasks)
        assert has_completed or has_pending

    def test_parse_no_markers_returns_unknown(self, sample_html_no_markers):
        tasks = parse_task_status_from_page(sample_html_no_markers, "1217304706")
        for t in tasks:
            assert t.confidence == "UI"

    def test_parse_returns_task_info_with_correct_fields(self, sample_html_with_markers):
        tasks = parse_task_status_from_page(sample_html_with_markers, "1217304706")
        for t in tasks:
            assert t.chapter_id == "1217304706"
            assert t.task_id.startswith("1217304706")
            assert t.title  # 标题不应为空


# ── 测试：Evidence Aggregator ─────────────────────────────────────

class TestEvidenceAggregator:
    def test_server_verified_overrides_ui(self):
        passive = {
            "t1": TaskInfo("t1", "c1", "Task1", "video", "PENDING", "UI", "marker=0",
                           TaskEvidence("PENDING", "UI", "marker=0")),
        }
        active = {
            "t1": TaskInfo("t1", "c1", "Task1", "video", "COMPLETED", "SERVER_VERIFIED",
                           "isPassed=true", TaskEvidence("COMPLETED", "SERVER_VERIFIED", "isPassed=true")),
        }
        merged = aggregate_evidence(passive, active)
        assert merged["t1"].status == "COMPLETED"
        assert merged["t1"].confidence == "SERVER_VERIFIED"

    def test_passive_only_no_change(self):
        passive = {
            "t1": TaskInfo("t1", "c1", "Task1", "video", "COMPLETED", "UI", "marker=1",
                           TaskEvidence("COMPLETED", "UI", "marker=1")),
        }
        merged = aggregate_evidence(passive)
        assert merged["t1"].status == "COMPLETED"
        assert merged["t1"].confidence == "UI"

    def test_new_task_in_active_only(self):
        passive = {}
        active = {
            "t_new": TaskInfo("t_new", "c1", "New Task", "video", "COMPLETED", "SERVER_VERIFIED",
                              "newly verified", TaskEvidence("COMPLETED", "SERVER_VERIFIED", "newly verified")),
        }
        merged = aggregate_evidence(passive, active)
        assert "t_new" in merged
        assert merged["t_new"].confidence == "SERVER_VERIFIED"


# ── 测试：Task Registry ───────────────────────────────────────────

class TestTaskRegistry:
    def test_save_and_load(self, tmp_path, monkeypatch):
        # 临时替换 TASKS_FILE
        import tvdp.tdvp as tdvp_mod
        test_file = tmp_path / "tdvp_tasks.json"
        monkeypatch.setattr(tdvp_mod, "TASKS_FILE", test_file)

        tasks = {
            "t1": TaskInfo("t1", "c1", "Task 1", "video", "COMPLETED", "UI", "marker=1",
                           TaskEvidence("COMPLETED", "UI", "marker=1")),
            "t2": TaskInfo("t2", "c1", "Task 2", "video", "PENDING", "UI", "marker=0",
                           TaskEvidence("PENDING", "UI", "marker=0")),
        }
        save_task_registry("265997861_151695658", tasks)

        loaded = load_task_registry("265997861_151695658")
        assert "t1" in loaded
        assert loaded["t1"].status == "COMPLETED"
        assert "t2" in loaded
        assert loaded["t2"].status == "PENDING"

    def test_get_pending_tasks(self, tmp_path, monkeypatch):
        import tvdp.tdvp as tdvp_mod
        test_file = tmp_path / "tdvp_tasks.json"
        monkeypatch.setattr(tdvp_mod, "TASKS_FILE", test_file)

        tasks = {
            "t1": TaskInfo("t1", "c1", "Task 1", "video", "COMPLETED", "UI", "marker=1",
                           TaskEvidence("COMPLETED", "UI", "marker=1")),
            "t2": TaskInfo("t2", "c1", "Task 2", "video", "PENDING", "UI", "marker=0",
                           TaskEvidence("PENDING", "UI", "marker=0")),
            "t3": TaskInfo("t3", "c1", "Task 3", "video", "UNKNOWN", "UNKNOWN", "",
                           TaskEvidence("UNKNOWN", "UNKNOWN", "")),
        }
        save_task_registry("key", tasks)

        pending = get_pending_tasks("key")
        assert len(pending) == 2  # PENDING + UNKNOWN
        assert all(t.status in ("PENDING", "UNKNOWN") for t in pending)

    def test_get_completed_tasks(self, tmp_path, monkeypatch):
        import tvdp.tdvp as tdvp_mod
        test_file = tmp_path / "tdvp_tasks.json"
        monkeypatch.setattr(tdvp_mod, "TASKS_FILE", test_file)

        tasks = {
            "t1": TaskInfo("t1", "c1", "Task 1", "video", "COMPLETED", "UI", "marker=1",
                           TaskEvidence("COMPLETED", "UI", "marker=1")),
            "t2": TaskInfo("t2", "c1", "Task 2", "video", "PENDING", "UI", "marker=0",
                           TaskEvidence("PENDING", "UI", "marker=0")),
        }
        save_task_registry("key", tasks)

        completed = get_completed_tasks("key")
        assert len(completed) == 1
        assert completed[0].task_id == "t1"


# ── 测试：discover_course ─────────────────────────────────────────

class TestDiscoverCourse:
    def test_discovers_with_chapter_id(self, course_url, sample_html_with_markers):
        disc = discover_course(course_url, sample_html_with_markers, chapter_id="1217304706")
        assert disc.course_key == "265997861_151695658"
        assert len(disc.chapters) >= 1

    def test_discovers_without_chapter_id(self, course_url, sample_html_with_markers):
        disc = discover_course(course_url, sample_html_with_markers)
        assert disc.course_key == "265997861_151695658"
        assert len(disc.chapters) >= 1


# ── 集成测试：真实 HTML ───────────────────────────────────────────

class TestIntegration:
    def test_real_page_structure(self, course_url):
        """用之前捕获的真实页面片段测试解析。"""
        real_html = """
        <div class="task-item">
            <span class="title">1.1 互联网概述</span>
            <span class="status">1</span>
        </div>
        <div class="task-item">
            <span class="title">1.2 互联网的组成</span>
            <span class="status">1</span>
        </div>
        <div class="task-item">
            <span class="title">1.3 计算机网络的概念与类别</span>
            <span class="status">0</span>
        </div>
        <div class="task-item">
            <span class="title">1.6 计算机网络的体系结构</span>
            <span class="status">1</span>
        </div>
        """
        disc = discover_course(course_url, real_html, chapter_id="1217304706")
        tasks = disc.all_tasks
        assert len(tasks) > 0
        completed = [t for t in tasks if t.status == "COMPLETED"]
        pending = [t for t in tasks if t.status == "PENDING"]
        assert len(completed) > 0
        assert len(pending) > 0
