"""看门狗预算降级必须显式可归因。

事故（2026-09-19 本地稳定性验证）：章 1217304752 视频 846s，自适应看门狗本该给
`846*1.5+400=1669s` 预算，但 `_probe_video_duration_s()` 内部 `except Exception:
return None` 静默失败（它另起一个 headed 浏览器、还带 Linux 专用的 --display=:99），
于是回落到静态 900s，播放到 34% 被 killpg 砍成 TIMEOUT。日志里没有任何一行说明
"自适应没生效、为什么"。

"探测失败不得成为新故障点"这个设计意图是对的 —— 错在降级完全不可见。
"""

from scheduler.scheduler import video_watch_budget


class TestAdaptivePathIsUsedWhenDurationKnown:
    def test_long_video_gets_expanded_budget(self):
        budget, reason = video_watch_budget(900, 846)
        assert budget == int(846 * 1.5 + 400)
        assert reason.startswith("ok")
        assert "846" in reason

    def test_agrees_with_adaptive_helper(self):
        from scheduler.scheduler import _adaptive_video_watch_s as W
        for dur in (300, 846, 1045, 20000):
            assert video_watch_budget(900, dur)[0] == W(900, dur)


class TestDegradationIsExplicit:
    def test_none_duration_reports_fallback_with_cause(self):
        budget, reason = video_watch_budget(900, None, probe_error="TimeoutError: probe")
        assert budget == 900
        assert reason.startswith("fallback")
        assert "TimeoutError" in reason, "降级必须带上探测失败的原因"

    def test_fallback_names_the_risk(self):
        """读日志的人要能立刻明白后果，而不是只看到一句"回退 base"。"""
        _, reason = video_watch_budget(900, None, probe_error="browser launch failed")
        assert "误杀" in reason or "TIMEOUT" in reason

    def test_zero_duration_also_counts_as_degradation(self):
        budget, reason = video_watch_budget(900, 0)
        assert budget == 900
        assert reason.startswith("fallback")

    def test_missing_probe_error_still_says_fallback(self):
        _, reason = video_watch_budget(900, None)
        assert reason.startswith("fallback")
