"""E6.1 — Task State Reconciliation & Evidence-backed Canonical State.

把「Discovery → Registry 校准」从 scheduler 内联逻辑抽出为独立、可测试的模块。

原则（E6.1）：
  - Task Registry 是 canonical state（记录「当前系统认为状态如何、为什么」）。
  - Execution History 是过去执行过什么（本阶段不复核重跑）。
  - Execution Queue 是派生物（每次 reconcile 后重算）。
  - done_ids 只是 derived cache（COMPLETED+证据的章节集合），不是权威状态。

完成语义：
  - 只有具备有效 completion evidence 的任务才保留 COMPLETED。
  - SERVER_VERIFIED / RECHECK → 强证据，保留 COMPLETED。
  - UI(服务器 DOM `completed` 标记) → 补偿性证据，保留 COMPLETED(UI)。
  - 无证据 / 仅 nextUnit / URL chapterId 变化 / 页面导航推断 → 降级 UNKNOWN（需重新验证）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from e6.task_registry import (
    CompletionEvidence,
    TaskRecord,
    Verification,
)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconcileReport:
    """一次 reconcile 的修复报告（用于 state/migrations 等审计）。"""
    course_key: str
    upcoming: int = 0                          # 新增为 DISCOVERED
    kept_completed: int = 0                    # 有强证据、保留 COMPLETED
    downgraded: int = 0                        # 由 COMPLETED 降级（污染修复）
    upgraded_ui: int = 0                       # 由 NONE/非COMPLETED 升为 COMPLETED(UI)
    repair_map: dict = field(default_factory=dict)   # {task_id: {before, after, reason}}

    def to_dict(self) -> dict:
        return {
            "course_key": self.course_key,
            "upcoming": self.upcoming,
            "kept_completed": self.kept_completed,
            "downgraded": self.downgraded,
            "upgraded_ui": self.upgraded_ui,
            "repair_map": self.repair_map,
        }


def _evidence_level(t: TaskRecord) -> str:
    """当前任务最可信的证据强度（completion_evidence 优先，回退 verification）。"""
    if getattr(t, "completion_evidence", None):
        lvl = t.completion_evidence.type
        if lvl not in ("NONE", ""):
            return lvl
    return (t.verification.level if getattr(t, "verification", None) else "NONE") or "NONE"


def has_strong_evidence(t: TaskRecord) -> bool:
    return _evidence_level(t) in ("SERVER_VERIFIED", "RECHECK")


def has_ui_evidence(t: TaskRecord) -> bool:
    return _evidence_level(t) == "UI"


def _dom_is_completed(cid: str, dom_status: dict) -> bool:
    return bool(cid) and dom_status.get(cid) == "completed"


def _sync_meta(rec: TaskRecord, info) -> None:
    """同步 discovery 的目录索引/标题到既有记录（不动状态机）。"""
    if hasattr(info, "_ch_idx"):
        rec._ch_idx = getattr(info, "_ch_idx", rec._ch_idx)
    if hasattr(info, "_cell_idx"):
        rec._cell_idx = getattr(info, "_cell_idx", rec._cell_idx)
    if getattr(info, "title", None):
        rec.title = info.title


def _make_discovered(info) -> TaskRecord:
    return TaskRecord(
        task_id=info.task_id,
        chapter_id=getattr(info, "chapter_id", "") or "",
        title=getattr(info, "title", "") or "",
        task_type=getattr(info, "task_type", "video") or "video",
        status="DISCOVERED",
        _ch_idx=getattr(info, "_ch_idx", 0),
        _cell_idx=getattr(info, "_cell_idx", 0),
    )


def _make_ui_completed(info) -> TaskRecord:
    cid = getattr(info, "chapter_id", "") or ""
    src = "server DOM completed marker"
    return TaskRecord(
        task_id=info.task_id,
        chapter_id=cid,
        title=getattr(info, "title", "") or "",
        task_type=getattr(info, "task_type", "video") or "video",
        status="COMPLETED",
        _ch_idx=getattr(info, "_ch_idx", 0),
        _cell_idx=getattr(info, "_cell_idx", 0),
        verification=Verification(level="UI", verified_at_utc=_now(), run_id="", source_detail=src),
        completion_evidence=CompletionEvidence(type="UI", source=src, run_id="", detail=src),
    )


def downgrade_to_unknown(t: TaskRecord) -> None:
    """把无有效证据的 COMPLETED 降级为 UNKNOWN（不再保持 COMPLETED，也不自动执行）。"""
    t.status = "UNKNOWN"
    t.updated_at_utc = _now()


def reconcile_registry(
    course_key: str,
    existing: dict[str, TaskRecord],
    discovery_tasks: list,
    dom_status: Optional[dict] = None,
) -> tuple[dict[str, TaskRecord], ReconcileReport]:
    """将现有 Registry 与最新 Discovery 校准为 canonical 状态。

    Args:
        existing: 现有 registry（{task_id: TaskRecord}）
        discovery_tasks: build_tasks_from_discovery() 输出的 TaskInfo 列表
        dom_status: {chapter_id: 'completed'|'pending'|'unknown'} 服务器 DOM 渲染状态

    Returns:
        (new_registry, report)
    """
    dom_status = dom_status or {}
    report = ReconcileReport(course_key=course_key)

    # 迁移旧 task_id（title 匹配但 task_id 格式已变）
    # 按 title 建立 discovery 索引，用于旧条目迁移
    by_title: dict[str, object] = {}
    for t in discovery_tasks:
        if getattr(t, "title", None):
            by_title[t.title] = t

    new_tids = {t.task_id for t in discovery_tasks}
    upgraded: dict[str, TaskRecord] = {}
    repaired: dict[str, TaskRecord] = {}

    for old_tid, old_rec in list(existing.items()):
        if old_tid in new_tids:
            # 保留给统一主循环
            upgraded[old_tid] = old_rec
            continue
        # 旧 task_id 不在最新 discovery：可能发生了 task_id 格式迁移
        matched = by_title.get(old_rec.title)
        if matched and getattr(matched, "task_id", None):
            migrated = TaskRecord(
                task_id=matched.task_id,
                chapter_id=getattr(matched, "chapter_id", "") or "",
                title=matched.title or old_rec.title,
                task_type="video",
                status=("COMPLETED" if (has_strong_evidence(old_rec) or has_ui_evidence(old_rec))
                        else "UNKNOWN"),
                priority=old_rec.priority,
                _ch_idx=getattr(matched, "_ch_idx", 0),
                _cell_idx=getattr(matched, "_cell_idx", 0),
                completion_evidence=old_rec.completion_evidence,
                verification=old_rec.verification,
            )
            if not (has_strong_evidence(old_rec) or has_ui_evidence(old_rec)):
                report.repair_map[old_tid] = {
                    "before": "COMPLETED", "after": "UNKNOWN",
                    "reason": "migration, no completion evidence"}
                report.downgraded += 1
            repaired[matched.task_id] = migrated
        else:
            # 不在最新 discovery，也没匹配到新 title → 保留诊断，不删除历史。
            # 若曾是 COMPLETED 但无证据 → 修正为 UNKNOWN。
            if old_rec.status == "COMPLETED" and not (has_strong_evidence(old_rec) or has_ui_evidence(old_rec)):
                downgrade_to_unknown(old_rec)
                report.repair_map[old_tid] = {
                    "before": "COMPLETED", "after": "UNKNOWN", "reason": "no evidence, not in latest discovery"}
                report.downgraded += 1
            repaired[old_tid] = old_rec

    # 统一 canonical 状态
    result: dict[str, TaskRecord] = dict(existing)
    result.update(repaired)
    result.update(upgraded)

    for t in discovery_tasks:
        tid = t.task_id
        cid = getattr(t, "chapter_id", "") or ""
        dom_done = _dom_is_completed(cid, dom_status)
        old = result.get(tid)
        if dom_done:
            # 服务器 DOM completed 标记
            if old is None:
                result[tid] = _make_ui_completed(t)
                report.upgraded_ui += 1
                report.repair_map[tid] = {
                    "before": "absent", "after": "COMPLETED",
                    "reason": "server DOM completed marker"}
            elif has_strong_evidence(old):
                # 强证据优先，仅同步元数据
                _sync_meta(old, t)
                report.kept_completed += 1
            else:
                # 无强证据：若已 COMPLETED 且仅 NONE/弱证据，补 UI(server DOM) 证据；
                # 若尚未 COMPLETED → 升为 COMPLETED(UI)。
                if old.status != "COMPLETED":
                    result[tid] = _make_ui_completed(t)
                    report.upgraded_ui += 1
                    report.repair_map[tid] = {
                        "before": old.status, "after": "COMPLETED",
                        "reason": "server DOM completed marker"}
                else:
                    # 已 COMPLETED：确保携带 UI evidence（server DOM），不再裸 COMPLETED
                    if _evidence_level(old) == "NONE":
                        old.completion_evidence = CompletionEvidence(type="UI", source="server DOM completed marker",
                                                                     run_id="", detail="server DOM completed marker")
                        old.verification = Verification(level="UI", verified_at_utc=_now(),
                                                        run_id="", source_detail="server DOM completed marker")
                        report.upgraded_ui += 1
                        report.repair_map[tid] = {
                            "before": "COMPLETED(NONE)", "after": "COMPLETED(UI)",
                            "reason": "server DOM completed marker attached as UI evidence"}
                    _sync_meta(old, t)
        else:
            # 服务器 DOM 未显示完成
            if old is None:
                if (getattr(t, "task_type", "video") or "video") != "video":
                    # 非 video 残余 task：不自动执行，标 pending/unsupported（§6）
                    rec = _make_discovered(t)
                    rec.status = "PENDING"
                    rec.task_type = t.task_type
                    result[tid] = rec
                    report.upcoming += 1
                else:
                    result[tid] = _make_discovered(t)
                    report.upcoming += 1
            elif old.status == "COMPLETED":
                if has_strong_evidence(old):
                    # 强证据保留（服务器 DOM 可能延迟 / 不稳定）
                    result[tid] = old
                    report.kept_completed += 1
                else:
                    # UI 或空证据 + 当前服务器未确认 → 降级为 UNKNOWN
                    downgrade_to_unknown(old)
                    report.downgraded += 1
                    report.repair_map[tid] = {
                        "before": "COMPLETED", "after": "UNKNOWN",
                        "reason": f"server DOM='{dom_status.get(cid,'unknown')}', no strong evidence"}
            else:
                _sync_meta(old, t)
                result[tid] = old

    return result, report