"""R-04 自动续播：视频停在 paused 时要自己恢复，而不是干等到心跳超时。

事故（2026-09-19 本地稳定性验证）：章 1217304751 视频 595s，起播后 ct 卡在 8s 不动，
`ml` 只到 2；章 1217304753 同样卡在 ct=9/1045。引擎在 `get_video_state` 里**读了**
`video.paused`（e2_headed_gha.py:208）却从不处理它 —— 观察到了却什么都不做。

合规边界（README 红线）：只允许调用页面自己的 `video.play()` 让它继续自然播放；
不构造/伪造/重放 multimedia/log，不改 playingTime/_t/enc。
"""

from app.e2_headed_gha import (
    MAX_RESUME_ATTEMPTS,
    RESUME_COOLDOWN_S,
    should_auto_resume,
)


def _st(**over):
    base = {"found": True, "paused": True, "ended": False, "currentTime": 8.0}
    base.update(over)
    return base


class TestResumesWhenActuallyPaused:
    def test_paused_video_before_end_is_resumed(self):
        assert should_auto_resume(_st(), now=1000.0, last_resume_at=None,
                                  resume_count=0, ended_seen=False) is True

    def test_playing_video_is_left_alone(self):
        assert should_auto_resume(_st(paused=False), now=1000.0,
                                  last_resume_at=None, resume_count=0,
                                  ended_seen=False) is False

    def test_no_video_frame_is_noop(self):
        assert should_auto_resume(_st(found=False), now=1000.0,
                                  last_resume_at=None, resume_count=0,
                                  ended_seen=False) is False

    def test_missing_state_is_noop(self):
        assert should_auto_resume(None, now=1000.0, last_resume_at=None,
                                  resume_count=0, ended_seen=False) is False


class TestEndOfVideoIsNotDisturbed:
    def test_ended_seen_blocks_resume(self):
        """播完后的 ended 是真信号，不能被 play() 打断（否则回退进度/误判下一章）。"""
        assert should_auto_resume(_st(), now=1000.0, last_resume_at=None,
                                  resume_count=0, ended_seen=True) is False

    def test_ended_flag_blocks_resume(self):
        assert should_auto_resume(_st(ended=True), now=1000.0,
                                  last_resume_at=None, resume_count=0,
                                  ended_seen=False) is False


class TestBoundedSoItCannotRunAway:
    def test_cooldown_between_attempts(self):
        assert should_auto_resume(_st(), now=1000.0, last_resume_at=995.0,
                                  resume_count=1, ended_seen=False) is False
        assert should_auto_resume(_st(), now=995.0 + RESUME_COOLDOWN_S,
                                  last_resume_at=995.0, resume_count=1,
                                  ended_seen=False) is True

    def test_hard_attempt_limit(self):
        assert should_auto_resume(_st(), now=1000.0, last_resume_at=None,
                                  resume_count=MAX_RESUME_ATTEMPTS,
                                  ended_seen=False) is False

    def test_one_below_limit_still_allowed(self):
        assert should_auto_resume(_st(), now=1000.0, last_resume_at=None,
                                  resume_count=MAX_RESUME_ATTEMPTS - 1,
                                  ended_seen=False) is True
