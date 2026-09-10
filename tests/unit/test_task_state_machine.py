"""E6.1 — Task State Integrity, Failure Persistence, Queue Reconciliation, Evidence Completion.

对应 E6.1 验收矩阵：
  - completion requires evidence
  - nextUnit != completion
  - failure increments counter
  - failure persists
  - threshold → BLOCKED
  - completed task not pushed
  - failed task can retry
  - blocked task skipped
  - same course reconciliation
  - PASS → registry update (evidence-backed)
  - FAIL → registry update (cmd_run postflight mark_failed)
  - previous bad done_ids → migration repair
  - 回归：target=1217304721, nextUnit 跳 1217304708 → 不得让 1217304708 COMPLETED
"""

import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_registry(tmp_path):
    """把 e6 registry 的文件路径指向临时目录，避免污染仓库。"""
    import app.registry.task_registry as tr
    orig = tr.TASKS_DIR
    tr.TASKS_DIR = tmp_path / "registry"
    yield tmp_path
    tr.TASKS_DIR = orig


# ── State Machine ──────────────────────────────────────────────────

class TestStateMachine:
    def test_mark_completed_requires_evidence(self):
        from app.registry.task_registry import TaskRecord
        t = TaskRecord("A", "c1", "T")
        with pytest.raises(ValueError):
            t.mark_completed(run_id="", source="isPassed")
        with pytest.raises(ValueError):
            t.mark_completed(run_id="r1", evidence_level="NONE")

    def test_mark_completed_with_evidence_ok(self):
        from app.registry.task_registry import TaskRecord
        t = TaskRecord("A", "c1", "T")
        t.mark_completed(run_id="r1", source="isPassed", passed_object_ids=["obj1"])
        assert t.status == "COMPLETED"
        assert t.completion_evidence.type == "SERVER_VERIFIED"
        assert t.completion_evidence.passed_object_ids == ["obj1"]
        assert t.consecutive_failures == 0

    def test_mark_failed_increments_and_keeps_stage(self):
        from app.registry.task_registry import TaskRecord
        t = TaskRecord("A", "c1", "T")
        t.mark_failed(run_id="r1", failure_stage="VIDEO_METADATA", detail="90s timeout")
        assert t.status == "FAILED"
        assert t.consecutive_failures == 1
        assert t.failure.stage == "VIDEO_METADATA"
        assert t.failure.run_id == "r1"
        assert t.completion_evidence.type == "NONE"

    def test_threshold_reaches_blocked(self):
        from app.registry.task_registry import TaskRecord
        t = TaskRecord("A", "c1", "T", max_attempts=3)
        statuses = []
        for i in range(3):
            statuses.append(t.mark_failed(run_id=f"r{i}", failure_stage="M"))
        assert statuses == ["FAILED", "FAILED", "BLOCKED"]
        assert t.consecutive_failures == 3

    def test_serialization_roundtrip_preserves_evidence(self):
        from app.registry.task_registry import TaskRecord
        t = TaskRecord("A", "c1", "T")
        t.mark_completed(run_id="r1", source="isPassed", detail="passed_object_ids=3")
        d = TaskRecord.from_dict(json.loads(json.dumps(t.to_dict())))
        assert d.status == "COMPLETED"
        assert d.completion_evidence.source == "isPassed"
        assert d.consecutive_failures == 0


# ── Queue Reconciliation ───────────────────────────────────────────

