"""E6.2 — Multi-video chapter is split into per-video tasks and stays queued.

Real case: 1217304708 数据通信基础知识 has 2 video points.
  - When the scheduler live-verifies the chapter (video_total=2), discovery
    must emit two video tasks: `1217304708` and `1217304708:video2`.
  - Both are READY (unfinished) initially.
  - After video #1 completes (its point finished server-side), video #2's task
    must STILL be READY in the queue (not dropped) — the whole point of the fix.
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


def test_4708_two_video_queue_splits(tmp_registry):
    """构建阶段：2 视频章 → `zh` 与 `zh:video2` 都是 READY。"""
    from e6.task_registry import reconcile_queue, done_chapter_ids_from_registry
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import build_tasks_from_discovery

    cid = "1217304708"
    chapters = [{"chapter_id": cid, "title": "数据通信基础知识",
                 "status": "pending", "job_remaining": 2,
                 "chapter_index": 11, "cell_index": 11}]
    # 首次：L2 发现 2 个视频点，都未完成
    tasks = build_tasks_from_discovery(chapters, video_counts={cid: 2})
    reg, _ = reconcile_registry("k1", {}, tasks, {cid: "pending"})
    # 两个 video task 都产生
    assert cid in reg and f"{cid}:video2" in reg
    q = reconcile_queue("k1", reg, done_chapter_ids_from_registry(reg))
    ids = {i["task_id"] for i in q.items}
    assert cid in ids          # 第一个视频 → READY
    assert f"{cid}:video2" in ids   # 第二个视频 → READY（不丢失）


def test_after_first_video_second_stays_queued(tmp_registry):
    """回归核心：v1 完成后 v2 仍 READY，下次 action 继续执行 v2。"""
    from e6.task_registry import TaskRecord, reconcile_queue, done_chapter_ids_from_registry
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import build_tasks_from_discovery

    cid = "1217304708"
    course_key = "k1"
    chapters = [{"chapter_id": cid, "title": "数据通信",
                 "status": "pending", "job_remaining": 2,
                 "chapter_index": 11, "cell_index": 11}]

    # 阶段 0：2 个视频都未完成
    reg, _ = reconcile_registry(course_key, {},
        build_tasks_from_discovery(chapters, video_counts={cid: 2}), {cid: "pending"})

    # 阶段 1：v1 已完成（= runtime 跑完第 1 个视频，postflight 把它 mark_completed）
    # 之后 live 复核：v1 finished、v2 still [unfinished]，v2 的 live_pending 仍在
    v1 = reg[cid]
    v1.mark_completed(run_id="run-v1", source="isPassed")     # 与 app/run.py postflight 一致
    reg1 = dict(reg); reg1[cid] = v1
    # 下一 action 重recon：v2 仍未完成 → live_pending 含 v2
    tasks1 = build_tasks_from_discovery(chapters, video_counts={cid: 2})
    reg, _ = reconcile_registry(course_key, reg1, tasks1, {cid: "pending"},
                                live_pending={f"{cid}:video2"})
    # v1 已完成(保留强证据)→ 不重放；v2 的 video 点仍未完成 → 仍 READY
    q = reconcile_queue(course_key, reg, done_chapter_ids_from_registry(reg))
    ids = {i["task_id"] for i in q.items}
    assert cid not in ids                     # v1 已完成 → 不重放
    assert q.items[0]["chapter_id"] == cid    # 下一 action 仍针对该章
    assert f"{cid}:video2" in ids             # v2 仍在队里（核心回归点）


def test_reconcile_does_not_keep_done_video_ahead_of_pending_video(tmp_registry):
    """E6.2 重点:scheduler 不应把已完成 v1 当作整章 done 而跳过 v2。"""
    cid = "1217304708"
    course_key = "k1"
    from e6.task_registry import (TaskRecord, reconcile_queue, done_chapter_ids_from_registry)
    from e6.reconcile import reconcile_registry
    from tvdp.tdvp import build_tasks_from_discovery

    chapters = [{"chapter_id": cid, "title": "数据通信",
                 "status": "pending", "job_remaining": 2,
                 "chapter_index": 11, "cell_index": 11}]
    # v1 COMPLETED（强证据），v2 未完成 → 章 NOT in done，queue 有 v2
    v1 = TaskRecord(cid, cid, "数据通信")
    v1.mark_completed(run_id="r-v1", source="isPassed")
    v2 = TaskRecord(f"{cid}:video2", cid, "数据通信", task_type="video", status="PENDING")
    reg0 = {cid: v1, f"{cid}:video2": v2}
    done = done_chapter_ids_from_registry(reg0)
    assert cid not in done            # 章未全完成
    q = reconcile_queue(course_key, reg0, done)
    ids = {i["task_id"] for i in q.items}
    assert cid not in ids             # v1 已完成 → 不重放
    assert f"{cid}:video2" in ids     # v2 仍 READY


def test_catalog_stale_downgrades_completed_when_jobs_remain(tmp_registry):
    """E6.2/L1: 目录层 job_remaining>0 ⇒ COMPLETED 降级 STALE，重新入队。

    真实回归：4706/4708 各自 2 个视频点都没播完，但 registry 是 COMPLETED
    （被旧代码 isPassed 一次性标完成）→ 现在必须被目录校准重新拉起，
    而不是当 done 永久跳过（用户报告：遗留视频总是被跳过）。
    """
    from e6.task_registry import (TaskRecord, reconcile_queue, done_chapter_ids_from_registry)
    from e6.reconcile import stale_completed_by_catalog

    cid = "1217304708"
    # 旧协议把整章标成单个 COMPLETED video task
    done_task = TaskRecord(cid, cid, "数据通信基础知识", task_type="video")
    done_task.mark_completed(run_id="old-run", source="isPassed")
    reg = {cid: done_task}

    # 真实目录：该章仍有 2 个未完成任务点
    chapters = [{"chapter_id": cid, "status": "pending", "job_remaining": 2}]
    stale = stale_completed_by_catalog(reg, chapters)
    assert stale == [cid]                       # 本章的 COMPLETED 被点名降级
    for sid in stale:
        reg[sid].mark_stale(detail="catalog job_remaining>0 (L1)")
    assert reg[cid].status == "STALE"           # 完成态被推翻
    # 章不再是 done → 该 video 回到 READY 队列
    done_ids = done_chapter_ids_from_registry(reg)
    assert cid not in done_ids
    q = reconcile_queue("k1", reg, done_ids)
    ids = {i["task_id"] for i in q.items}
    assert cid in ids                            # 重新入队（不再跳过）

    # 对照：确无剩余任务的章 → COMPLETED 保持，不入 stale
    ok = TaskRecord("1217304700", "1217304700", "互联网概述", task_type="video")
    ok.mark_completed(run_id="x", source="isPassed")
    reg_ok = {"1217304700": ok}
    chapters_ok = [{"chapter_id": "1217304700", "job_remaining": 0}]
    assert stale_completed_by_catalog(reg_ok, chapters_ok) == []