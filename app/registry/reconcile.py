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

from app.registry.task_registry import (
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
    healed_by_server: int = 0                  # 非 COMPLETED 记录由服务端 finished 恢复
    phantom_pruned: int = 0                    # D13：live 枚举里不存在的幻影视频点被收掉
    repair_map: dict = field(default_factory=dict)   # {task_id: {before, after, reason}}

    def to_dict(self) -> dict:
        return {
            "course_key": self.course_key,
            "upcoming": self.upcoming,
            "kept_completed": self.kept_completed,
            "downgraded": self.downgraded,
            "upgraded_ui": self.upgraded_ui,
            "healed_by_server": self.healed_by_server,
            "phantom_pruned": self.phantom_pruned,
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
    """把无有效证据的 COMPLETED 降级为 UNKNOWN（不再保持 COMPLETED，也不自动执行）。

    触发即「服务器回退」信号：此前服务器确认完成/有证据，现服务器不再承认 →
    调用 mark_rollback()，使调度可在开新课前优先补齐该章。
    """
    t.mark_rollback()
    t.status = "UNKNOWN"
    t.updated_at_utc = _now()


def reconcile_registry(
    course_key: str,
    existing: dict[str, TaskRecord],
    discovery_tasks: list,
    dom_status: Optional[dict] = None,
    live_pending: Optional[set] = None,
    live_finished: Optional[set] = None,
) -> tuple[dict[str, TaskRecord], ReconcileReport]:
    """将现有 Registry 与最新 Discovery 校准为 canonical 状态。

    Args:
        existing: 现有 registry（{task_id: TaskRecord}）
        discovery_tasks: build_tasks_from_discovery() 输出的 TaskInfo 列表
        dom_status: {chapter_id: 'completed'|'pending'|'unknown'} 服务器 DOM 渲染状态
        live_pending: 由 live verification（L2/L3）确认「当前确实未完成」的 task_id 集合。
                      非 None 时用于「COMPLETED 被实时状态覆盖」的校准（E6.2）
        live_finished: 由 live verification 确认「服务端已判 finished」的 task_id 集合。
                      用于 BLOCKED 任务的服务端真源恢复（用户手动看完某点的场景）

    Returns:
        (new_registry, report)
    """
    dom_status = dom_status or {}
    live_pending = live_pending if live_pending is not None else set()
    live_finished = live_finished if live_finished is not None else set()
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
    migrated_old: list[str] = []   # P0-11：迁移中被替换的旧 task_id（唯一化，需从结果淘汰）

    for old_tid, old_rec in list(existing.items()):
        if old_tid in new_tids:
            # 保留给统一主循环
            upgraded[old_tid] = old_rec
            continue
        # 旧 task_id 不在最新 discovery：可能发生了 task_id 格式迁移。
        # 但点级记录（`<cid>:videoN` / `<cid>:other`）不是任何 id 的旧格式 —— 它与
        # `<cid>` 同 title，一旦走 by_title 匹配就会被当成"格式迁移"合并掉，整章的
        # 兄弟点账目随之消失（第 4 轮 M0 run1：`tasks=82 → 74`，少的是 8 条点级记录），
        # 而"已确认的点不再重投"的护栏正因缺少兄弟点而失效。
        matched = None if ":" in old_tid else by_title.get(old_rec.title)
        if matched and getattr(matched, "task_id", None):
            was_blocked = getattr(old_rec, "status", "") == "BLOCKED"
            migrated = TaskRecord(
                task_id=matched.task_id,
                chapter_id=getattr(matched, "chapter_id", "") or "",
                title=matched.title or old_rec.title,
                task_type="video",
                status=("BLOCKED" if was_blocked
                        else ("COMPLETED" if (has_strong_evidence(old_rec)
                                              or has_ui_evidence(old_rec))
                              else "UNKNOWN")),
                priority=old_rec.priority,
                _ch_idx=getattr(matched, "_ch_idx", 0),
                _cell_idx=getattr(matched, "_cell_idx", 0),
                completion_evidence=old_rec.completion_evidence,
                verification=old_rec.verification,
                consecutive_failures=getattr(old_rec, "consecutive_failures", 0) or 0,
                max_attempts=getattr(old_rec, "max_attempts", 3) or 3,
                attempt_count=getattr(old_rec, "attempt_count", 0) or 0,
            )
            if migrated.status == "UNKNOWN":
                report.repair_map[old_tid] = {
                    "before": "COMPLETED", "after": "UNKNOWN",
                    "reason": "migration, no completion evidence"}
                report.downgraded += 1
            repaired[matched.task_id] = migrated
            migrated_old.append(old_tid)   # P0-11：旧 key 迁移后唯一化并淘汰
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
    # P0-11：迁移产生的旧 task_id 一律淘汰，保证 canonical 唯一（不双键）。
    for _old in migrated_old:
        result.pop(_old, None)
    result.update(repaired)
    result.update(upgraded)

    for t in discovery_tasks:
        tid = t.task_id
        cid = getattr(t, "chapter_id", "") or ""
        dom_done = _dom_is_completed(cid, dom_status)
        old = result.get(tid)
        # 已 BLOCKED 的任务：reconcile 不得把它复活。否则每轮 live/DOM 仍显示
        # 「未完成」→ 下面对其 mark_stale+downgrade_to_pending → status 回到
        # PENDING → 反复重跑一个已达失败上限（如 headed-Xvfb 抓不到 <video> 的
        # 1217304719）的任务，形成无限循环。BLOCKED 保持冻结，等显式/冷却恢复。
        # 例外（2026-09-21 用户选定方案1）：live job points 读到**本点**已被服务端
        # 判 finished（用户手动看完的场景）→ 以 SERVER_VERIFIED 证据恢复为
        # COMPLETED。replay 在此场景产生不了真实成功事件（点已完成不会播），
        # 服务端判定本身就是那个真实事件。失败计数保留不清（留痕）。
        if old is not None and getattr(old, "status", "") == "BLOCKED":
            if tid in live_finished:
                _heal_by_server_truth(tid, old, report)
            result[tid] = old
            continue
        # D14：服务端 finished 判定本身就是那个"真实事件"，治愈不该只发给 BLOCKED。
        # 真站 1217304738 把已被服务端判 finished 的点留在 UNKNOWN 投了出去，而
        # 页面绝不为已完成点起流（探针实测：点它只拿到 metadata，ct 冻结不动）→
        # Step F 等 metadata 90s×2 超时 FAIL，点记 FAILED、课程被推向熔断。
        if (old is not None and tid in live_finished
                and getattr(old, "status", "") != "COMPLETED"):
            _heal_by_server_truth(tid, old, report)
            result[tid] = old
            continue
        if dom_done:
            # P0-09：即使服务器 DOM 标 completed，若 live 复核确认该 task 仍有
            # 未完成的真实视频点（live_pending），也不能静默 COMPLETED → 漏课。
            # live 真源优先于 DOM 标记（服务器擅长 DOM 缓存可能延迟/被污染）。
            if tid in live_pending:
                if old is not None:
                    old.mark_stale(detail="live verification: now pending "
                                          "(DOM showed completed)")
                    old.downgrade_to_pending(detail="live re-check confirmed pending")
                    old.task_type = getattr(t, "task_type", "video") \
                        or getattr(old, "task_type", "video")
                    result[tid] = old
                else:
                    rec = _make_discovered(t)
                    rec.status = "PENDING"
                    result[tid] = rec
                report.downgraded += 1
                report.repair_map[tid] = {
                    "before": "COMPLETED(DOM)", "after": "PENDING",
                    "reason": "DOM completed but live shows unfinished points"}
            elif old is None:
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
            elif old.status in ("COMPLETED", "STALE"):
                if tid in live_pending:
                    # E6.2：COMPLETED 被「实时状态」明确推翻（live verification 确认未完成）。
                    # 强证据也服从实时真相 —— 否则 registry 会变成错误缓存（4706 案例）。
                    old.mark_rollback()   # 服务器回退信号 → 调度优先补齐
                    old.mark_stale(detail="live verification: now pending")
                    old.downgrade_to_pending(detail="live re-check confirmed pending")
                    result[tid] = old
                    report.downgraded += 1
                    report.repair_map[tid] = {
                        "before": "COMPLETED", "after": "PENDING",
                        "reason": "live status override: server now shows task unfinished"}
                elif has_strong_evidence(old):
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


def _heal_by_server_truth(tid: str, rec: TaskRecord,
                                  report: "ReconcileReport") -> bool:
    """服务端真源恢复单个非 COMPLETED 记录（方案1 + D14）。命中返回 True。

    live job points 读到**本点**已被服务端判 finished（用户手动看完的场景）
    → 以 SERVER_VERIFIED 证据恢复为 COMPLETED。replay 在此场景产生不了
    真实成功事件（点已完成不会播），服务端判定本身就是那个真实事件。
    失败计数保留不清（留痕：曾连续失败到熔断）。
    """
    rec.status = "COMPLETED"
    rec.verification = Verification(
        level="SERVER_VERIFIED", verified_at_utc=_now(), run_id="",
        source_detail="live job points: server marked this point finished")
    rec.completion_evidence = CompletionEvidence(
        type="SERVER_VERIFIED", source="live job points",
        run_id="",
        detail="server finished marker on the exact point "
               "(manual watch / server truth)")
    report.healed_by_server += 1
    report.repair_map[tid] = {
        "before": "BLOCKED", "after": "COMPLETED",
        "reason": "server live truth: point finished"}
    return True


def heal_blocked_by_live(
    course_key: str,
    existing: dict[str, TaskRecord],
    discovery_tasks: list,
    dom_status: Optional[dict],
    verify_points,
) -> tuple[dict[str, TaskRecord], ReconcileReport]:
    """冻结章的服务端真源恢复 + 幻影点清理（方案1 + D13/D14）。

    对每个含 BLOCKED 任务的章调用 `verify_points(cid) -> list[dict] | None`
    （生产里是 live_verify_chapter 的 points；测试里是桩），把读到的 finished
    点汇入 live_finished 交给 reconcile_registry。只读冻结章 —— 健康章不烧
    L2 成本；读数失败/无 finished → 保持冻结（恢复必须踩在服务端真源上）。
    """
    frozen = sorted({
        (t.chapter_id or "") for t in existing.values()
        if getattr(t, "status", "") == "BLOCKED" and (t.chapter_id or "")
    })
    if not frozen:
        return existing, ReconcileReport(course_key=course_key)
    from tvdp.tdvp import build_live_finished
    live_done: set[str] = set()
    live_seqs: dict[str, set[int]] = {}
    for cid in frozen:
        pts = verify_points(cid) or []
        live_done |= build_live_finished(pts)
        seqs: set[int] = set()
        for pt in pts:
            if pt.get("type") != "video":
                continue
            pt_tid = str(pt.get("task_id") or "")
            if pt_tid == cid:
                seqs.add(1)                      # 第 1 个视频点沿用章节 id
            elif pt_tid.startswith(f"{cid}:video"):
                try:
                    seqs.add(int(pt_tid[len(cid) + 6:]))
                except ValueError:
                    pass
        live_seqs[cid] = seqs
    report = ReconcileReport(course_key=course_key)
    for tid in list(existing):
        rec = existing[tid]
        status = getattr(rec, "status", "")
        # D14：服务端 finished 判定本身就是那个"真实事件" —— 治愈不该只发给
        # BLOCKED。FAILED/UNKNOWN/PENDING/DISCOVERED 一律按真源落 COMPLETED
        # （失败计数留痕不清），否则引擎会反复投递页面不肯起流的已完成点。
        if status != "COMPLETED" and tid in live_done:
            _heal_by_server_truth(tid, rec, report)
            continue
        # D13：冻结章的 live 枚举里没有这个视频序号 ⇒ 幻影点（cards 帧裸
        # `.ans-job-icon` 被启发式判成 video 而 mint 出来的），它永远不会产生
        # 任何真实事件，却占着投递名额并撞 90s 死等 —— 从账本收掉。
        cid = str(getattr(rec, "chapter_id", "") or "")
        seqs = live_seqs.get(cid) or set()
        if not seqs or status not in ("DISCOVERED", "UNKNOWN"):
            continue
        if getattr(rec, "consecutive_failures", 0) or not tid.startswith(f"{cid}:video"):
            continue
        try:
            seq = int(tid[len(cid) + 6:])
        except ValueError:
            continue
        if seq > max(seqs):
            del existing[tid]
            report.phantom_pruned += 1
    return existing, report


def restore_blocked_for_manual(existing: dict[str, TaskRecord]) -> list[str]:
    """人工显式恢复：把 BLOCKED 点解冻回 PENDING，返回被恢复的 task_id 列表。

    `heal_blocked_by_live` 只能治"服务端其实已经判完成"的点；像 `1217304738:video2`
    这种真没学成的点，冻结后既进不了队列、又是章内剩余工作量的唯一承载者 → 整章搁浅。
    只在 manual 触发腿调用（判据在 scheduler 侧），schedule 腿不放宽熔断。
    """
    return [tid for tid, rec in existing.items() if rec.restore_for_manual_retry()]


def pick_conflict_chapters(
    chapter_ids: list[str],
    existing: dict[str, TaskRecord],
    dom_pending: set[str],
) -> list[str]:
    """选「需要 L2 live 复核」的章节：registry 里有 COMPLETED（视频已完成/强证据）
    但服务器 DOM 此刻显示该章待完成 → 存在 COMPLETED 被实时状态推翻的嫌疑；
    或该章有 BLOCKED 任务 —— 其服务端真源恢复（方案1）也依赖 live 读数。

    返回该章 id 列表；只对这些章做 read_chapter_job_points（成本梯度）。
    """
    conflicted = set()
    for cid in chapter_ids:
        recs = [r for t, r in existing.items() if r.chapter_id == cid]
        completed = [r for r in recs if r.status == "COMPLETED"]
        completed_video = [
            r for r in completed
            if (getattr(r, "task_type", "video") or "video") == "video"
        ]
        blocked = [r for r in recs if r.status == "BLOCKED"]
        if (completed or completed_video) and cid in dom_pending:
            conflicted.add(cid)
        elif blocked:
            conflicted.add(cid)
    # 按原有章节顺序返回，保持确定性
    return [c for c in chapter_ids if c in conflicted]


def has_unfinished_video_sibling(existing: dict, rec) -> bool:
    """同章是否另有**未完成**的 video 记录 —— 即章内剩余视频工作另有承载者。

    章级快照只能说"这章还有视频点没做完"，说不出是哪一个。而 registry 是一条一点
    （`<cid>` = 第 1 点，`<cid>:videoN` = 第 N 点）。把章级结论套在已完成的点上，
    就会重播第 1 点、而 `:videoN` 永远轮不到（1217304708 实况）。
    BLOCKED 的兄弟点也算承载者：活在那儿但被冻住，重播已完成的点同样不解决问题。
    """
    my_tid = getattr(rec, "task_id", "")
    cid = getattr(rec, "chapter_id", "")
    for other in existing.values():
        if other is rec or getattr(other, "task_id", "") == my_tid:
            continue
        if getattr(other, "chapter_id", "") != cid:
            continue
        if (getattr(other, "task_type", "") or "video") != "video":
            continue
        if getattr(other, "status", "") == "COMPLETED":
            continue
        return True
    return False


def point_is_server_verified(t) -> bool:
    """该**点**自身带服务端确认（`SERVER_VERIFIED` + 非空 `passed_object_ids`）。

    为什么单独一个判据：`job_remaining` 是**整章**的粗读数（含达标测试/PPT 这些引擎
    永远做不了的点），而这条是**该点**的细真源 —— 粗读数不得推翻细读数。它不依赖
    点级快照缓存，所以缓存被清/未预热时同样成立（否则保护会随缓存冷热而漂移）。
    判据本体在 `TaskRecord.point_is_server_verified`。
    """
    return t.point_is_server_verified()


def stale_completed_by_catalog(
    existing: dict[str, TaskRecord],
    chapters_raw: list[dict],
    points_map: Optional[dict] = None,
) -> list[str]:
    """目录层校准：凡是 raw 目录显示「仍有待完成任务点」(job_remaining>0) 的章，
    其 COMPLETED video task 一律降级为 STALE（真实还有未完成点 ⇒ 完成态可疑）。

    只返回被降级任务的 task_id；调用方负责 mark_stale + save_registry。
    这是不依赖逐章浏览器复核的库一级校准（L1），成本仅为目录解析。

    但目录的 `job_remaining` 计的是**整章所有任务点**，含达标测试/PPT —— 引擎永远
    做不了它们。所以两种情况下不再降级：该点已有服务端确认（细真源覆盖粗读数，
    且不依赖快照缓存），或点级快照显示该章视频点已全部 finished。否则该章会被
    无限降级重排（第 3 轮 M0 里 9 章 COMPLETED→UNKNOWN 的来路）。
    """
    from app.registry.task_registry import chapter_done_from_snapshot
    pending_cids = {
        str(c.get("chapter_id") or "")
        for c in (chapters_raw or [])
        if int(c.get("job_remaining", 0) or 0) > 0
    }
    downgraded = []
    for tid, t in existing.items():
        if t.status != "COMPLETED":
            continue
        if t.chapter_id not in pending_cids:
            continue
        if point_is_server_verified(t):
            continue          # 点级服务端确认 > 章级计数，且不依赖快照缓存
        if chapter_done_from_snapshot(t.chapter_id, points_map or {}) is True:
            continue          # 视频点已尽，剩下的是引擎做不了的点
        downgraded.append(tid)
    return downgraded


def stale_completed_by_points(
    existing: dict[str, TaskRecord],
    points_map: dict,
) -> list[str]:
    """洞2 点级校准：凡快照显示该章有 video 点、且并未全部 finished 的章，
    无论 registry 是否把它记 COMPLETED，都降级（完成态已被实时推翻）。

    与 stale_completed_by_catalog 互补：catalog 用 job_remaining 计数，
    这里用点级快照（video_total / video_finished）——更精确。

    快照是**章级**的，记录是**点级**的：若该章另有未完成的视频兄弟记录（`:videoN`），
    未完成量由它扛，不许把已完成的第 1 点降级重播。兄弟记录不存在时才降级 ——
    这正是 4708「双视频只播 1 个」当年需要的保护。

    返回需要降级的任务 task_id；调用方负责 mark_stale + save。
    """
    from app.registry.task_registry import chapter_done_from_snapshot
    downgraded = []
    for tid, t in existing.items():
        if t.status != "COMPLETED":
            continue
        cid = t.chapter_id
        done_state = chapter_done_from_snapshot(cid, points_map or {})
        if done_state is False:                # 有未 finish 的视频点
            if has_unfinished_video_sibling(existing, t):
                continue
            downgraded.append(tid)
    return downgraded


def run_calibrated_reconcile(
    course_key: str,
    existing: dict[str, TaskRecord],
    discovery_tasks: list,
    dom_status: Optional[dict] = None,
    chapter_ids: Optional[list[str]] = None,
    page=None,
    course_params: Optional[dict] = None,
    live_pending_override: Optional[set] = None,
    live_finished_override: Optional[set] = None,
) -> tuple[dict[str, TaskRecord], ReconcileReport]:
    """Discovery → (可选) L2 live 复核冲突章 → Reconcile。

    对「有 COMPLETED 且服务器 DOM 待完成」的冲突章节，若传入 page 与 course_params，
    则调用 tdvp.read_chapter_job_points() 读取实时任务点，把其「未完成」的 task_id
    汇入 live_pending，交给 reconcile_registry 在 reconcile 内把强证据 COMPLETED
    降级为 PENDING（E6.2：完成状态可被实时状态推翻）。

    live_pending_override 用于测试在无浏览器时手动注入 live_pending。
    """
    dom = dom_status or {}
    dom_pending = {cid for cid, s in dom.items() if s == "pending"}
    chapter_ids = chapter_ids or [
        t.chapter_id for t in discovery_tasks if t.chapter_id
    ]
    live = set(live_pending_override or set())
    live_done = set(live_finished_override or set())

    conflicted = pick_conflict_chapters(chapter_ids, existing, dom_pending)
    if page is not None and course_params and conflicted:
        from tvdp.tdvp import (build_live_finished, build_live_pending,
                               read_chapter_job_points)
        for cid in conflicted:
            job_pts = read_chapter_job_points(
                page, cid,
                course_params.get("course_id", ""),
                course_params.get("clazz_id", ""),
                course_params.get("cpi", ""),
            )
            live |= build_live_pending(job_pts)
            live_done |= build_live_finished(job_pts)

    return reconcile_registry(
        course_key, existing, discovery_tasks, dom_status=dom,
        live_pending=live, live_finished=live_done,
    )