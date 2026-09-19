"""账本修复的判据：只有服务端真读到视频点才允许改回 video。

背景：E6.2 的 head_cid 缺陷把真实视频章静默降级成 task_type=other/PENDING。
修 head_cid 只阻止继续损坏；已损坏的 22 章要按服务端真源逐章核对。
这里钉住核对结论到"改不改账"的映射 —— 关键是**没测到**绝不能当成"确认非视频"，
否则就是用同一个测量失误去修上一个测量失误。
"""

import importlib

import pytest

mod = importlib.import_module("scripts.diag_video_points_ledger")
plan_restorations = mod.plan_restorations


def _srv(video_total, points_seen):
    return {"video_total": video_total, "points_seen": points_seen}


def test_server_shows_video_restores_the_record():
    rows = plan_restorations(["1217304750"], {"1217304750": _srv(2, 3)})
    assert rows == [{"chapter_id": "1217304750", "action": "restore_video",
                     "video_total": 2, "points_seen": 3}]


def test_confirmed_non_video_stays_other():
    rows = plan_restorations(["1217304705"], {"1217304705": _srv(0, 4)})
    assert rows[0]["action"] == "keep_other"


def test_unmeasured_chapter_is_never_touched():
    """points_seen=0 代表没读到任何点（探测失败），不是"该章无视频"。"""
    assert plan_restorations(["1"], {"1": _srv(0, 0)})[0]["action"] == "unknown"
    assert plan_restorations(["1"], {"1": None})[0]["action"] == "unknown"
    assert plan_restorations(["1"], {})[0]["action"] == "unknown"


def test_only_the_demoted_records_are_planned():
    server = {"1": _srv(1, 1), "2": _srv(1, 1)}
    rows = plan_restorations(["1"], server)
    assert [r["chapter_id"] for r in rows] == ["1"]


def test_empty_input_yields_no_rows():
    assert plan_restorations([], {"1": _srv(1, 1)}) == []


@pytest.mark.parametrize("video_total,points_seen,action", [
    (1, 1, "restore_video"),
    (3, 5, "restore_video"),
    (0, 1, "keep_other"),
    (0, 0, "unknown"),
])
def test_decision_table(video_total, points_seen, action):
    rows = plan_restorations(["x"], {"x": _srv(video_total, points_seen)})
    assert rows[0]["action"] == action
