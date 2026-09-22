# -*- coding: utf-8 -*-
"""投递侧证据闸门（ACCEPTANCE §4.17）：决定投 `<cid>:videoN` 的那一问，就地要证据。

§4.16 把清理放在"证据到达处"（E6.2 refine 刚读完的那个章），run 35733572959 实测覆盖率 0：
refine 读的是**预测队首章** 1217304719，投递目标却是重建队列后的 candidates[0]
= 1217304733:video2 —— 被投的那个章从来没有被新鲜读过，prune 自然不动手，于是那一晚
唯一次投递又花在撞幻影上（18.7s FAIL，`done` 不动，聚合仍 SUCCESS）。

闸门因此必须钉在**决策点**。三条判据（本文件全部 L1）：
  1. 记录自己带证据（服务端确认过点 / 真跑过）→ 直接投，一次读数都不多花；
  2. 否则看这一次新鲜读数：≥N 投、1..N-1 收（走生产原语，不在这里新造删除写点）、
     读不到就**照投** —— "没有证据"和"证据说没有"是两件事，前者投错只损失一晚，
     后者不投会把真实学习量永久饿死（1217304741:video2 就是本次实测里的真点）。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.registry.reconcile import (
    EVIDENCE_ALLOW,
    EVIDENCE_PRUNE,
    EVIDENCE_UNKNOWN,
    dispatch_gate_decision,
    point_evidence_verdict,
    record_has_own_point_evidence,
)
from app.registry.task_registry import TaskRecord

CID = "1217304741"


def _rec(tid, status="DISCOVERED", cf=0, att=0, level="NONE", pids=()):
    r = TaskRecord(tid, tid.split(":")[0], "OSPF协议")
    r.status = status
    r.consecutive_failures = cf
    r.attempts = att
    r.verification.level = level
    r.completion_evidence.passed_object_ids = list(pids)
    return r


# ── 1. 记录自己带不带证据 ──────────────────────────────────────────
def test_server_verified_sibling_with_object_ids_carries_its_own_evidence():
    """`:videoN` 曾被服务端确认过（有 objectid）= 这个点被看见过，不必再问。"""
    r = _rec(f"{CID}:video2", status="COMPLETED", att=5,
             level="SERVER_VERIFIED", pids=["19da22cc12345678"])
    assert record_has_own_point_evidence(r) is True


def test_a_real_run_attempt_is_evidence_even_without_server_confirmation():
    """跑过（attempts>0）说明引擎真到过那一段：证据已经产生，只是结果未确认。"""
    assert record_has_own_point_evidence(_rec(f"{CID}:video2", att=1)) is True
    assert record_has_own_point_evidence(_rec(f"{CID}:video2", cf=1)) is True


def test_a_minted_but_never_seen_sibling_has_no_evidence():
    """§4.17 里那 6 条的形状：DISCOVERED / NONE / 零尝试零失败 —— 纯断言，没被看见过。"""
    assert record_has_own_point_evidence(_rec(f"{CID}:video2")) is False


# ── 2. 新鲜读数怎么裁决 ───────────────────────────────────────────
def test_fresh_read_at_or_above_the_sequence_allows_the_dispatch():
    assert point_evidence_verdict(2, observed=2) == EVIDENCE_ALLOW
    assert point_evidence_verdict(2, observed=5) == EVIDENCE_ALLOW


def test_fresh_read_below_the_sequence_prunes():
    """实测评过的形状：4733/4734/4737/4750/4751 各 1 点，却挂着 `:video2`。"""
    assert point_evidence_verdict(2, observed=1) == EVIDENCE_PRUNE
    assert point_evidence_verdict(3, observed=2) == EVIDENCE_PRUNE


def test_no_read_is_not_the_same_as_a_negative_read():
    """读不到 → 照投。反证：若把 UNKNOWN 当 PRUNE，一次 cards 帧抖动就会吃掉真实点；
    若把 UNKNOWN 当"跳过不投"，真点会在每一轮都被跳过 —— 永久饿死。"""
    assert point_evidence_verdict(2, observed=None) == EVIDENCE_UNKNOWN
    assert point_evidence_verdict(2, observed=0) == EVIDENCE_UNKNOWN
    assert point_evidence_verdict(2, observed=-1) == EVIDENCE_UNKNOWN


# ── 3. 两条判据叠起来：4741 那个真点在只有陈旧快照时仍会被投 ──────
def test_real_sibling_survives_the_gate(tmp_path, monkeypatch):
    """本次实测的正例：4741 新鲜读数 2 点 / finished 1 → `:video2` 是真工作。

    闸门判 ALLOW 的必须是"证据"而不是"猜测"：同一条记录在没有读数时也只能靠 UNKNOWN
    的照投分支通过，绝不会被误判成 PRUNE。
    """
    rec = _rec(f"{CID}:video2")
    assert record_has_own_point_evidence(rec) is False
    assert point_evidence_verdict(2, observed=2) == EVIDENCE_ALLOW
    assert point_evidence_verdict(2, observed=None) == EVIDENCE_UNKNOWN
    assert point_evidence_verdict(2, observed=1) == EVIDENCE_PRUNE


# ── 4. 两条判据的合成（scheduler 只负责取读数，判定全在这里）───────
def test_own_evidence_short_circuits_so_no_extra_browser_read_is_spent():
    rec = _rec(f"{CID}:video2", status="COMPLETED", att=4,
               level="SERVER_VERIFIED", pids=["19da22cc12345678"])
    assert dispatch_gate_decision(rec, video_index=2, observed=None) == EVIDENCE_ALLOW


def test_unevidenced_sibling_is_decided_by_the_fresh_read_alone():
    rec = _rec(f"{CID}:video2")
    assert dispatch_gate_decision(rec, video_index=2, observed=None) == EVIDENCE_UNKNOWN
    assert dispatch_gate_decision(rec, video_index=2, observed=2) == EVIDENCE_ALLOW
    assert dispatch_gate_decision(rec, video_index=2, observed=1) == EVIDENCE_PRUNE


def test_the_gate_never_touches_a_chapters_first_point():
    """`<cid>`（index=1）是章自己的记录，不是 mint 出来的兄弟断言，没有可质疑的东西。"""
    rec = _rec(CID)
    assert dispatch_gate_decision(rec, video_index=1, observed=None) == EVIDENCE_ALLOW
    assert dispatch_gate_decision(rec, video_index=1, observed=1) == EVIDENCE_ALLOW
    assert dispatch_gate_decision(rec, video_index=1, observed=0) == EVIDENCE_ALLOW


# ── 5. 花读数之前先问"要不要花"（§4.17 生产侧的浏览器成本就在这一步）──
def test_only_an_unevidenced_sibling_is_worth_a_deep_read():
    from app.registry.reconcile import dispatch_gate_needs_read
    assert dispatch_gate_needs_read(_rec(f"{CID}:video2"), video_index=2) is True
    assert dispatch_gate_needs_read(_rec(CID), video_index=1) is False
    assert dispatch_gate_needs_read(
        _rec(f"{CID}:video2", att=4, level="SERVER_VERIFIED",
             pids=["19da22cc12345678"]), video_index=2) is False


# ── 6. 决策点上那一层接线（scheduler._dispatch_evidence_gate）────────
# §4.16 的教训就是"判据有 L1、接线没有，只能等真站"。这一层用注入读数的方式钉住：
# 什么时候该开一次深读、什么时候不该、收掉之后要不要换候选。
from scheduler.scheduler import _dispatch_evidence_gate   # noqa: E402


def _cand(tid):
    return {"task_id": tid, "chapter_id": tid.split(":")[0], "priority": 0,
            "state": "READY"}


def test_gate_prunes_the_sibling_a_fresh_read_refutes_and_moves_to_the_next():
    """run 35733572959 的形状重演：队首是 `:video2`，新鲜读数说该章只有 1 点。"""
    notes = []
    reg = {CID: _rec(CID, status="COMPLETED"), f"{CID}:video2": _rec(f"{CID}:video2")}
    rebuilt = []

    def rebuild():
        rebuilt.append(1)
        return [_cand("1217304719")]

    out = _dispatch_evidence_gate([_cand(f"{CID}:video2"), _cand("1217304719")], reg,
                                  read_points=lambda cid: 1, rebuild=rebuild,
                                  note=lambda m: notes.append(m))
    assert [c["task_id"] for c in out] == ["1217304719"]
    assert f"{CID}:video2" not in reg, "幻影记录应当场收掉，不是留着让引擎撞"
    assert rebuilt == [1] and any("DISPATCH-GATE" in m for m in notes)


def test_gate_spends_no_extra_read_on_the_chapters_own_record():
    calls = []
    reg = {CID: _rec(CID, status="PENDING")}
    out = _dispatch_evidence_gate([_cand(CID)], reg,
                                  read_points=lambda c: calls.append(c) or 1,
                                  rebuild=lambda: [], note=lambda m: None)
    assert [c["task_id"] for c in out] == [CID]
    assert calls == [], "章自己的点不该触发任何深读"


def test_gate_spends_no_extra_read_on_a_sibling_that_was_really_seen():
    calls = []
    rec = _rec(f"{CID}:video2", status="COMPLETED", att=4,
               level="SERVER_VERIFIED", pids=["19da22cc12345678"])
    out = _dispatch_evidence_gate([_cand(f"{CID}:video2")], {rec.task_id: rec},
                                  read_points=lambda c: calls.append(c) or None,
                                  rebuild=lambda: [], note=lambda m: None)
    assert out[0]["task_id"] == f"{CID}:video2"
    assert calls == []


def test_gate_dispatches_a_real_sibling_when_the_read_supports_it():
    """§4.17 实测的正例：4741 新鲜读数 2 点 → `:video2` 照投，记录一条都不能少。"""
    reg = {CID: _rec(CID, status="COMPLETED"), f"{CID}:video2": _rec(f"{CID}:video2")}
    out = _dispatch_evidence_gate([_cand(f"{CID}:video2")], reg,
                                  read_points=lambda cid: 2, rebuild=lambda: [],
                                  note=lambda m: None)
    assert out[0]["task_id"] == f"{CID}:video2"
    assert f"{CID}:video2" in reg


def test_gate_dispatches_anyway_when_there_is_nothing_to_read():
    """"读不到"必须走照投分支，并在日志里说清 —— 它和"证据说没有"是两件事。"""
    notes = []
    reg = {CID: _rec(CID, status="COMPLETED"), f"{CID}:video2": _rec(f"{CID}:video2")}
    out = _dispatch_evidence_gate([_cand(f"{CID}:video2")], reg,
                                  read_points=lambda cid: None,
                                  rebuild=lambda: [], note=notes.append)
    assert out[0]["task_id"] == f"{CID}:video2"
    assert f"{CID}:video2" in reg, "没有证据不许删"
    assert any("照投" in m for m in notes)
