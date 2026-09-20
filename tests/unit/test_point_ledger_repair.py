"""点级账本修复：UNKNOWN/STALE 回 COMPLETED 必须同时有点级真源，不能只看记录自述。

第 3 轮 M0 之后有 9 章被 `stale_completed_by_catalog` 从 COMPLETED 打成 UNKNOWN。
止住后续翻转（reconcile 的改动）不等于把已翻的翻回来 —— 那需要**该章视频点已全部
finished** 的点级证据。

为什么不能只用 `point_is_server_verified`：它只证明"该点"过了，不证明"该章没有别的
视频点待做"。快照缓存（chapter_points.json）此刻是冷的（先前键全是 repr，已清空），
冷缓存下直接恢复会让 4708「双视频只播 1 个」那类章被当成已完成而跳过。
所以：**没有点级快照就不动账**，宁可少修，不可假修。
"""

from scripts.diag_video_points_ledger import observe_points, plan_point_repair
from app.registry.task_registry import TaskRecord

CID = "1217304722"
OBJ = ["96bf782cc04a753ee2efe6785431f8b2"]


def _rec(status="UNKNOWN", verified="SERVER_VERIFIED", ids=OBJ, tid=CID,
         task_type="video"):
    t = TaskRecord(tid, CID, "信道复用技术", task_type=task_type, status=status)
    t.verification.level = verified
    t.completion_evidence.passed_object_ids = list(ids)
    return t


def _snap(video_total, video_finished):
    return {CID: {"video_total": video_total, "video_finished": video_finished,
                  "has_video": True}}


def test_restores_when_snapshot_proves_all_video_points_finished():
    rows = plan_point_repair({CID: _rec()}, _snap(1, 1))
    assert rows == [{"task_id": CID, "action": "restore_completed",
                     "from": "UNKNOWN", "video_total": 1, "video_finished": 1}]


def test_no_snapshot_means_no_repair():
    """冷缓存下不动账 —— 少修可以补，假修会把未完成章藏掉。"""
    assert plan_point_repair({CID: _rec()}, {})[0]["action"] == "needs_evidence"
    assert plan_point_repair({CID: _rec()}, _snap(0, 0))[0]["action"] == "needs_evidence"


def test_unfinished_video_points_are_left_for_the_queue():
    rows = plan_point_repair({CID: _rec()}, _snap(2, 1))
    assert rows[0]["action"] == "keep_queued"


def test_unverified_record_is_not_restored():
    for rec in (_rec(verified="UI"), _rec(ids=[]), _rec(verified="NONE")):
        rows = plan_point_repair({CID: rec}, _snap(1, 1))
        assert rows[0]["action"] == "keep_queued", f"{rec.verification.level}/{rec.completion_evidence.passed_object_ids}"


def test_completed_record_is_not_touched():
    assert plan_point_repair({CID: _rec(status="COMPLETED")}, _snap(1, 1)) == []


def test_non_video_record_is_not_touched():
    rec = _rec(status="PENDING", task_type="other")
    assert plan_point_repair({f"{CID}:other": rec}, _snap(1, 1)) == []


def test_blocked_record_is_left_alone():
    """BLOCKED 是熔断态，归熔断逻辑管，不由点级修复翻案。"""
    assert plan_point_repair({CID: _rec(status="BLOCKED")}, _snap(1, 1)) == []


def test_stale_status_is_also_repairable():
    assert plan_point_repair({CID: _rec(status="STALE")}, _snap(1, 1))[0][
        "action"] == "restore_completed"


# ── 读数形状：修复要的是点级 finished，不只 total ──────────────────

def test_observe_points_reports_finished_alongside_total():
    pts = [{"type": "video", "isFinished": True},
           {"type": "video", "isFinished": False}]
    assert observe_points(pts) == {"video_total": 2, "video_finished": 1,
                                   "points_seen": 2}


def test_observe_points_on_empty_read_is_a_measurement_gap():
    """空读数必须原样是 0 —— 由 plan_* 判成 needs_evidence，不得当成"无视频"。"""
    assert observe_points([]) == {"video_total": 0, "video_finished": 0,
                                  "points_seen": 0}
    assert observe_points(None) == {"video_total": 0, "video_finished": 0,
                                    "points_seen": 0}
