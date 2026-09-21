# P1 残留修复：绑定模式不得因 target_frame_not_found 触发 reload。
#
# 取证（2026-09-21，_probe_persist 只读观测）：
#  - 未完成点的绑定帧在 headed 会话里 50s 持续存在（4738，25/25 采样）
#  - 708 复验里"绑定帧 1s 出现、11s 消失"发生在**点已被服务端判完成**之后
#    —— 完成的点不再保留播放器，reload 救不回来，反而把页面打回点 1，
#    干扰页面自身向目标点的推进（run abort 日志实证）。
# 因此 reload 只保留旧的三种"整章没视频帧"触发；target_frame_not_found
# 交给 Step F 的 90s 预算，到点诚实 FAIL。

import pytest

from app.e2_headed_gha import video_reload_warranted


@pytest.mark.parametrize("reason", [
    "no_video_in_cards", "no_cards_doc", "no_cards_frame",
])
def test_legacy_no_video_reasons_still_reload(reason):
    assert video_reload_warranted(reason) is True


def test_missing_bound_frame_does_not_reload():
    # 绑定帧不在：要么点已完成（reload 无意义），要么页面还没推进到它
    # （reload 把页面打回点 1，反而阻碍推进）
    assert video_reload_warranted("target_frame_not_found") is False


def test_other_absences_do_not_reload():
    assert video_reload_warranted("no_frames") is False
    assert video_reload_warranted("") is False
    assert video_reload_warranted(None) is False