class TestQueueReconcile:
    def test_completed_task_not_queued(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, reconcile_queue, done_chapter_ids_from_registry
        a = TaskRecord("A", "c1", "T"); a.mark_completed(run_id="r1", source="isPassed")
        b = TaskRecord("B", "c2", "T")
        q = reconcile_queue("k", {"A": a, "B": b})
        ids = [i["task_id"] for i in q.items]
        assert "A" not in ids
        assert "B" in ids
        assert done_chapter_ids_from_registry({"A": a}) == {"c1"}

    def test_failed_task_can_retry(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, reconcile_queue
        f = TaskRecord("F", "c1", "T"); f.mark_failed(run_id="r", failure_stage="S")
        q = reconcile_queue("k", {"F": f})
        assert [i["task_id"] for i in q.items] == ["F"]
        assert q.items[0]["state"] == "RETRY"

    def test_blocked_task_skipped(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, reconcile_queue
        b = TaskRecord("B", "c1", "T", max_attempts=2)
        for i in range(2):
            b.mark_failed(run_id=str(i), failure_stage="S")
        q = reconcile_queue("k", {"B": b})
        assert [i["task_id"] for i in q.items] == []

    def test_queue_is_derived_not_authoritative(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, reconcile_queue
        a = TaskRecord("A", "c1", "T")
        q = reconcile_queue("k2", {"A": a})
        assert [i["task_id"] for i in q.items] == ["A"]


# ── Reconciliation (canonical state) ───────────────────────────────

class TestReconcile:
    def _mk(self, tid, cid, title="T"):
        from tvdp.tdvp import TaskInfo, TaskEvidence
        return TaskInfo(tid, cid, title, "video", "UNKNOWN", "UI", "",
                        TaskEvidence("UNKNOWN", "UI", ""))

    def test_completed_without_evidence_downgraded(self):
        from app.registry.task_registry import TaskRecord
        from app.registry.reconcile import reconcile_registry
        bad = TaskRecord("C1", "c1", "Ch1", status="COMPLETED")
        tasks = [self._mk("C1", "c1")]
        reg, rep = reconcile_registry("k", {"C1": bad}, tasks, {})
        assert reg["C1"].status == "UNKNOWN"
        assert rep.downgraded == 1

    def test_completed_with_server_evidence_retained(self):
        from app.registry.task_registry import TaskRecord
        from app.registry.reconcile import reconcile_registry
        good = TaskRecord("C2", "c2", "T")
        good.mark_completed(run_id="r1", source="isPassed")
        tasks = [self._mk("C2", "c2")]
        reg, rep = reconcile_registry("k", {"C2": good}, tasks, {})
        assert reg["C2"].status == "COMPLETED"
        assert rep.kept_completed == 1

    def test_nextunit_url_change_never_completes(self):
        """E6.1 §13 回归：target=1217304721，nextUnit URL 跳到 1217304708，
        1217304708 绝不因页面跳转而进入 COMPLETED。"""
        from app.registry.task_registry import TaskRecord
        from app.registry.reconcile import reconcile_registry
        polluted = TaskRecord("1217304721", "1217304721", "Target",
                              status="COMPLETED")  # 无证据（污染）。
        tasks = [self._mk("1217304721", "1217304721"),
                 self._mk("1217304708", "1217304708")]
        # 服务器 DOM 只确认 1217304721 完成（不确认 1217304708）
        reg, rep = reconcile_registry("k", {"1217304721": polluted}, tasks,
                                      {"1217304721": "completed"})
        assert reg["1217304721"].status == "COMPLETED"
        assert "1217304708" in reg
        assert reg["1217304708"].status != "COMPLETED"


# ── Failure Persistence ────────────────────────────────────────────

class TestFailurePersistence:
    def test_runtime_fail_writes_back(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, save_registry, load_registry
        t = TaskRecord("1217304708", "1217304708", "Target")
        save_registry("ck", {"1217304708": t})
        t = load_registry("ck")["1217304708"]
        t.mark_failed(run_id="run-fail", failure_stage="VIDEO_METADATA", detail="no cards")
        save_registry("ck", {"1217304708": t})
        saved = load_registry("ck")["1217304708"]
        assert saved.consecutive_failures == 1
        assert saved.status == "FAILED"
        assert saved.failure.stage == "VIDEO_METADATA"

    def test_success_resets_consecutive_failures(self, tmp_registry):
        from app.registry.task_registry import TaskRecord, save_registry, load_registry
        t = TaskRecord("T", "c1", "x", consecutive_failures=2)
        save_registry("kk", {"T": t})
        t = load_registry("kk")["T"]
        t.mark_completed(run_id="r1", source="isPassed")
        save_registry("kk", {"T": t})
        assert load_registry("kk")["T"].consecutive_failures == 0
        assert load_registry("kk")["T"].status == "COMPLETED"


# ── Migration / Repair ─────────────────────────────────────────────

class TestMigration:
    def test_bad_done_ids_repaired(self):
        """历史污染：无证据 COMPLETED 被修复为 UNKNOWN（Registry + History 分离）。"""
        from app.registry.task_registry import TaskRecord
        from app.registry.reconcile import reconcile_registry
        bad = TaskRecord("X", "chX", "T", status="COMPLETED")
        good = TaskRecord("Y", "chY", "T")
        good.mark_completed(run_id="r1", source="isPassed")
        reg, rep = reconcile_registry("mig", {"X": bad, "Y": good}, [], {})
        assert reg["X"].status == "UNKNOWN"
        assert reg["Y"].status == "COMPLETED"


# ── cmd_run: FAIL → registry 真正写回（端到端回写，mock 浏览器）───────────────────

class TestCmdRunWriteback:
    def test_cmd_run_fail_marks_registry(self, tmp_registry):
        """engine 返回 FAIL → cmd_run 的 postflight 把 registry 标记 FAILED & 持久化。"""
        import argparse
        import app.run as ar
        from unittest.mock import patch
        import app.registry.task_registry as tr
        tr.TASKS_DIR = tmp_registry / "registry"

        from app.registry.task_registry import TaskRecord, save_registry, load_registry
        # cmd_run 使用 identity.key() → "265997861_151695158" 作为 registry 目录
        save_registry("265997861_151695158",
                      {"1217304708": TaskRecord("1217304708", "1217304708", "Target")})

        from state.course_state import CourseIdentity
        ci = CourseIdentity("265997861", "151695158", "506830460", "T", "", "2026-01-01T00:00:00Z")

        fail_ev = {"verdict": "FAIL(video metadata not ready)", "passed_count": 5,
                   "failure_stage": "NO_METADATA", "checks": {}, "errors": ["x"]}

        with patch.object(ar, "parse_course_url", return_value={
                "course_id": "265997861", "clazz_id": "151695158", "cpi": "506830460",
                "enc": "abc", "chapter_id": "1217304708", "openc": "x", "hidetype": "0"}), \
             patch.object(ar, "run_test", return_value=fail_ev), \
             patch.object(ar, "load_active_course", return_value=ci), \
             patch("state.course_state.STATE_DIR", tmp_registry / "cstate"), \
             patch("state.course_state.COURSES_DIR", tmp_registry / "cstate" / "courses"), \
             patch("state.course_state.ACTIVE_FILE", tmp_registry / "cstate" / "active.json"):
            ns = argparse.Namespace(course_url="url", chapter_id="1217304708",
                                    output=str(tmp_registry / "r.json"),
                                    xvfb_display=":99", max_attempts=1)
            ar.cmd_run(ns)

        saved = load_registry("265997861_151695158")["1217304708"]
        assert saved.status == "FAILED"
        assert saved.consecutive_failures == 1
        assert saved.failure.stage == "NO_METADATA"


# ── Scheduler restart recovery ─────────────────────────────────────

class TestSchedulerRecovery:
    def test_scheduler_restart_recovers_from_registry(self, tmp_registry):
        """重启后 reconcile 能从 registry（+discovery）恢复 canonical 状态。"""
        from app.registry.task_registry import TaskRecord, save_registry, load_registry
        from app.registry.reconcile import reconcile_registry
        ok = TaskRecord("R2", "ch2", "T"); ok.mark_completed(run_id="r-pre", source="isPassed")
        save_registry("rec", {"R2": ok})
        from tvdp.tdvp import TaskInfo, TaskEvidence
        tasks = [TaskInfo("R2", "ch2", "T", "video", "UNKNOWN", "UI", "",
                          TaskEvidence("UNKNOWN", "UI", ""))]
        reg = load_registry("rec")
        fixed, rep = reconcile_registry("rec", reg, tasks, {"ch2": "completed"})
        assert fixed["R2"].status == "COMPLETED"
        assert rep.kept_completed == 1