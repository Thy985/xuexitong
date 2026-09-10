"""E6.2 — Chapter point-level snapshot (洞2): done derived from point-finished,
not one-shot isPassed. Non-video chapters (洞1) never enter the video queue.
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_registry(tmp_path):
    import app.registry.task_registry as tr
    orig = tr.TASKS_DIR
    tr.TASKS_DIR = tmp_path / "registry"
    yield tmp_path
    tr.TASKS_DIR = orig


def test_set_and_load_point_snapshot(tmp_registry):
    from app.registry.task_registry import set_chapter_point_snapshot, load_chapter_points
    set_chapter_point_snapshot("k1", "1217304708",
                               video_total=2, video_finished=0, has_video=True)
    set_chapter_point_snapshot("k1", "1217304705",
                               video_total=0, video_finished=0, has_video=False)
    pts = load_chapter_points("k1")
    assert pts["1217304708"]["video_total"] == 2
    assert pts["1217304708"]["video_finished"] == 0
    assert pts["1217304708"]["has_video"] is True
    assert pts["1217304705"]["has_video"] is False


def test_merge_done_keeps_and_removes_by_points(tmp_registry):
    """洞2: 有点级快照未完成的章，即使 registry 记 COMPLETED 也不放行。"""
    from app.registry.task_registry import (
        set_chapter_point_snapshot, load_chapter_points,
        save_chapter_points, merge_done_with_points,
    )
    set_chapter_point_snapshot("k", "1217304708",
                               video_total=2, video_finished=0, has_video=True)
    m = load_chapter_points("k")
    m["1217304708"]["video_finished"] = 0      # 未全 finish
    save_chapter_points("k", m)

    done = {"1217304708", "1217304700"}        # registry 把二者都记为完成
    out = merge_done_with_points(done, set(), load_chapter_points("k"))
    assert "1217304708" not in out              # 有未完视频点 → 踢出 done
    assert "1217304700" in out                 # 无快照 → 保留 registry 断言


def test_chapter_done_true_when_all_finished(tmp_registry):
    from app.registry.task_registry import chapter_done_from_snapshot
    pts_all = {"1217304708": {"has_video": True, "video_total": 2, "video_finished": 2}}
    pts_part = {"1217304708": {"has_video": True, "video_total": 2, "video_finished": 1}}
    pts_none = {}
    assert chapter_done_from_snapshot("1217304708", pts_all) is True
    assert chapter_done_from_snapshot("1217304708", pts_part) is False
    assert chapter_done_from_snapshot("1217304708", pts_none) is None


def test_reconcile_points_exclude_non_video_chapter(tmp_registry):
    """洞1(队列级): has_video==False 的章, 即使 registry 有 video task, 也不进 READY。"""
    from app.registry.task_registry import TaskRecord, reconcile_queue
    cid = "1217304705"
    t = TaskRecord(cid, cid, "体系结构", task_type="video")
    t.status = "FAILED"          # 未达阈值 → 否则本来会进 READY
    reg = {cid: t}
    ids1 = {i["task_id"] for i in reconcile_queue("k", reg, set()).items}
    assert cid in ids1                                  # 无快照 → 旧行为（进队列）
    pts_no_video = {cid: {"has_video": False, "video_total": 0, "video_finished": 0}}
    ids2 = {i["task_id"] for i in reconcile_queue(
        "k", reg, set(), points_map=pts_no_video).items}
    assert cid not in ids2                              # 已确认非视频 → 不进待播队列


def test_chapter_video_summary_from_points(tmp_registry):
    """洞3 复用路径: 从同一浏览器已读点级 → 得到 video_total/finished/live_pending。"""
    from tvdp.tdvp import chapter_video_summary, build_live_pending
    cid = "1217304708"
    points = [
        {"task_id": cid, "type": "video", "isFinished": True},          # v1 finished
        {"task_id": f"{cid}:video2", "type": "video", "isFinished": False},  # v2 not
        {"task_id": f"{cid}:quiz", "type": "quiz", "isFinished": False},
    ]
    total, finished = chapter_video_summary(points)
    assert total == 2 and finished == 1
    livep = build_live_pending(points)
    assert f"{cid}:video2" in livep          # 未完成的视频点 → live pending
    assert cid not in livep                  # v1 已 finished → 不在 pending
    assert f"{cid}:quiz" in livep