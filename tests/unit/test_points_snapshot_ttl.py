# -*- coding: utf-8 -*-
"""点级快照是缓存，陈旧就不该再参与 `:videoN` 的拆分（#23）。

不对称的根形状：`chapter_points.json` 被 gitignore，云端每次干净检出**没有**快照，
本地却留着 2026-09-20 那一批 —— 于是同一段代码在两端看到的是完全不同的真源。
本地那批陈旧条目实测已在说谎：`1217304730` 写 2（今天三路实测都 1）、
`1217304738` 写 3（真站 run 与只读探测都是 2）。拿它们当拆分数，就会把已经收掉的
幻影 `:videoN` 重新 mint 回账本。

修法不是逐条手改缓存，而是给它加时效：**没有新鲜的 live 证据，就不宣布某章有多个点**
—— 与 `phantom_correction_policy` 拒绝采信 observed=0 同一个原则。
"""
from datetime import datetime, timedelta, timezone

from app.registry.task_registry import video_counts_from_points

KEY = "265997861_151695658"
NOW = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)


def _snap(total, minutes_ago):
    return {"video_total": total, "video_finished": 1, "has_video": True,
            "updated_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_fresh_snapshot_still_supplies_the_video_count():
    pts = {"1217304738": _snap(2, minutes_ago=5)}
    assert video_counts_from_points(pts, now=NOW) == {"1217304738": 2}


def test_stale_snapshot_is_dropped_and_chapter_falls_back_to_default_one():
    """9/20 那批就是这种：陈旧到不能代表站点今天的形状。"""
    pts = {"1217304730": _snap(2, minutes_ago=2 * 24 * 60)}
    assert video_counts_from_points(pts, now=NOW) == {}


def test_default_ttl_is_one_day():
    pts = {"a": _snap(3, minutes_ago=60 * 23),      # 23h 前：仍新鲜
           "b": _snap(3, minutes_ago=60 * 25)}      # 25h 前：过期
    counts = video_counts_from_points(pts, now=NOW)
    assert counts == {"a": 3}


def test_snapshot_without_updated_at_is_treated_as_stale():
    """读不到时间戳 = 无法证明它新鲜 → 不采信。"""
    pts = {"x": {"video_total": 2, "video_finished": 1, "has_video": True}}
    assert video_counts_from_points(pts, now=NOW) == {}


def test_malformed_updated_at_is_treated_as_stale():
    pts = {"x": dict(_snap(2, 0), updated_at="not-a-timestamp")}
    assert video_counts_from_points(pts, now=NOW) == {}


def test_ttl_can_be_widened_by_caller():
    pts = {"1217304730": _snap(2, minutes_ago=2 * 24 * 60)}
    counts = video_counts_from_points(pts, now=NOW, ttl_s=30 * 24 * 3600)
    assert counts == {"1217304730": 2}
