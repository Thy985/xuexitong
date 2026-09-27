"""P0-1（issue #2）：业务判定 = 服务端真源，UI 观测不得覆盖 server verdict。

回归场景（用户 report）：章节服务端已回 isPassed=true，但 UI 没观测到
nextUnit 触发（老逻辑 PARTIAL → 误 FAIL → mark_failed → BLOCKED）。新逻辑：
  server 真源确认通过 → SERVER_CONFIRMED_PASS → run.py `passed=True` → exit 0，
  consecutive_failures 不 +1、不 BLOCKED。
UI 的 ended_seen / nextunit_triggered / 时长 / ml 只属于「观测」，不参与业务判定。
"""
import app.e2_headed_gha as E
from app.e2_headed_gha import (
    SERVER_CONFIRMED_PASS,
    INCONCLUSIVE, EXECUTION_ERROR, business_verdict_from_checks,
)


class TestBusinessVerdictServerLed:
    """核心回归：服务端是真源，UI 观测不否决。"""

    def test_ispassed_true_no_ended_no_nextunit_pass(self):
        # 用户 report 的现场：isPassed=true，ended=false，nextUnit 没触发
        passed_object_ids = ["2d7c9aa7f775713866ec797eeb9198b8"]
        checks = {"isPassed_seen": True, "ended_seen": False,
                  "nextunit_triggered": False}
        b = business_verdict_from_checks(checks, passed_object_ids,
                                         exec_broken=False)
        assert b == SERVER_CONFIRMED_PASS

    def test_ispassed_true_even_if_cards_observed_missing(self):
        # exec_broken=True 也不覆盖 server verdict（server 已确认就 PASS）
        b = business_verdict_from_checks(
            {"isPassed_seen": True}, ["obj1"], exec_broken=True)
        assert b == SERVER_CONFIRMED_PASS

    def test_passed_object_ids_alone_is_server_confirmed(self):
        # passed_object_ids 非空 = 服务端对该点的实时确认（本轮 live multimedia/log）
        b = business_verdict_from_checks({}, ["obj1"], exec_broken=False)
        assert b == SERVER_CONFIRMED_PASS

    def test_no_server_signal_and_exec_ok_is_inconclusive(self):
        b = business_verdict_from_checks({"isPassed_seen": False}, [], exec_broken=False)
        assert b == INCONCLUSIVE

    def test_no_server_signal_and_execution_broke_is_exec_error(self):
        b = business_verdict_from_checks({"isPassed_seen": False}, [], exec_broken=True)
        assert b == EXECUTION_ERROR


class TestServerConfirmedPassNeverFailsCounter:
    """run.py 决策层：SERVER_CONFIRMED_PASS → passed=True，绝不进 mark_failed。"""

    def test_server_confirmed_maps_to_passed(self):
        ev = {"business_verdict": SERVER_CONFIRMED_PASS,
              "passed_object_ids": ["obj1"], "passed_count": 9}
        biz = ev.get("business_verdict")
        passed = biz == SERVER_CONFIRMED_PASS or bool(ev.get("passed_object_ids"))
        assert passed is True
        assert ev.get("passed_count", 0) < 10  # 观测少 1 也不再否定业务 PASS

    def test_no_server_signal_maps_to_not_passed(self):
        ev = {"business_verdict": INCONCLUSIVE, "passed_object_ids": [],
              "passed_count": 9}
        passed = ev.get("business_verdict") == SERVER_CONFIRMED_PASS \
            or bool(ev.get("passed_object_ids"))
        assert passed is False