# BLOCKED 任务的「服务端真源」恢复路径（方案1，2026-09-21 用户选定）。
#
# 场景：用户手动看完了 1217304708 的点 2，服务端已把该点判 finished —— 但
# `:video2` 因 P1 缺陷被熔断成 BLOCKED。旧护栏（test_reconcile_blocked_preserved）
# 一律冻结 BLOCKED，是对的；但 replay 无法产生"真实成功事件"（点已完成，页面
# 不会播它）。恢复的合法事件不是 replay，而是**服务端自己的 finished 判定**：
# live job points 读到该点 finished → 以 SERVER_VERIFIED 证据置 COMPLETED。
# 点身份必须精确匹配（<cid>:videoN ↔ 该章第 N 个视频点），兄弟点完成不算。

import pytest

from app.registry.task_registry import (
    TaskRecord, done_chapter_ids_from_registry, reconcile_queue,
)
from app.registry.reconcile import (
    heal_blocked_by_live,
    pick_conflict_chapters,
    reconcile_registry,
)
from tvdp.tdvp import TaskEvidence, TaskInfo, build_live_finished


def _blocked_record(tid, cid, title="双视频章"):
    rec = TaskRecord(tid, cid, title)
    rec.status = "BLOCKED"
    rec.consecutive_failures = 4
    rec.max_attempts = 3
    return rec


def _discovery(tid, cid):
    return TaskInfo(tid, cid, "双视频章", "video",
                    "PENDING", "UI", "x", TaskEvidence("PENDING", "UI", ""))


def test_blocked_point_healed_when_server_marks_it_finished():
    old = _blocked_record("1217304708:video2", "1217304708")
    fixed, rep = reconcile_registry(
        "k", {"1217304708:video2": old},
        [_discovery("1217304708:video2", "1217304708")],
        {"1217304708": "completed"},
        live_finished={"1217304708:video2"})
    rec = fixed["1217304708:video2"]
    assert rec.status == "COMPLETED"
    assert rec.verification.level == "SERVER_VERIFIED"
    assert rec.completion_evidence.type == "SERVER_VERIFIED"
    assert rec.consecutive_failures == 4      # 留痕：曾连续失败 4 次，不清史
    assert rep.repair_map["1217304708:video2"]["after"] == "COMPLETED"


def test_blocked_point_stays_frozen_without_server_truth():
    # 没有 live finished 证据时，护栏原样保留（防止回到无限重跑老问题）
    old = _blocked_record("1217304719", "1217304719")
    fixed, _ = reconcile_registry(
        "k", {"1217304719": old}, [_discovery("1217304719", "1217304719")],
        {"1217304719": "pending"}, live_finished=set())
    assert fixed["1217304719"].status == "BLOCKED"

    fixed2, _ = reconcile_registry(
        "k", {"1217304719": old}, [_discovery("1217304719", "1217304719")],
        {"1217304719": "pending"})             # live_finished 缺省 None
    assert fixed2["1217304719"].status == "BLOCKED"


def test_sibling_point_being_finished_does_not_heal():
    # 点身份精确匹配：兄弟点（:video1=<cid>）finished 不解冻 :video2
    old = _blocked_record("1217304708:video2", "1217304708")
    fixed, _ = reconcile_registry(
        "k", {"1217304708:video2": old},
        [_discovery("1217304708:video2", "1217304708")],
        {"1217304708": "completed"},
        live_finished={"1217304708"})
    assert fixed["1217304708:video2"].status == "BLOCKED"


def test_healed_point_unfreezes_its_chapter():
    # 恢复后章级冻结解除：章内其余 PENDING 的 video 任务可以正常入队。
    # （dom 不给 completed —— 否则既有语义会把兄弟点一并 UI 升为 COMPLETED。）
    old = _blocked_record("1217304708:video2", "1217304708")
    sibling = TaskRecord("1217304708:video3", "1217304708", "双视频章", "video")
    sibling.status = "PENDING"
    fixed, _ = reconcile_registry(
        "k", {"1217304708:video2": old, "1217304708:video3": sibling},
        [_discovery("1217304708:video2", "1217304708"),
         _discovery("1217304708:video3", "1217304708")],
        {},
        live_finished={"1217304708:video2"})
    assert fixed["1217304708:video2"].status == "COMPLETED"
    assert fixed["1217304708:video3"].status == "PENDING"
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    qids = {i["task_id"] for i in q.items}
    assert "1217304708:video3" in qids


# ── live 读数 → finished 集合 ────────────────────────────────────────

def test_build_live_finished_collects_finished_point_ids():
    pts = [
        {"task_id": "1217304708", "type": "video", "isFinished": True},
        {"task_id": "1217304708:video2", "type": "video", "isFinished": True},
        {"task_id": "1217304708:work", "type": "homework", "isFinished": False},
    ]
    assert build_live_finished(pts) == {"1217304708", "1217304708:video2"}


# ── L2 成本梯度：有 BLOCKED 的章也进入 live 复核范围 ─────────────────

def test_pick_conflict_chapters_includes_chapter_with_blocked_record():
    existing = {"1217304708:video2": _blocked_record(
        "1217304708:video2", "1217304708")}
    assert pick_conflict_chapters(["1217304708"], existing,
                                  dom_pending=set()) == ["1217304708"]


def test_pick_conflict_chapters_ignores_healthy_chapters():
    healthy = TaskRecord("1217304708", "1217304708", "双视频章", "video")
    healthy.status = "COMPLETED"
    existing = {"1217304708": healthy}
    assert pick_conflict_chapters(["1217304708"], existing,
                                  dom_pending=set()) == []


# ── 生产封装：只对冻结章做 live 读数 → 恢复 ──────────────────────────

def _pending_video(tid, cid):
    rec = TaskRecord(tid, cid, "双视频章", "video")
    rec.status = "PENDING"
    return rec


def test_heal_blocked_by_live_reads_only_frozen_chapters_and_heals():
    calls = []

    def verify(cid):
        calls.append(cid)
        if cid == "1217304708":
            return [{"task_id": "1217304708:video2", "type": "video",
                     "isFinished": True}]
        return None

    existing = {
        "1217304708:video2": _blocked_record("1217304708:video2",
                                             "1217304708"),
        "1217304709": _pending_video("1217304709", "1217304709"),
    }
    fixed, rep = heal_blocked_by_live("k", existing, [], {}, verify)
    assert calls == ["1217304708"], "只读冻结章，健康章不烧 L2 成本"
    assert fixed["1217304708:video2"].status == "COMPLETED"
    assert fixed["1217304709"].status == "PENDING"   # 无关记录原样保留
    assert rep.healed_by_server == 1


def test_heal_blocked_by_live_is_noop_when_nothing_frozen():
    calls = []

    def verify(cid):
        calls.append(cid)
        return None

    existing = {"1217304709": _pending_video("1217304709", "1217304709")}
    fixed, rep = heal_blocked_by_live("k", existing, [], {}, verify)
    assert calls == []
    assert rep.healed_by_server == 0
    assert fixed["1217304709"].status == "PENDING"


def test_heal_blocked_by_live_keeps_frozen_when_live_read_fails():
    # live 读数失败（页面没渲染出点）→ 保持冻结，不误恢复
    calls = []

    def verify(cid):
        calls.append(cid)
        return None

    existing = {"1217304708:video2": _blocked_record("1217304708:video2",
                                                     "1217304708")}
    fixed, rep = heal_blocked_by_live("k", existing, [], {}, verify)
    assert calls == ["1217304708"]
    assert fixed["1217304708:video2"].status == "BLOCKED"
    assert rep.healed_by_server == 0
