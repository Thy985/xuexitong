"""人工显式恢复：BLOCKED 点要有合法入口解冻，而不是手改账本 JSON。

背景（2026-09-22）：`1217304738:video2` 连续失败 3 次进 BLOCKED 后，账本里没有任何
入口让它再被投一次 —— 课程级 manual override（`scheduler._blocked_decision`）只管
课程状态，任务级冻结在 `reconcile_queue` 里是硬跳过。缺口后果正是它自己写下的那句
话：被冻住的点是"章内剩余工作量的唯一承载者"，于是整章搁浅。

规则与课程级同族，不放宽 schedule 腿：
  manual   = 人工主动干预 → 允许立即再投一次（本文件）
  schedule = 仍只走服务端真源恢复（`heal_blocked_by_live`），夜巡不悄悄解熔断
"""

from app.registry.reconcile import restore_blocked_for_manual
from app.registry.task_registry import TaskRecord, reconcile_queue

CID = "1217304738"
VID2 = f"{CID}:video2"


def _blocked_point():
    rec = TaskRecord(VID2, CID, "信道复用技术", task_type="video",
                     status="BLOCKED", consecutive_failures=3, attempt_count=3)
    rec.failure.stage = "7_CURRENTTIME_GROWING"
    rec.failure.detail = "video metadata not ready"
    return rec


def _completed_first_point():
    rec = TaskRecord(CID, CID, "信道复用技术", task_type="video",
                     status="COMPLETED")
    rec.verification.level = "SERVER_VERIFIED"
    rec.completion_evidence.passed_object_ids = ["94382be4d0a1c2b3"]
    return rec


# ── TaskRecord.restore_for_manual_retry ──────────────────────────

def test_restore_unfreezes_blocked_point_for_one_more_real_attempt():
    rec = _blocked_point()
    assert rec.restore_for_manual_retry() is True
    assert rec.status == "PENDING"
    assert rec.consecutive_failures == 0, "不清零就仍撞 _task_is_blocked 的 cf>=max 护栏"
    assert rec.attempt_count == 3, "历史尝试次数是留痕，恢复不许抹掉"
    assert rec.failure.stage == "7_CURRENTTIME_GROWING", "上次为什么失败要留在账上"


def test_restore_is_a_no_op_for_records_that_are_not_blocked():
    for status in ("FAILED", "COMPLETED", "PENDING", "UNKNOWN"):
        rec = TaskRecord(f"{CID}:x", CID, "t", status=status,
                         consecutive_failures=2)
        assert rec.restore_for_manual_retry() is False
        assert rec.status == status
        assert rec.consecutive_failures == 2, "非冻结记录不该被清掉失败计数"


# ── restore_blocked_for_manual + 队列真值 ─────────────────────────

def _registry():
    return {CID: _completed_first_point(), VID2: _blocked_point()}


def test_helper_returns_only_the_ids_it_unfroze():
    reg = _registry()
    assert restore_blocked_for_manual(reg) == [VID2]
    assert restore_blocked_for_manual(reg) == [], "二次调用没有可恢复项"


def test_restore_can_be_narrowed_to_the_chapter_the_human_named():
    """一次人工投递不该顺带把别的冻结章（1217304719：headed 下反复抓不到 video）
    放回夜巡队列 —— 那等于替它烧掉课程的失败预算。"""
    other = TaskRecord("1217304719", "1217304719", "点对点协议PPP",
                       status="BLOCKED", consecutive_failures=3)
    reg = dict(_registry(), **{other.task_id: other})
    assert restore_blocked_for_manual(reg, only_chapter=CID) == [VID2]
    assert reg["1217304719"].status == "BLOCKED"


def test_blocked_chapter_is_frozen_until_manual_restore_then_queues_the_point():
    """冻结→恢复→入队三步都要真函数：任何一步假了，`:video2` 仍然永远轮不到。"""
    reg = _registry()
    assert [t["task_id"] for t in reconcile_queue("k", reg).items] == []
    restore_blocked_for_manual(reg)
    assert [t["task_id"] for t in reconcile_queue("k", reg).items] == [VID2]
