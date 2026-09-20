"""降级校准必须落在"点"上：目录计数不能推翻已核验的视频点，兄弟点该扛住未完成量。

第 3 轮 M0 验证的根因（ACCEPTANCE §4.5）：
  1. `stale_completed_by_catalog` 用目录的 `job_remaining>0` 降级 COMPLETED video 记录，
     而 `job_remaining` 计的是**整章所有任务点**（含达标测试/PPT —— 引擎永远做不了它们）。
     结果：9 章 COMPLETED→STALE→UNKNOWN→重新排队，账本震荡，且与账本回填互相抵消。
  2. `stale_completed_by_points` 用的是**章级**快照（video_total/video_finished），
     却把它套在**单个点**的记录上：708 有 2 个视频点、#1 已过、#2 未做 —— 降级的是 #1
     那条记录，而 `1217304708:video2` 永远轮不到，于是每次 run 重播 #1。

两条保护都要留住：4708「双视频只播 1 个却被判完成」不能因为这次改动而回归。
"""

from app.registry.reconcile import (has_unfinished_video_sibling,
                                    stale_completed_by_catalog,
                                    stale_completed_by_points)
from app.registry.task_registry import TaskRecord

CID = "1217304708"


def _video(status="COMPLETED", tid=CID, cid=CID):
    return TaskRecord(tid, cid, "数据通信基础知识", task_type="video", status=status)


def _catalog(remaining=1, cid=CID):
    return [{"chapter_id": cid, "job_remaining": remaining}]


def _snap(video_total, video_finished, cid=CID):
    return {cid: {"video_total": video_total, "video_finished": video_finished,
                  "has_video": True}}


# ── 1) 目录计数不得推翻"视频点已全完成" ──────────────────────────

def test_catalog_stale_is_overridden_by_finished_video_snapshot():
    """快照说视频点全 finished：剩下的 job_remaining 必是非视频点，不许再降级。"""
    existing = {CID: _video()}
    out = stale_completed_by_catalog(existing, _catalog(remaining=1),
                                     points_map=_snap(1, 1))
    assert out == [], f"视频点已尽，目录计数不该推翻它，实际 {out}"


def test_catalog_stale_still_applies_when_video_points_unfinished():
    """快照显示还有视频点未 finished → 4708 类保护必须继续生效。"""
    existing = {CID: _video()}
    out = stale_completed_by_catalog(existing, _catalog(remaining=1),
                                     points_map=_snap(2, 1))
    assert out == [CID]


def test_catalog_stale_without_snapshot_keeps_old_behaviour():
    """从没读过该章点级 → 目录是唯一信号，保守降级（不因新规则放宽）。"""
    existing = {CID: _video()}
    assert stale_completed_by_catalog(existing, _catalog(1)) == [CID]
    assert stale_completed_by_catalog(existing, _catalog(1), points_map={}) == [CID]


def test_catalog_does_not_touch_chapters_without_remaining_points():
    existing = {CID: _video()}
    out = stale_completed_by_catalog(existing, [{"chapter_id": CID, "job_remaining": 0}],
                                     points_map=_snap(1, 1))
    assert out == []


# ── 2) 章级未完成量要由"兄弟点"扛，而不是重播已完成的点 ──────────

def test_sibling_carries_the_unfinished_point():
    existing = {CID: _video(),
                f"{CID}:video2": _video(status="DISCOVERED", tid=f"{CID}:video2")}
    assert has_unfinished_video_sibling(existing, existing[CID]) is True


def test_no_sibling_when_no_other_video_record():
    existing = {CID: _video()}
    assert has_unfinished_video_sibling(existing, existing[CID]) is False


def test_non_video_sibling_does_not_count():
    existing = {CID: _video(),
                f"{CID}:other": TaskRecord(f"{CID}:other", CID, "扩展阅读",
                                           task_type="other", status="PENDING")}
    assert has_unfinished_video_sibling(existing, existing[CID]) is False


def test_completed_sibling_does_not_count():
    existing = {CID: _video(),
                f"{CID}:video2": _video(status="COMPLETED", tid=f"{CID}:video2")}
    assert has_unfinished_video_sibling(existing, existing[CID]) is False


def test_points_stale_skips_finished_point_when_sibling_is_pending():
    """708 实况：#1 已过、:video2 待做 → 该重排的是 :video2，不是重播 #1。"""
    existing = {CID: _video(),
                f"{CID}:video2": _video(status="DISCOVERED", tid=f"{CID}:video2")}
    out = stale_completed_by_points(existing, _snap(2, 1))
    assert out == [], f"已完成点不该被重播，实际 {out}"


def test_points_stale_keeps_4708_protection_without_sibling():
    existing = {CID: _video()}
    assert stale_completed_by_points(existing, _snap(2, 1)) == [CID]


# ── 3) 章级计数不得推翻"该点已被服务端确认" ───────────────────────
# 快照缓存可能没预热（chapter_points.json 会被清），所以这条不依赖快照：
# 记录自己带 SERVER_VERIFIED + passed_object_ids，就是**该点**的点级真源，
# 而 job_remaining 是**整章**的粗读数，粗读数不能推翻细读数。

def _verified(tid=CID, status="COMPLETED"):
    t = _video(status=status, tid=tid)
    t.verification.level = "SERVER_VERIFIED"
    t.completion_evidence.passed_object_ids = ["96bf782cc04a753ee2efe6785431f8b2"]
    return t


def test_catalog_cannot_stale_a_server_verified_point_without_snapshot():
    existing = {CID: _verified()}
    out = stale_completed_by_catalog(existing, _catalog(remaining=3), points_map={})
    assert out == [], f"章级计数不能推翻点级服务端确认，实际 {out}"


def test_catalog_does_not_stale_verified_point_even_with_open_sibling():
    """已确认点不降级；该章另一个视频点仍由兄弟记录承载（4708 保护不受影响）。"""
    existing = {CID: _verified(),
                f"{CID}:video2": _video(status="DISCOVERED", tid=f"{CID}:video2")}
    assert stale_completed_by_catalog(existing, _catalog(1), points_map={}) == []


def test_catalog_stales_unverified_completed_on_catalog_signal():
    """没有点级服务端确认时，目录计数仍是有效信号（不放水）。"""
    existing = {CID: _video(status="COMPLETED")}
    out = stale_completed_by_catalog(existing, _catalog(1), points_map={})
    assert out == [CID]
