# BLOCKED 任务的 reconcile 不可被 live_pending 复活（防止无限重跑一个注定失败的视频章）
#
# 场景：1217304719（点对点协议PPP）在 headed-Xvfb 反复抓不到 <video>，达
# consecutive_failures>=max_attempts 被 BLOCKED。但每轮 live DOM 仍显示
# 「未完成」→ reconcile 的 dom_done+live_pending 分支曾把它 mark_stale+
# downgrade_to_pending 回到 PENDING，从而 reconcile_queue 又把它入队 → 反复
# 重跑同一个注定失败的任务（死循环）。修复：reconcile 跳过 BLOCKED 任务。
import pytest

from app.registry.task_registry import (
    TaskRecord, done_chapter_ids_from_registry, reconcile_queue,
)
from app.registry.reconcile import reconcile_registry
from tvdp.tdvp import TaskEvidence, TaskInfo


def _blocked_record(tid, cid, title="点对点协议PPP"):
    rec = TaskRecord(tid, cid, title)
    rec.status = "BLOCKED"
    rec.consecutive_failures = 3
    rec.max_attempts = 3
    return rec


def test_blocked_preserved_against_live_pending_resurrection(tmp_registry):
    old = _blocked_record("1217304719", "1217304719")
    discovery = [
        TaskInfo("1217304719", "1217304719", "点对点协议PPP", "video",
                 "PENDING", "UI", "2个待完成", TaskEvidence("PENDING", "UI", "")),
    ]
    live_pending = {"1217304719"}
    # dom_done=True 且 live_pending 命中 —— 旧逻辑会降级 PENDING，新逻辑保持 BLOCKED
    fixed, rep = reconcile_registry(
        "k", {"1217304719": old}, discovery,
        {"1217304719": "completed"}, live_pending=live_pending)
    rec = fixed["1217304719"]
    assert rec.status == "BLOCKED", f"expected BLOCKED, got {rec.status}"
    assert rec.consecutive_failures == 3
    # 不可重新进队
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    assert "1217304719" not in {i["task_id"] for i in q.items}


def test_blocked_skipped_even_when_dom_pending(tmp_registry):
    # dom_status='pending'（未完成）+ live_pending 命中 —— 仍保持 BLOCKED
    old = _blocked_record("X1", "X1")
    discovery = [TaskInfo("X1", "X1", "x", "video", "PENDING", "UI", "x",
                          TaskEvidence("PENDING", "UI", ""))]
    fixed, _ = reconcile_registry("k", {"X1": old}, discovery,
                                  {"X1": "pending"}, live_pending={"X1"})
    assert fixed["X1"].status == "BLOCKED"


@pytest.fixture
def tmp_registry(tmp_path):
    """让 load/save_registry 走临时目录，避免污染 state/。"""
    import app.registry.task_registry as tr
    orig = tr.TASKS_DIR
    tr.TASKS_DIR = tmp_path / "registry"
    yield tmp_path
    tr.TASKS_DIR = orig


class TestMigrationPreservesBlocked:
    def test_blocked_survives_task_id_migration(self, tmp_registry):
        # 旧 task_id 不在最新 discovery → 走 by_title 迁移重建。修复前重建把
        # status 改成 UNKNOWN/PENDING 且丢 BLOCKED/失败计数 → 每轮被当 READY
        # 反复重跑（1217304719 真因：reconcile kept=0 → rebuild）。修复后迁移
        # 必须保留 BLOCKED + consecutive_failures，保持冻结不进队列。
        from app.registry.task_registry import TaskRecord
        from app.registry.reconcile import reconcile_registry
        from tvdp.tdvp import TaskInfo, TaskEvidence

        old = TaskRecord("legacy_ppp", "legacy", "点对点协议PPP")
        old.status = "BLOCKED"
        old.consecutive_failures = 3
        old.max_attempts = 3
        discovery = [TaskInfo("1217304719", "1217304719", "点对点协议PPP", "video",
                              "PENDING", "UI", "x", TaskEvidence("PENDING", "UI", ""))]
        fixed, _ = reconcile_registry(
            "k", {"legacy_ppp": old}, discovery,
            {"1217304719": "pending"}, live_pending=set())

        assert "legacy_ppp" not in fixed          # 旧 key 唯一化淘汰
        rec = fixed.get("1217304719")
        assert rec is not None, "migrated task must exist"
        assert rec.status == "BLOCKED", f"expected BLOCKED got {getattr(rec, 'status', None)}"
        assert rec.consecutive_failures == 3


    def test_non_blocked_migration_stays_executable(self, tmp_registry):
        # 对照：未 BLOCKED（仅 FAILED）的迁移任务仍可进队重试（不误冻结）。
        from app.registry.task_registry import (
            TaskRecord, done_chapter_ids_from_registry, reconcile_queue,
        )
        from app.registry.reconcile import reconcile_registry
        from tvdp.tdvp import TaskInfo, TaskEvidence

        old = TaskRecord("legacy_y", "y", "互联网概述")
        old.status = "FAILED"
        old.consecutive_failures = 1
        old.max_attempts = 3
        discovery = [TaskInfo("1217304700", "1217304700", "互联网概述", "video",
                              "PENDING", "UI", "x", TaskEvidence("PENDING", "UI", ""))]
        fixed, _ = reconcile_registry("k", {"legacy_y": old}, discovery, {},
                                      live_pending=set())
        rec = fixed.get("1217304700")
        assert rec is not None
        # 迁移后非 BLOCKED 但带 repID/可重试状态（FAILED 或 PENDING）
        assert rec.status in ("UNKNOWN", "PENDING", "FAILED")
        assert rec.consecutive_failures == 1
