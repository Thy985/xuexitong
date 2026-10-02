"""reconcile 的 title 迁移不得吞掉 plain 视频记录（run 36984897107 回归）。

事故：本轮 discovery 对无点级快照的章只产 `<cid>:other`，旧实现把 plain 视频记录
按 title 迁进 `:other`（task_id 被换成另一个任务的 id），随后又被 upgraded 里该
`:other` 的裸记录覆盖（update 顺序：repaired 先、upgraded 后）—— 88 条账本一轮
蒸发 28 条：19 条 SERVER_VERIFIED 完成证据清零、9 个未完成视频任务
（PENDING/DISCOVERED）从台账消失，video-only 队列从此无米下锅，调度器只能反复
重看唯一有点级快照的已完成章。

修复两条腿：
  1. 迁移目标必须是 video discovery 任务；非视频 title 命中只同步目录位置。
  2. 迁移产物与 canonical 记录撞同一新 id 时，带强证据的一方获胜。
"""

from app.registry.reconcile import reconcile_registry
from app.registry.task_registry import (
    TaskRecord, done_chapter_ids_from_registry, reconcile_queue,
)
from tvdp.tdvp import TaskEvidence, TaskInfo


def _other_discovery(cid, title):
    return TaskInfo(f"{cid}:other", cid, title, "other", "PENDING", "UI",
                    "无已确认视频点", TaskEvidence("PENDING", "UI", ""))


def _video_discovery(tid, cid, title):
    return TaskInfo(tid, cid, title, "video", "PENDING", "UI", "x",
                    TaskEvidence("PENDING", "UI", ""))


def _server_verified(rec, oid="oid-1"):
    rec.verification.level = "SERVER_VERIFIED"
    rec.completion_evidence.type = "SERVER_VERIFIED"
    rec.completion_evidence.source = "isPassed"
    rec.completion_evidence.passed_object_ids = [oid]
    return rec


def test_completed_video_record_survives_other_only_discovery():
    """19 条蒸发记录的代表形状（1217304721）：COMPLETED+SERVER_VERIFIED 的 plain
    记录必须原地保留，完成证据原样带走。"""
    cid = "1217304721"
    done = _server_verified(TaskRecord(cid, cid, "使用广播信道的数据链路层",
                                       task_type="video", status="COMPLETED"),
                            oid="be76065d5fe100ce61396121cf66cde8")
    other = TaskRecord(f"{cid}:other", cid, "使用广播信道的数据链路层",
                       task_type="other", status="PENDING")
    fixed, _rep = reconcile_registry(
        "k", {cid: done, f"{cid}:other": other},
        [_other_discovery(cid, "使用广播信道的数据链路层")],
        dom_status={cid: "pending"})

    assert cid in fixed, "plain 视频记录不得被 title 迁移蒸发"
    assert fixed[cid].status == "COMPLETED"
    assert fixed[cid].point_is_server_verified(), "完成证据必须原样保留"
    assert fixed[f"{cid}:other"].status == "PENDING"
    assert fixed[f"{cid}:other"].task_type == "other"


def test_pending_video_record_survives_and_reenters_queue():
    """719 形状：PENDING 视频记录被吞后，该章从此进不了 video-only 队列。"""
    cid = "1217304719"
    pend = TaskRecord(cid, cid, "点对点协议PPP", task_type="video", status="PENDING")
    other = TaskRecord(f"{cid}:other", cid, "点对点协议PPP",
                       task_type="other", status="PENDING")
    fixed, _rep = reconcile_registry(
        "k", {cid: pend, f"{cid}:other": other},
        [_other_discovery(cid, "点对点协议PPP")])

    assert fixed[cid].task_type == "video" and fixed[cid].status == "PENDING"
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    assert [it["task_id"] for it in q.items] == [cid], \
        "待学视频任务必须回到队列（修复前被迁移蒸发，队列常年只剩已完成章）"


def test_legacy_positional_id_still_migrates_into_video_task():
    """护栏不回归：真正的格式迁移（旧位置 id → 新章 id，目标是 video 任务）仍工作。"""
    old = _server_verified(TaskRecord("_gi5", "", "ARP协议",
                                      task_type="video", status="COMPLETED"))
    fixed, _rep = reconcile_registry(
        "k", {"_gi5": old},
        [_video_discovery("1217304736", "1217304736", "ARP协议")])

    assert "_gi5" not in fixed, "旧 key 仍须唯一化淘汰"
    rec = fixed["1217304736"]
    assert rec.status == "COMPLETED" and rec.point_is_server_verified()


def test_migration_with_strong_evidence_wins_over_bare_canonical():
    """同 id 撞车：迁移产物带强证据、canonical 裸 → 采迁移产物（证据合并）。"""
    legacy = _server_verified(TaskRecord("_gi7", "", "RIP协议",
                                         task_type="video", status="COMPLETED"),
                              oid="rip-oid")
    bare = TaskRecord("1217304740", "1217304740", "RIP协议",
                      task_type="video", status="DISCOVERED")
    fixed, _rep = reconcile_registry(
        "k", {"_gi7": legacy, "1217304740": bare},
        [_video_discovery("1217304740", "1217304740", "RIP协议")])

    rec = fixed["1217304740"]
    assert rec.point_is_server_verified(), "带证据的迁移产物不得被裸 canonical 覆盖"
    assert rec.status == "COMPLETED"


def test_bare_migration_does_not_downgrade_strong_canonical():
    """反向撞车：canonical 带强证据 → 保持 canonical，裸迁移产物不得覆盖。"""
    legacy = TaskRecord("_gi8", "", "ICMP协议", task_type="video", status="COMPLETED")
    strong = _server_verified(TaskRecord("1217304737", "1217304737", "ICMP协议",
                                         task_type="video", status="COMPLETED"),
                              oid="icmp-oid")
    fixed, _rep = reconcile_registry(
        "k", {"_gi8": legacy, "1217304737": strong},
        [_video_discovery("1217304737", "1217304737", "ICMP协议")])

    rec = fixed["1217304737"]
    assert rec.point_is_server_verified() and rec.status == "COMPLETED"
