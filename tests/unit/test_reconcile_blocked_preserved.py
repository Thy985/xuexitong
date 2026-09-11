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
