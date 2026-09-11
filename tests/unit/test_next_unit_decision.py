# 完成语义状态机 next_unit_decision 的单元测试。
# 完成凭据（都要**本轮运行真实观测**）：
#   1) ended_seen=True（本轮视频真播到末尾）—— 基线 run 34293378209；
#   2) 方案A（用户确认修复）：nextUnit 已切（chapterId 变到另一章）+ 本轮真实
#      isPassed(has_passed) 且有实际进度 → 服务端已 PASS 该章(P0-04 服务端为真源)，
#      判 exit_complete；不再死等旧 video 的 ended（run 34573528666 曾因旧逻辑
#      死等 900s 看门狗 → TIMEOUT）。
# 防假结护栏（锤磊自旧事故 run 34332366744 的 33s 假完成）：若 nextUnit 已切 +
# passed 但**零进度**(max_ct=0 且已知时长=0) → 绝不判完成（回落到 exit_switch）。
import pytest

from app.e2_headed_gha import next_unit_decision


@pytest.mark.parametrize(
    "nextunit_seen,has_passed,max_ct,ended_seen,initial_duration,expected",
    [
        # ── 真实推进基线：视频真播到终点(ended_seen) → 完成 ──
        (False, True, 751.0, True, 751.7, "exit_complete"),
        (True, True, 751.0, True, 751.7, "exit_complete"),
        (True, False, 751.0, True, 751.7, "exit_complete"),
        (True, False, 0.0, True, 751.0, "exit_complete"),

        # ── 方案A：nextUnit 已切 + has_passed + 有进度 → 服务端已 PASS → 完成 ──
        (True, True, 905.0, False, 751.7, "exit_complete"),
        (True, True, 750.0, False, 751.7, "exit_complete"),
        # 恢复进度污染：max_ct≈duration 但已有本轮 passed + 已切 → 方案A完成
        (True, True, 720.0, False, 750.7, "exit_complete"),

        # ── 零进度：max_ct=0 且时长=0，即便 passed 也已切 → 不判完成 ──
        (True, True, 0.0, False, 0.0, "exit_switch"),
        # nextUnit 已切，但无本轮 isPassed（非完成）→ 有效切换退出
        (True, False, 5.0, False, 0.0, "exit_switch"),

        # ── nextUnit 未变 → 继续播（none），绝不误判完成 ──
        (False, True, 100.0, False, 751.0, "none"),
        (False, False, 0.0, False, 0.0, "none"),
        (False, False, 99.0, False, 0.0, "none"),
    ],
)
def test_next_unit_decision(nextunit_seen, has_passed, max_ct,
                            ended_seen, initial_duration, expected):
    assert next_unit_decision(nextunit_seen, has_passed, max_ct,
                              ended_seen=ended_seen,
                              initial_duration=initial_duration) == expected