"""ci_local_run 的"算不算通过"口径 —— 不得把探针失败的 NOOP 洗成绿。

事故（2026-09-19 本地 L2）：TDVP 目录探查两次拿不到 #coursetree，scheduler 返回
decision=NOOP / verdict="No pending task / probe empty (no guess)" / **passed=False**，
但汇总打印 "1 次完成: 全部通过"。原因：判定只看 decision+failure_stage，
从未读 scheduler 已经诚实交回的 passed 字段。
ACCEPTANCE.md 的原则正是"凡看起来能跑都不算通过"。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ci_local_run  # noqa: E402


def _s(**over):
    base = {"decision": "NOOP", "verdict": None, "passed": None,
            "failure_stage": None}
    base.update(over)
    return base


class TestRealPassStillPasses:
    def test_pass_verdict_is_acceptable(self):
        assert ci_local_run.counts_as_pass(
            _s(decision="RUN", verdict="PASS", passed=True)) is True


class TestGenuineIdleIsAcceptable:
    def test_clean_noop_without_complaint_is_acceptable(self):
        """课程确实无事可做：NOOP、无 verdict 抱怨、无 failure_stage、未报 passed=False。"""
        assert ci_local_run.counts_as_pass(_s()) is True


class TestDiscoveryFailureIsNotGreen:
    def test_noop_with_passed_false_is_a_failure(self):
        """探针空导致没选到章：scheduler 已说 passed=False，不得判通过。"""
        assert ci_local_run.counts_as_pass(_s(
            verdict="No pending task / probe empty (no guess)", passed=False)) is False

    def test_noop_with_failure_stage_is_a_failure(self):
        assert ci_local_run.counts_as_pass(
            _s(failure_stage="BROWSER_MISSING")) is False

    def test_fail_verdict_is_a_failure(self):
        assert ci_local_run.counts_as_pass(
            _s(decision="RUN", verdict="FAIL", passed=False,
               failure_stage="ISPASSED_FALSE")) is False
