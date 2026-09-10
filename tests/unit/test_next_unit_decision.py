# 完成语义状态机 next_unit_decision 的单元测试。
# 核心：真实推进的唯一完成凭据 = ended_seen（本轮视频真播到末尾）。
# 回归依据：run 34293378209（真实推进，ended_seen=True, 视频 0→751 真播完）
# vs run 34332366744（方案2 bug，nextUnit+passed+max_ct 却被历史进度污染，
# 33s 就冒充完成、ended_seen=False，服务端任务点永不推进）。绝不能把
# 「max_ct>=95% 已知时长」当完成凭据（会被恢复的历史进度污染）。
import pytest

from app.e2_headed_gha import next_unit_decision


@pytest.mark.parametrize(
    "nextunit_seen,has_passed,max_ct,ended_seen,initial_duration,expected",
    [
        # ── 真实推进基线：视频真播到终点(ended_seen) → 完成 ──
        (False, True, 751.0, True, 751.7, "exit_complete"),
        (True, True, 751.0, True, 751.7, "exit_complete"),
        (True, False, 751.0, True, 751.7, "exit_complete"),
        # 即便没有本轮 passed、max_ct 为 0，只要 see ended → 完成（进度为准）
        (True, False, 0.0, True, 751.0, "exit_complete"),

        # ── 死循环/假完成修复：nextUnit 已切 + passed + max_ct 很大，但
        # ── ended_seen=False → 不能判完成（曾 33s 冒充完成），继续等 ended ──
        (True, True, 905.0, False, 751.7, "wait_playback"),
        (True, True, 750.0, False, 751.7, "wait_playback"),
        # 恢复进度污染：max_ct≈duration 但未 ended → 仍不可判完成
        (True, True, 720.0, False, 750.7, "wait_playback"),

        # ── 防早切：从未起播，未 ended → 等起播/等 ended ──
        (True, True, 0.0, False, 0.0, "wait_playback"),
        # nextUnit 已切，且无 passed（非完成）→ 视为有效切换退出
        (True, False, 5.0, False, 0.0, "exit_switch"),

        # ── nextUnit 未变、未到终点 → 继续播 ──
        (False, True, 100.0, False, 751.0, "none"),
        (False, False, 0.0, False, 0.0, "none"),
        # 纯文档/未起播：未 ended 且进度低 → none（不误退为完成）
        (False, False, 99.0, False, 0.0, "none"),
    ],
)
def test_next_unit_decision(nextunit_seen, has_passed, max_ct,
                            ended_seen, initial_duration, expected):
    assert next_unit_decision(nextunit_seen, has_passed, max_ct,
                              ended_seen=ended_seen,
                              initial_duration=initial_duration) == expected