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

from app.e2_headed_gha import should_navigate_to_target, video_reload_warranted


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


# ── 章内导航：绑定模式不得干等页面自己走到目标点 ─────────────────────
#
# 真站实证（2026-09-21，4738:video2 e2e run，用户目视）：页面停在**已完成的
# 第 1 点**并播放它；目标点的播放器帧存在但 rs=0/dur=None —— 不在视口的
# player 永不激活。绑定只解决"看哪一帧"，不解决"页面停在哪儿"。
# 章内导航（把目标附件滚进视口中心，触发页面自己的激活逻辑）是用户级
# 页面操作：不碰上报链路、不跳过播放，且 dispatch 目标的前序点都已
# 服务端完成（承载规则），不存在"跳着学"。

def _nav(**kw):
    base = dict(target_objectid="e79a9a86" + "0" * 24, bound_dur=None,
                stalled_for_s=20.0, nav_attempts=0, last_nav_at=None, now=100.0,
                target_vi=2)
    base.update(kw)
    return should_navigate_to_target(**base)


def test_first_video_point_never_navigates():
    # `<cid>`（N=1）路径是被验证过的稳定链路：第 1 点就是页面"当前"播放器，
    # Step F 正常等得到 metadata —— 不往这条链路塞新交互（用户 2026-09-21 定）
    assert _nav(target_vi=1) is False
    assert _nav(target_vi=0) is False


def test_natural_mode_never_navigates():
    assert _nav(target_objectid=None) is False
    assert _nav(target_objectid="") is False


def test_alive_bound_player_needs_no_navigation():
    assert _nav(bound_dur=655.0) is False


def test_short_stall_does_not_trigger_navigation_yet():
    assert _nav(stalled_for_s=5.0) is False


def test_sustained_stall_triggers_navigation():
    assert _nav(stalled_for_s=20.0) is True


def test_navigation_respects_cooldown():
    assert _nav(last_nav_at=90.0, now=100.0) is False   # 10s < 15s 冷却
    assert _nav(last_nav_at=80.0, now=100.0) is True    # 20s ≥ 冷却


def test_navigation_attempts_are_bounded():
    assert _nav(nav_attempts=3) is False


# 升级策略实证（4738 第二次 run）：3 次 scrollIntoView 全 ok=True，绑定帧仍
# dur=None —— 滚动进视口**不足以**激活播放器。第 1 次仍只滚动（最保守），
# 之后加"可信点击目标帧 <video>"（等同用户按播放键；前序点已服务端完成，
# 不构成跳播）。


def test_first_attempt_scrolls_then_later_attempts_add_click():
    from app.e2_headed_gha import nav_action_for_attempt
    assert nav_action_for_attempt(0) == "scroll"
    assert nav_action_for_attempt(1) == "scroll_click"
    assert nav_action_for_attempt(2) == "scroll_click"


# 起播入口的选择（C 探测，2026-09-21）：cards 无"定位任务点"入口；页面是
# video.js，目标点播放器里有**可见且 hit-test 可命中**的 .vjs-big-play-button
# —— 点击它=用户按播放。上一版 click 落空的原因：点了 <video>，被 poster/
# 大按钮层挡住，actionability 不通过。

def test_activation_target_is_big_play_button_with_video_fallback():
    from app.e2_headed_gha import player_activation_selectors
    sels = player_activation_selectors()
    assert sels[0] == ".vjs-big-play-button"      # 大播放按钮优先
    assert "video" in sels                        # 找不到按钮时退回点视频本体
