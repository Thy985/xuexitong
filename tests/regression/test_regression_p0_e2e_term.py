"""P0-02 / P0-04：e2 主循环 termination + isPassed 响应回调判定。

── P0-02 ─────────────────────────────────────────────────────────
Failure mode: e2 主循环 wait_playback 空转，仅靠 1500s cap 兜底；曾 33s 冒充完成。
Expected invariant（termination）:
    连到「any」输入也必须可收敛——
      * ended_seen=True → 永远返回 exit_complete（真·完成，must exit）
      * ended_seen=False → **永不**返回 exit_complete（绝不冒充完成）
   这里是纯函数 next_unit_decision 的穷举模型检查（现有 param 测试只采样，
   这里对完整输入域做一致性/终止性断言，把「死循环兜底」固化为不变量）。

回归 P0-04 ───────────────────────────────────────────────────────
Failure: isPassed 判定被二次 fetch / 字段漂移导致漏判或误判完成。
Expected {body} → has_is_passed_marker(body)：
    body 含 `"isPassed":true` → True；缺失/NULL/漂移为其它 key → False。
依据：REGRESSION_MATRIX P0-02 / P0-04；HISTORICAL_BUG_CASES C3 / C8 / §6.5。
"""

import pytest

from e2.e2_headed_gha import next_unit_decision, has_is_passed_marker


class TestP02MainLoopTerminationModel:
    """穷举 next_unit_decision 的终止不变量（死循环/假完成为不可变式）。

    关键不变量：
      1. endned_seen=True → 必须 exit_completed（可终止）；
      2. ended_seen=False → 永不 exit_completed（不可冒充完成 → 由外层 watchDog 兜底）。
    """

    def test_ended_always_completes(self):
        # 覆盖全部 bool 子状态 + 若干 max_ct/initial_duration
        for nxt in (False, True):
            for passed in (False, True):
                for mt in (0.0, 100.0, 751.0, 905.0):
                    for ini in (0.0, 751.7):
                        assert next_unit_decision(nxt, passed, mt,
                                                  ended_seen=True,
                                                  initial_duration=ini) == "exit_complete"

    def test_no_faked_complete_without_ended(self):
        # ended_seen=False 时，任何 nextunit/has_passed/current_time 组合都不得判完成
        for nxt in (False, True):
            for passed in (False, True):
                for mt in (0.0, 1.0, 720.0, 905.0):
                    for ini in (0.0, 750.0):
                        d = next_unit_decision(nxt, passed, mt,
                                               ended_seen=False,
                                               initial_duration=ini)
                        assert d != "exit_complete"
                        assert d in ("none", "wait_playback", "exit_switch")


class TestP04IsPassedMarker:
    """isPassed 判定的唯一真源（本 helper），保护字段漂移/返回体格式变化。"""

    def test_true_marker_detected(self):
        assert has_is_passed_marker('{"isPassed":true}') is True
        assert has_is_passed_marker('{"result":{"isPassed":true},"code":200}') is True

    def test_false_or_none_not_detected(self):
        assert has_is_passed_marker('{"isPassed":false}') is False
        assert has_is_passed_marker('{"isPassed":0}') is False
        assert has_is_passed_marker(None) is False
        assert has_is_passed_marker("") is False
        # 字段名漂移 / 别名字段（超星若改名）→ 不得被误判为已完成
        assert has_is_passed_marker('{"passed":true}') is False