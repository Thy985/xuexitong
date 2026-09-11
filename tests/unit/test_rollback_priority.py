"""回退优先调度：服务器把已完成的视频推翻（rollback）后，reconcile_queue 须
优先补齐该章，而不是着急开新课；且普通 UNKNOWN（非回退）仍不自动执行。"""
from app.registry.task_registry import (
    TaskRecord, reconcile_queue, done_chapter_ids_from_registry,
)


def test_downgrade_to_unknown_marks_rollback():
    from app.registry.reconcile import downgrade_to_unknown
    t = TaskRecord("A", "c1", "T", task_type="video", status="COMPLETED")
    downgrade_to_unknown(t)
    assert t.status == "UNKNOWN"
    assert t.rollback_count == 1


def test_rollback_unknown_is_prioritized_before_fresh_chapter():
    """回退章（UNKNOWN+rollback>0）应排在全新 DISCOVERED 章之前。"""
    rollback = TaskRecord("1217304733", "1217304733", "IP数据报的格式",
                          task_type="video", status="UNKNOWN")
    rollback.mark_rollback()      # 服务器回退过一次
    fresh = TaskRecord("1217304736", "1217304736", "ARP协议",
                       task_type="video", status="DISCOVERED")
    q = reconcile_queue("k", {"1217304733": rollback, "1217304736": fresh},
                        done_chapter_ids_from_registry(
                            {"1217304733": rollback, "1217304736": fresh}))
    ids = [it["task_id"] for it in q.items]
    assert ids[0] == "1217304733"          # 回退章最先
    assert ids[1] == "1217304736"


def test_plain_unknown_still_not_requeued():
    """非回退的 UNKNOWN 仍不自动执行（保持既有语义）。"""
    unknown = TaskRecord("X", "chX", "未分类", task_type="video", status="UNKNOWN")
    far = TaskRecord("Y", "chY", "新课", task_type="video", status="PENDING")
    q = reconcile_queue("k", {"X": unknown, "Y": far},
                        done_chapter_ids_from_registry({"X": unknown, "Y": far}))
    ids = [it["task_id"] for it in q.items]
    assert "X" not in ids            # 非回退 UNKNOWN 不入队
    assert "Y" in ids


# 小助手：避免顶名与 from_dict 纠缠，直接构造
def TaskRecordTask(task_id, cid, title, **kw):
    return TaskRecord(task_id, cid, title, **kw)