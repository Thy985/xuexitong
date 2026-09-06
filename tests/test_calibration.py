"""E6.2 — Live status calibration: COMPLETED can be overridden by server state.

Real case: 1217304706 物理层的主要任务
  - 历史 registry: video = COMPLETED (昨日 isPassed=true 强证据)
  - 今日服务器 DOM/catalog 显示该章待完成（2 个点：视频 + 达标测试）
  - L2 live 复核确认视频点当前未完成
  必须把该 video 从 COMPLETED 降回 PENDING（重入队列），而不是永久信任历史
  isPassed（否则 registry 变成错误缓存）。

同时覆盖: STALE 状态机 / 冲突章节选择(成本梯度) / 实时 job 点推导。
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_registry(tmp_path):
    import e6.task_registry as tr
    orig = tr.TASKS_DIR
    tr.TASKS_DIR = tmp_path / "registry"
    yield tmp_path
    tr.TASKS_DIR = orig


# -- 1) 状态机: COMPLETED -> STALE -> PENDING -------------------------------

def test_task_record_stale_flags_conflict():
    from e6.task_registry import TaskRecord
    t = TaskRecord("1217304706", "1217304706", "物理层的主要任务")
    t.mark_completed(run_id="run-123", source="isPassed")
    assert t.status == "COMPLETED"
    assert t.completion_evidence.type == "SERVER_VERIFIED"
    t.mark_stale(detail="live verification: now pending")
    assert t.status == "STALE"
    # 完成证据降级为 CONFLICT，但保留 run_id 以便溯源
    assert t.completion_evidence.type == "CONFLICT"
    assert t.completion_evidence.run_id == "run-123"


def test_stale_task_requeues_after_downgrade(tmp_registry):
    from e6.task_registry import TaskRecord, reconcile_queue
    t = TaskRecord("1217304706", "1217304706", "物理层的主要任务")
    t.mark_completed(run_id="r-pre", source="isPassed")
    t.mark_stale()
    t.downgrade_to_pending("live re-check confirmed pending")
    assert t.status == "PENDING"
    assert t.is_executable is True
    q = reconcile_queue("k", {"1217304706": t})
    assert {i["task_id"] for i in q.items} == {"1217304706"}


# -- 2) STALE 章节不再算完成 --------------------------------------------

def test_stale_chapter_not_done():
    from e6.task_registry import (TaskRecord, chapter_aggregate_status,
                                  done_chapter_ids_from_registry)
    t = TaskRecord("1217304706", "1217304706", "物理层的主要任务")
    t.mark_completed(run_id="r", source="isPassed")
    t.mark_stale()
    reg = {"1217304706": t}
    assert chapter_aggregate_status(reg, "1217304706") == "STALE"
    assert "1217304706" not in done_chapter_ids_from_registry(reg)


# -- 3) 任务点类型分类 + live_pending 推导 --------------------------------

def test_classify_job_types():
    from tvdp.tdvp import _classify_job
    assert _classify_job("ans-job-icon ans-job-video ans-job-icon-clear") == "video"
    assert _classify_job("ans-job-icon ans-job-quiz") == "quiz"
    assert _classify_job("ans-job-icon ans-job-exam") == "exam"
    assert _classify_job("some-unrelated-class") == "other"


def test_build_live_pending_only_unfinished():
    from tvdp.tdvp import build_live_pending
    pts = [
        {"task_id": "1217304706", "type": "video", "isFinished": False},
        {"task_id": "1217304706:quiz", "type": "quiz", "isFinished": False},
    ]
    assert build_live_pending(pts) == {"1217304706", "1217304706:quiz"}
    pts[0]["isFinished"] = True                      # 视频已完成
    assert build_live_pending(pts) == {"1217304706:quiz"}


# -- 4) 成本梯度：只选「有 COMPLETED + DOM 待完成」的冲突章节做 L2 ---------

def test_pick_conflict_chapters_only_conflicted():
    from e6.task_registry import TaskRecord
    from e6.reconcile import pick_conflict_chapters
    done_4706 = TaskRecord("1217304706", "1217304706", "T")
    done_4706.mark_completed(run_id="r", source="isPassed")
    done_4705 = TaskRecord("1217304705", "1217304705", "T")
    done_4705.mark_completed(run_id="r", source="isPassed")
    pend_4708 = TaskRecord("1217304708", "1217304708", "T",
                           task_type="video", status="PENDING")
    reg = {"1217304706": done_4706, "1217304705": done_4705,
           "1217304708": pend_4708}
    chapter_ids = ["1217304705", "1217304706", "1217304708"]
    dom_pending = {"1217304706", "1217304708"}
    assert pick_conflict_chapters(chapter_ids, reg, dom_pending) == ["1217304706"]


# -- 5) 端到端: registry COMPLETED(强证据) + live_pending → 降级 PENDING ---

def test_reconcile_downgrades_4706_via_live_pending(tmp_registry):
    from e6.task_registry import (TaskRecord, chapter_aggregate_status,
                                  done_chapter_ids_from_registry, reconcile_queue)
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import TaskEvidence, TaskInfo

    old = TaskRecord("1217304706", "1217304706", "物理层的主要任务")
    old.mark_completed(run_id="run-yesterday", source="isPassed")

    discovery = [
        TaskInfo("1217304706", "1217304706", "物理层的主要任务", "video",
                 "PENDING", "UI", "2个待完成", TaskEvidence("PENDING", "UI", "")),
        TaskInfo("1217304706:quiz", "1217304706", "物理层的主要任务", "quiz",
                 "PENDING", "UI", "达标测试", TaskEvidence("PENDING", "UI", "")),
    ]
    # live 复核确认: 视频点 + 达标测试点都未完成
    live_pending = {"1217304706", "1217304706:quiz"}
    fixed, rep = reconcile_registry("k", {"1217304706": old}, discovery, {},
                                    live_pending=live_pending)
    # 强证据 COMPLETED 也必须服从实时真相 → 降级 PENDING
    assert fixed["1217304706"].status == "PENDING"
    assert rep.repair_map["1217304706"]["after"] == "PENDING"
    # 达标测试作为独立任务点存在
    assert fixed["1217304706:quiz"].status == "PENDING"
    assert fixed["1217304706"].task_type == "video"
    # 降级后可重新进队，章节不再完成
    assert "1217304706" not in done_chapter_ids_from_registry(fixed)
    assert chapter_aggregate_status(fixed, "1217304706") == "PENDING"
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    assert "1217304706" in {i["task_id"] for i in q.items}


# -- 6) 无 live 冲突时，强证据 COMPLETED 仍保留（防抖） --------------------

def test_reconcile_keeps_completed_without_live_conflict(tmp_path):
    from e6.task_registry import TaskRecord
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import TaskEvidence, TaskInfo

    old = TaskRecord("1217304700", "1217304700", "互联网概述")
    old.mark_completed(run_id="run-pre", source="isPassed")
    discovery = [TaskInfo("1217304700", "1217304700", "互联网内容", "video",
                          "PENDING", "UI", "x", TaskEvidence("PENDING", "UI", ""))]
    fixed, _ = reconcile_registry("k", {"1217304700": old}, discovery, {})
    assert fixed["1217304700"].status == "COMPLETED"