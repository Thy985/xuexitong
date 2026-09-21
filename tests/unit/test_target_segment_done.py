"""P1：Options B 的"目标段完成"判定必须看**播完**，而不是看**到达**。

`--video-index N`（task_id `<cid>:videoN`）的语义是"本次只把章内第 N 段判完成"。
但 `run_test` 里的 `video_count` 是在 **video.src 切换（即到达下一段）** 时自增的，
与"该段播完了"无关；切换分支还会把 `ended_seen` 归零。原判定

    if target_vi and video_count >= target_vi:   # 注释写着"并结束"，条件里却没有

于是 `N>=2` 时刚跳到目标段就 `break`。真站日志（`evidence/
chapter_1217304708_video2.scheduler.stdout.log`，共 4 秒）：

    10:03:57  ★ Chapter video switch -> #3 src=…69a6c4c5…   ← 确实换到了点 2
    10:03:57  Playback loop ended: 4s max_ct=0s videos=2     ← 同一秒退出，一秒都没播

结果 10 项检查里唯一失败的就是 `7_currentTime_growing` → 整章 DEGRADED。
"""
import pytest

from app.e2_headed_gha import target_segment_done


@pytest.mark.parametrize("target_vi,video_count,ended,expected", [
    # P1 本体：刚到第 2 段（src 已切、还没播）—— 绝不能判完成
    (2, 2, False, False),
    # 目标段真的播完了
    (2, 2, True, True),
    # 第 1 段 ended、目标是第 2 段：继续等切换
    (2, 1, True, False),
    (2, 1, False, False),
    # `<cid>` 走 target_vi=1：第 1 段 ended 即完成（原有行为，不得退化）
    (1, 1, True, True),
    (1, 1, False, False),
    # 越过后才判到（页面连切两段）：目标段已 ended 才算
    (2, 3, True, True),
])
def test_target_segment_done(target_vi, video_count, ended, expected):
    assert target_segment_done(target_vi, video_count, ended) is expected


def test_natural_full_chapter_mode_never_exits_on_target():
    """`target_vi=0` = 自然连播整章，不参与"目标段"判定。"""
    assert target_segment_done(0, 8, True) is False
