# 完成语义状态机 next_unit_decision 的单元测试。
# 目的：把 run 34311891898/34311998897 的死循环场景固化为回归用例——
# nextUnit 已切 + 已记录 passed_object_id + 真起播过 → 应判 exit_complete（退出），
# 而不是旧代码的无限等待。同时守护「刚起播就跳」的防早切语义。
import pytest

from e2.e2_headed_gha import next_unit_decision


@pytest.mark.parametrize(
    "nextunit_seen,has_passed,max_ct,expected",
    [
        # 死循环场景: nextUnit 变 + passed + 真播放过(>0) → 完成退出
        (True, True, 905.0, "exit_complete"),
        # nextUnit 变 + passed + 正常进度(100%) → 完成退出
        (True, True, 200.0, "exit_complete"),
        # 防早切: nextUnit 变 + passed 但从未起播(max_ct=0) → 等起播，不退出
        (True, True, 0.0, "wait_playback"),
        # 有效切换: nextUnit 变 + 无 passed → 直接退出
        (True, False, 5.0, "exit_switch"),
        # nextUnit 未变 → 无动作(继续循环)
        (False, True, 905.0, "none"),
        (False, False, 0.0, "none"),
    ],
)
def test_next_unit_decision(nextunit_seen, has_passed, max_ct, expected):
    assert next_unit_decision(nextunit_seen, has_passed, max_ct) == expected