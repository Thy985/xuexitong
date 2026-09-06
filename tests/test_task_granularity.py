"""E6.2 — Task Discovery Granularity & Chapter/Task Separation.

验证：Chapter ≠ Task；Chapter status = aggregate(Task statuses)。
真实案例回归：1217304705「视频任务点已完成、但章节仍有 1 个非视频待完成点」——
  - video task = SERVER_VERIFIED COMPLETED
  - 但 chapter aggregate 不得 = COMPLETED
  - done_chapter_ids 不得把 4705 当完成
  - Queue 不得把该章的 video task 与 other task 一起选中
"""

import json
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


# ── 1) 为什么旧 discovery 只生成 1 个 TaskInfo（E6.2 §1）──────────────

def test_discovery_emits_video_plus_residual_task(tmp_registry):
    """E6.2 §3: 从章节 raw 生成 chapter 内多个 task：video + other(unsupported)。"""
    from tvdp.tdvp import build_tasks_from_discovery
    # 真实 4705：非完成态，job_remaining=1
    chapter = {"chapter_id": "1217304705", "title": "计算机网络的体系结构",
               "status": "pending", "job_remaining": 1,
               "chapter_index": 5, "cell_index": 5, "text": "1.6 ..."}
    tasks = build_tasks_from_discovery([chapter])
    infos = {t.task_id: t for t in tasks}
    assert "1217304705" in infos                       # video task
    assert infos["1217304705"].task_type == "video"
    assert "1217304705:other" in infos            # residual task
    assert infos["1217304705:other"].task_type == "other"
    assert infos["1217304705:other"].status == "PENDING"


def test_discovery_done_chapter_no_residual(tmp_registry):
    """已整体完成章节：只有 video task，不产出 other。"""
    from tvdp.tdvp import build_tasks_from_discovery
    tasks = build_tasks_from_discovery(
        [{"chapter_id": "1217304700", "title": "互联网概述",
          "status": "completed", "task_remaining": 0}])
    ids = [t.task_id for t in tasks]
    assert "1217304700" in ids
    assert "1217304700:other" not in ids


# ── 2) Chapter ≠ Task；Chapter = aggregate(Task)（E6.2 §7）────────────

def test_chapter_aggregate_is_completed_only_when_all_tasks_completed():
    from e6.task_registry import (
        TaskRecord, chapter_aggregate_status, done_chapter_ids_from_registry)
    video = TaskRecord("1217304705", "1217304705", "T")
    video.mark_completed(run_id="r1", source="isPassed")   # video 完成（SERVER_VERIFIED）
    other = TaskRecord("1217304705:other", "1217304705", "T",
                       task_type="other", status="PENDING")     # 残余非 video 未完成
    reg = {"1217304705": video, "1217304705:other": other}
    # §7 + §9: 视频已完成 ≠ 章节完成
    assert chapter_aggregate_status(reg, "1217304705") == "PENDING"
    assert done_chapter_ids_from_registry(reg) == set()          # 不算完成章


def test_chapter_aggregate_completed_when_video_and_other_done():
    from e6.task_registry import (
        TaskRecord, chapter_aggregate_status, done_chapter_ids_from_registry)
    video = TaskRecord("470", "1217304700", "T")
    video.mark_completed(run_id="r1", source="isPassed")
    reg = {"1217304700": video}
    assert chapter_aggregate_status(reg, "1217304700") == "COMPLETED"
    assert done_chapter_ids_from_registry(reg) == {"1217304700"}


# ── 3) Queue 只接受真正可执行 task（video）── §8 ─────────────────────

def test_reconcile_queue_excludes_non_video_task(tmp_registry):
    from e6.task_registry import (
        TaskRecord, reconcile_queue, save_registry)
    video_pending = TaskRecord("1217304705", "1217304705", "T",
                               status="PENDING", task_type="video")
    other_pending = TaskRecord("1217304705:other", "1217304705", "T",
                               status="PENDING", task_type="other")
    q = reconcile_queue("k", {"1217304705": video_pending,
                              "1217304705:other": other_pending})
    ids = {i["task_id"] for i in q.items}
    assert "1217304705" in ids            # video pending → READY
    assert "1217304705:other" not in ids  # non-video → not queued


def test_registry_queue_skips_completed_video_but_chapter_not_done(tmp_registry):
    """E6.2 §9: video 已完成 + other 未完成 → 该 video 不进队列、章不算完成。"""
    from e6.task_registry import (
        TaskRecord, reconcile_queue, done_chapter_ids_from_registry)
    video = TaskRecord("1217304705", "1217304705", "T")
    video.mark_completed(run_id="r-pre", source="isPassed")   # SERVER_VERIFIED
    other = TaskRecord("1217304705:other", "1217304705", "T",
                       task_type="other", status="PENDING")
    reg = {"1217304705": video, "1217304705:other": other}
    done = done_chapter_ids_from_registry(reg)
    assert "1217304705" not in done
    q = reconcile_queue("k", reg, done)
    ids = {i["task_id"] for i in q.items}
    assert "1217304705" not in ids   # video 已完成，不重选
    assert "1217304705:other" not in ids  # other 不可执行


# ── 4) 端到端 reconcile：真实 4705 场景 ────────────────────────────────

def test_reconcile_real_case_4705(tmp_registry):
    """真实案例回归（E6.2 §10）：
       4705 video 已有 SERVER_VERIFIED 完成证据，但该章仍有 non-video PENDING
       → reconcile 后 chapter 不得 COMPLETED，done 排除 4705。"""
    from e6.task_registry import (
        TaskRecord, save_registry, load_registry,
        done_chapter_ids_from_registry, chapter_aggregate_status)
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import TaskInfo, TaskEvidence

    # 预置：video task 已有 SERVER_VERIFIED 完成证据（历史 run）
    video_old = TaskRecord("1217304705", "1217304705", "网络体系结构")
    video_old.mark_completed(run_id="run-33985580763", source="isPassed")
    reg = {"1217304705": video_old}

    # discovery 返回：video(pending) + other(pending) ，非完成章
    discovery = [
        TaskInfo("1217304705", "1217304705", "网络体系结构", "video", "PENDING", "UI",
                 "1.6 1个待完成", TaskEvidence("PENDING", "UI", "")),
        TaskInfo("1217304705:other", "1217304705", "网络体系结构", "other", "PENDING", "UI",
                 "uncategorised 1 待完成", TaskEvidence("PENDING", "UI", "")),
    ]
    fixed, rep = reconcile_registry("k", dict(reg), discovery, {})
    # video：保留强证据 COMPLETED
    assert fixed["1217304705"].status == "COMPLETED"
    # 新增 other pending 任务
    assert fixed["1217304705:other"].status == "PENDING"
    assert fixed["1217304705:other"].task_type == "other"
    # 章节聚合不得为 COMPLETED；done 不含 4705
    assert chapter_aggregate_status(fixed, "1217304705") == "PENDING"
    assert "1217304705" not in done_chapter_ids_from_registry(fixed)