"""P0-08 / P0-09 / P0-11：reconcile 不应误降 / 不假完成 / 迁移单键。

── P0-08 ─────────────────────────────────────────────────────────
Failure mode: 服务端已完成章可能被 blanket 降级成 UNKNOWN（DOM 抖动/一次性 pending）。
Expected invariant: 有强证据（SERVER_VERIFIED/RECHECK）的 COMPLETED 章，
    在无 live_pending 推翻、且服务器 DOM 显示完成时，校准后仍保持 COMPLETED（不 UNKNOWN）。

── P0-09 ─────────────────────────────────────────────────────────
Failure: 服务器 DOM 标「该章已完成」，但 live verification 确认该 task 仍有未完成的
    真实 video 点 → 静默 COMPLETED → 漏课。
Expected: DOM completed + task 在 live_pending → 不得 COMPLETED，应保持/降为 PENDING。
（此前 reconcile_registry 的 dom_done 分支未查 live_pending → 真实缺口，已修。）

── P0-11 ─────────────────────────────────────────────────────────
Failure: task_id 格式迁移（title 匹配迁移到新 task_id）后，旧 task_id 残留仓库 →
同一任务双记录（canonical 不唯一）。
Expected: 迁移后 registry 中**只有一个** task_id（新 key），旧 key 被淘汰。
（此前 result=dict(existing)∪repaired 会同时保留 old+new，已修。）

依据：REGRESSION_MATRIX P0-08/09/11；HISTORICAL_BUG_CASES §3.3 / §6.2（task_id 迁移）。
"""

import pytest

from e6.reconcile import reconcile_registry
from e6.task_registry import (
    TaskRecord, CompletionEvidence, Verification,
)
from tvdp.tdvp import TaskInfo, TaskEvidence


def _completed_strong(tid, cid, title="章", source="isPassed"):
    t = TaskRecord(tid, cid, title)
    t.status = "COMPLETED"
    t.completion_evidence = CompletionEvidence(
        type="SERVER_VERIFIED", source=source, run_id="run-x", detail="d")
    t.verification = Verification(
        level="SERVER_VERIFIED", verified_at_utc="2026-09-01T00:00:00Z",
        run_id="run-x", source_detail=source)
    return t


def _info(tid, cid, title):
    return TaskInfo(tid, cid, title, "video", "PENDING", "UI", "x",
                    TaskEvidence("PENDING", "UI", ""))


class TestP08StrongEvidenceNotDowngraded:
    """强证据 COMPLETED 校准后必须保持 COMPLETED（服务器 DOM 已完成 / 未完成都不失守）。"""

    def test_kept_when_dom_completed(self):
        old = _completed_strong("1217304700", "1217304700", "互联网概述")
        fixed, rep = reconcile_registry(
            "k", {"1217304700": old}, [_info("1217304700", "1217304700", "互联网概述")],
            dom_status={"1217304700": "completed"})
        assert fixed["1217304700"].status == "COMPLETED"
        assert rep.kept_completed >= 1

    def test_kept_when_dom_pending_and_no_live_conflict(self):
        # 服务器 DOM 暂时 pending、无 live 推翻 → 强证据仍保留（防抖，不误降 UNKNOWN）
        old = _completed_strong("1217304702", "1217304702")
        fixed, rep = reconcile_registry(
            "k", {"1217304702": old}, [_info("1217304702", "1217304702", "章")],
            dom_status={"1217304702": "pending"}, live_pending=set())
        assert fixed["1217304702"].status == "COMPLETED"
        assert "1217304702" not in rep.repair_map  # 未触发降级


class TestP09DomCompletedMustRespectLivePending:
    """DOM completed + real video still pending → 不得 COMPLETED，应 PENDING（P09 缺口）。"""

    def test_dom_completed_but_live_pending(self):
        old = TaskRecord("1217304707", "1217304707", "章")
        old.status = "DISCOVERED"
        fixed, _ = reconcile_registry(
            "k", {"1217304707": old}, [_info("1217304707", "1217304707", "章")],
            dom_status={"1217304707": "completed"},
            live_pending={"1217304707"})
        # live 证明仍未完成 → 不得被 DOM 掩盖为 COMPLETED
        assert fixed["1217304707"].status != "COMPLETED"
        assert fixed["1217304707"].status in ("PENDING", "STALE")


class TestP0TaskIdMigrationSingleKey:
    """P11：task_id 迁移后 registry 只保留**新** task_id（旧键不双留）。"""

    def test_migration_leaves_only_new_key(self):
        old = _completed_strong("taskid_legacy_v1", "1217304706", "物理层的主要任务",
                                source=str("legacy"))
        new_info = _info("1217304706", "1217304706", "物理层的主要任务")
        fixed, rep = reconcile_registry("k", {"taskid_legacy_v1": old}, [new_info], {})
        assert "1217304706" in fixed            # 新 key 存在
        assert "taskid_legacy_v1" not in fixed  # 旧 key 被淘汰（唯一）
        assert fixed["1217304706"].status == "COMPLETED"  # 证据保留