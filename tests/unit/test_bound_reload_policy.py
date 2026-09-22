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
# 激活通道的对照实证（2026-09-22，evidence/target_activation_{with_v3,no_v3}.log，
# 同章 4738、同样只点一次目标卡的 .vjs-big-play-button）：
#   注入 v3   → 目标点只拿到 metadata（rs=4/dur=1130）却被永久钉住：ct 恒 19.8、
#               paused=True、180s 内 0 次翻转；同时点 1 被 v3 从 0 拽着连播到 262。
#   不注入 v3 → 目标点 ct 20.4→194.1、89/90 采样在前进、0 次暂停，点 1 安静停在
#               存储位 227，且站点自己上报 playingTime=198 objectId=<目标点 oid>。
# ⇒ 抢当前位的一直是我们自己的 v3（它 resume 每个模块帧里的**第一个** video = 点 1）；
#   点 1 在播时站点绝不让点 2 播。而"播完前一个点让页面自推进"也是死路：真播到 ended
#   之后整节跳走（chapterId 1217304738 → 1217304740），目标点从未激活。
# ⇒ 章内 `:videoN` 的形态 = 不注入 v3 + 点目标卡自己的播放键 + 交给已绑定目标帧的
#   R-04 续播。单视频章与点 1 一律不动 —— 那条路上 v3 仍是必需的播放驱动器。


def test_only_the_frame_bound_to_the_target_oid_may_be_clicked():
    """点击激活仍必须认帧身份 —— 支点 1 的播放键冒认目标点是 P1 的旧病灶。"""
    from app.e2_headed_gha import frame_is_bound_to
    tgt = "e79a9a86" + "0" * 24
    cur = "94382be4" + "0" * 24
    assert frame_is_bound_to(f"https://s2.cldisk.com/sv-w9/video/{tgt}/sd.mp4?ak_=x", tgt) is True
    assert frame_is_bound_to(f"https://s2.cldisk.com/sv-w9/video/{cur}/sd.mp4", tgt) is False
    assert frame_is_bound_to("", tgt) is False
    assert frame_is_bound_to("https://x/sd.mp4", "") is False


def test_v3_is_not_injected_when_dispatch_targets_a_later_video_point():
    """v3 会去 resume 帧里第一个 video；投 :videoN 时它替目标点抢走了当前位。"""
    from app.e2_headed_gha import should_inject_v3
    assert should_inject_v3(0) is True      # 未指定段 = 点 1 的稳定链路
    assert should_inject_v3(1) is True
    assert should_inject_v3(2) is False
    assert should_inject_v3(3) is False
    assert should_inject_v3(None) is True
