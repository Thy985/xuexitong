# -*- coding: utf-8 -*-
"""幻影 `:videoN` 的纠正链路（ACCEPTANCE §4.13 / R7）。

真站事实：`1217304730:video2` 在页面上不存在（引擎枚举 1、cards DOM 标记 1、服务端 live 点读 1，
对照章 4738 三路人手都 2），却被投递并 15.9s 判 `FAIL(target video point 2 not on page; points=1)`，
于是**账本自己的错被记成一次真实播放失败** —— cf 攒到 3 就整章冻结（4719 疑为同形）。

钉四件事：
  1. 策略纯函数：什么观测值才允许纠正（0 个点 = 没测到，不许拿"没测到"当"测到 0 个"）；
  2. 清理：按观测删掉 K>observed 的点级记录，**带失败计数的也删**（原 D13 因 cf>0 而跳过，
     正是这里漏掉的形态），但绝不删带证据的 COMPLETED；
  3. 快照纠正：`chapter_points.json` 的 video_total 改到观测值，否则下一轮 reconcile
     又会按老快照把幻影点重新 mint 出来；
  4. 端到端保证：纠正之后 `build_tasks_from_discovery` 不再产出 `:video2`。
"""
from unittest.mock import patch

import pytest

import app.registry.task_registry as tr
from app.registry.reconcile import (
    phantom_correction_policy,
    prune_phantom_video_points,
)
from app.registry.task_registry import (
    TaskRecord,
    load_chapter_points,
    set_chapter_point_snapshot,
    video_counts_from_points,
    video_total_from_observation,
)
from tvdp.tdvp import build_tasks_from_discovery

CID = "1217304730"
KEY = "265997861_151695658"


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """账本与快照都必须落在 tmp —— 这条测试跑在真实 worktree 里过，就会写坏真账。"""
    monkeypatch.setattr(tr, "TASKS_DIR", tmp_path / "state" / "registry")
    return tmp_path


def _rec(tid, status="DISCOVERED", cf=0):
    r = TaskRecord(tid, CID, "分类的IP地址")
    r.status = status
    r.consecutive_failures = cf
    return r


# ── 1. 策略 ─────────────────────────────────────────────────────────
def test_policy_returns_observed_count_for_a_phantom_target():
    assert phantom_correction_policy("TARGET_NOT_ON_PAGE", video_index=2,
                                     observed_points=1) == 1


def test_policy_refuses_to_trust_a_zero_observation():
    """0 个点 = cards 帧没读到（登录墙/瞬态），不是"该章真的没有视频"。"""
    assert phantom_correction_policy("TARGET_NOT_ON_PAGE", video_index=2,
                                     observed_points=0) is None


def test_policy_silent_when_target_is_within_observed_range():
    assert phantom_correction_policy("TARGET_NOT_ON_PAGE", video_index=1,
                                     observed_points=1) is None
    assert phantom_correction_policy("TARGET_NOT_ON_PAGE", video_index=2,
                                     observed_points=3) is None


def test_policy_silent_for_real_playback_failures():
    assert phantom_correction_policy("NO_CARDS_IFRAME", video_index=2,
                                     observed_points=1) is None
    assert phantom_correction_policy("", video_index=2, observed_points=1) is None


# ── 2. 清理 ─────────────────────────────────────────────────────────
def test_prune_removes_out_of_range_records_even_with_failure_count():
    existing = {CID: _rec(CID, "COMPLETED"),
                f"{CID}:video2": _rec(f"{CID}:video2", "FAILED", cf=1),
                f"{CID}:video3": _rec(f"{CID}:video3", "DISCOVERED")}

    pruned = prune_phantom_video_points(existing, chapter_id=CID, observed=1)

    assert sorted(pruned) == [f"{CID}:video2", f"{CID}:video3"]
    assert list(existing) == [CID]


def test_prune_keeps_in_range_records_and_evidence_bearing_ones():
    done2 = _rec(f"{CID}:video2", "COMPLETED")
    done2.completion_evidence.type = "SERVER_VERIFIED"
    existing = {f"{CID}:video2": done2, f"{CID}:video3": _rec(f"{CID}:video3")}

    pruned = prune_phantom_video_points(existing, chapter_id=CID, observed=2)

    assert pruned == [f"{CID}:video3"]
    assert f"{CID}:video2" in existing


def test_prune_does_nothing_for_chapters_it_is_not_told_about():
    other = TaskRecord("9:video9", "9", "别的章")
    existing = {"9:video9": other}

    assert prune_phantom_video_points(existing, chapter_id=CID, observed=1) == []
    assert "9:video9" in existing


# ── 3. 快照纠正 ─────────────────────────────────────────────────────
def test_snapshot_correction_lowers_video_total_and_clamps_finished(tmp_state):
    set_chapter_point_snapshot(KEY, CID, video_total=2, video_finished=1, has_video=True)

    changed = video_total_from_observation(KEY, CID, observed=1,
                                           reason="engine enumerated 1 point")

    snap = load_chapter_points(KEY)[CID]
    assert changed is True
    assert snap["video_total"] == 1
    assert snap["video_finished"] == 1          # min(1, 1)：不倒挂
    assert snap["has_video"] is True
    assert "engine enumerated 1 point" in snap["corrected_reason"]


def test_finished_count_never_exceeds_the_observed_total(tmp_state):
    set_chapter_point_snapshot(KEY, CID, video_total=3, video_finished=3, has_video=True)

    video_total_from_observation(KEY, CID, observed=1, reason="probe")

    assert load_chapter_points(KEY)[CID]["video_finished"] == 1


def test_correction_is_a_no_op_when_the_snapshot_already_agrees(tmp_state):
    set_chapter_point_snapshot(KEY, CID, video_total=1, video_finished=0, has_video=True)

    changed = video_total_from_observation(KEY, CID, observed=1, reason="probe")

    assert changed is False
    assert "corrected_reason" not in load_chapter_points(KEY)[CID]


# ── 4. 用户可见保证：纠正后不再 mint 幻影点 ─────────────────────────
def test_discovery_stops_minting_the_phantom_after_correction(tmp_state):
    chapters = [{"chapter_id": CID, "title": "分类的IP地址", "status": "completed",
                 "job_remaining": 0, "text": "", "chapter_index": 3, "cell_index": 0}]

    set_chapter_point_snapshot(KEY, CID, video_total=2, video_finished=1, has_video=True)
    before = build_tasks_from_discovery(
        chapters, video_counts=video_counts_from_points(load_chapter_points(KEY)))
    assert f"{CID}:video2" in [t.task_id for t in before]   # 病灶可复现

    video_total_from_observation(KEY, CID, observed=1, reason="engine says 1")
    after = build_tasks_from_discovery(
        chapters, video_counts=video_counts_from_points(load_chapter_points(KEY)))

    assert [t.task_id for t in after] == [CID]
