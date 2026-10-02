"""E: Account-first server bootstrap reconciliation (P0-3, issue #4 tail).

问题（issue #4 根因之二，已由 P0-2 修正命名空间——本模块管「真源链第一跳」）：
`progress.completed` 原本只从本地 registry 的「已完成章数」推导（scheduler
  `_sync_progress_from_registry`），而 registry 只记「要做的活 + 已由引擎确认完成的点」，
  服务端早已完成、不需再做的章根本不进 registry → 任何账号的 completed 都是「本地做过
  几次」，不是「服务端已完成几个」。这正是 31→12→(新账号)0 这类困惑的机制根源。

目标：**账号首次进入课程（该账号命名空间该课程 registry 为空）→ 以服务端真源一次性
材料化** work 列表，**并把 `progress.completed` 写成「服务端完成全集」**，而不是
「本地 registry 已完成章数」。即确立真源链：
    SERVER ──canonical─▶ 账号命名空间 progress.completed ──▶ UI
    （本地 registry 只保留需要执行的活）

不变式 / 使失能：
  - **幂等**：该账号该课程 registry 已非空 → NOOP，绝不覆盖、不打服务器。
  - **服务端主导**：完成数取自登录后真实会话的 catalog（`status=="completed"`）+ live
    点的 isFinished，不信任外来本地账。
  - 复用已有、真站跑通的组件：`fetch_course_detail_and_verify` / `build_tasks_from_discovery`
    / `reconcile_registry` / `update_course_state`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BootstrapReport:
    """bootstrap 摘要。"""
    course_key: str
    mode: str = "noop"            # bootstrap | noop
    reason: str = ""              # noop/失败原因
    status: str = "ok"            # ok | error | skipped
    server_completed: int = 0     # 服务端（catalog）判定已完成的章数
    total_tasks: int = 0          # 材料化后 registry 任务数
    at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _server_completed_from_catalog(chapters: list) -> int:
    """用真实会话 catalog 的 `status==completed` 数作「服务端完成章数」。"""
    return sum(1 for ch in chapters
               if (ch.get("status") or "").strip().lower() in ("completed", "done"))


@dataclass
class InheritReport:
    """legacy→account 继承摘要。"""
    course_key: str
    mode: str = "noop"            # inherit | noop
    status: str = "ok"            # ok | error
    legacy_tasks: int = 0
    inherited: int = 0            # 从 legacy 拷进 account 账的条数
    total_tasks: int = 0          # 继承后 account 账任务数
    reason: str = ""
    at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def inherit_from_legacy(course_key: str) -> InheritReport:
    """P0-2 继承：把 legacy 命名空间的真实工作历史合并进当前账号命名空间。

    问题（issue #4 第六层卡点）：P0-2 账号隔离把 9 月积累的全部真账
    （78 任务 / 63 video / 48 COMPLETED）留在 `state/registry/<course_key>/`，
    而账号命名空间只有 30 条退化账（29 other + 1 已 COMPLETED video）。
    `reconcile_queue` 只收 video，退化账唯一 video 已 COMPLETED → queue 恒空 →
    `upcoming=0` → `_ensure_bootstrap_on_start` 幂等 NOOP → 永远造不出 video 任务
    （自增强死循环）。继承把 legacy 的 63 条 video 带进 account 账，queue 即非空。

    幂等护栏（与 `bootstrap_registry_from_server` 同一条原则）：
      - legacy 账不存在/为空 → noop，绝不动 account 账；
      - account 命名空间该课程账已含 **≥1 条未完成的 video 任务**（PENDING/
        DISCOVERED/FAILED/UNKNOWN/STALE/RUNNING/READY）→ 视为「继承已完成」，
        noop，绝不重复拷、绝不覆盖既有 account 记录。

      **只有 COMPLETED 的 video 不触发幂等护栏** —— 退化账（issue #4 的 30 条账）
      恰好含 1 条已 COMPLETED video，被旧判据（"已有 video"）误锁；改成"已有 open
      video"后，仅 COMPLETED 的退化账仍允许继承，把 legacy 的 PENDING video 带进来。

    合并语义：account 既有记录**优先**保留（它可能带更新的运行时状态，如某章
    COMPLETED 的 UI 证据），legacy 仅补 account 缺失的 task_id。两条账的 key 空间
    不重叠（legacy 多用 bare `<cid>` / `<cid>:videoN`，account 退化账用
    `<cid>:other`），直接按 task_id 并集即可，无需按章去重。

    绝不修改 legacy 源账；只在当前账号命名空间（由调用方 context 决定，测试里用
    `models.set_account_id_hook` 注入）写。
    """
    from app.registry.task_registry import (
        load_registry, save_registry, load_legacy_registry)

    legacy = load_legacy_registry(course_key)
    if not legacy:
        return InheritReport(course_key, mode="noop", status="ok",
                             reason="legacy registry absent or empty; nothing to inherit")

    existing = load_registry(course_key)
    # 幂等判据必须是「account 已有**未完成**的 video 任务」——只有 COMPLETED 的退化账
    # （issue #4 的 30 条账恰含 1 条已 COMPLETED video）仍属死循环：queue 恒空、
    # bootstrap 永久 NOOP。只有存在 PENDING/DISCOVERED/FAILED/UNKNOWN video 才视为
    # 「继承已完成，别再重拷」。
    _OPEN_VIDEO = ("PENDING", "DISCOVERED", "FAILED", "UNKNOWN", "STALE", "RUNNING", "READY")
    if any((t.task_type or "video") == "video" and t.status in _OPEN_VIDEO
           for t in existing.values()):
        return InheritReport(
            course_key, mode="noop", status="ok", legacy_tasks=len(legacy),
            total_tasks=len(existing),
            reason="account registry already has open video tasks; inherit is idempotent")

    inherited = 0
    for tid, rec in legacy.items():
        if tid in existing:
            continue           # account 既有记录优先，保留更新状态
        existing[tid] = rec
        inherited += 1
    save_registry(course_key, existing)
    return InheritReport(
        course_key, mode="inherit", status="ok", legacy_tasks=len(legacy),
        inherited=inherited, total_tasks=len(existing),
        reason=f"inherited {inherited} task(s) from legacy into account namespace")


def bootstrap_registry_from_server(
    course_key: str,
    course_url: str,
    *,
    cx_user: Optional[str] = None,
    cx_pass: Optional[str] = None,
    persist_progress: bool = True,
) -> BootstrapReport:
    """账号首次进入课程：由服务端真源材料化 work 列表，并把 progress 写成服务端完成数。

    `course_key` 传**课程级 key**（`<course_id>_<clazz_id>`，即 `identity.key()`）；账户
    命名空间由当前登录账号（CX_USER / hook）经 P0-2 的存储层注入。`identity 的账号字段。

    Returns:
        BootstrapReport（败则 status="error"）。
    """
    from app.registry.task_registry import load_registry
    from tvdp.tdvp import fetch_course_detail_and_verify
    from state.course_state import update_course_state, CourseProgress

    # ── 幂等：该账号命名空间已有该课程任何记录 → 不覆盖、不打服务器 ──
    existing = load_registry(course_key)
    if existing:
        return BootstrapReport(course_key, mode="noop", status="ok",
                               reason="registry already present; not touched")

    _fetch = fetch_course_detail_and_verify(course_url,
                                            cx_user=cx_user, cx_pass=cx_pass)
    if not _fetch:
        return BootstrapReport(course_key, mode="bootstrap",
                               reason="server fetch failed (None)", status="error")
    return _materialize_from(course_key, course_url, _fetch,
                             persist_progress=persist_progress)


def materialize_from_common(course_key: str, course_url: str, combined,
                            persist_progress: bool = True) -> BootstrapReport:
    """用一次已抓取的服务端响应材料化（供调度首轮复用同一浏览器，避免双登踢会话）。

    `combined` = `fetch_course_detail_and_verify` 的返回（`{"chapters":[...], "points":[...]}`），
    由外部（如 scheduler）抓好后传入；本函数只做 reconcile 材料化 + 写 progress，不碰网络。
    已存在 registry 时仍幂等 NOOP。
    """
    from app.registry.task_registry import load_registry
    if load_registry(course_key):
        return BootstrapReport(course_key, mode="noop", status="ok",
                               reason="registry already present; not touched")
    return _materialize_from(course_key, course_url, combined,
                             persist_progress=persist_progress)


def _materialize_from(course_key, course_url, combined,
                      persist_progress: bool = True) -> BootstrapReport:
    from app.registry.task_registry import save_registry
    from app.registry.reconcile import reconcile_registry
    from tvdp.tdvp import build_tasks_from_discovery, build_live_finished

    chapters = combined.get("chapters") or []
    points = combined.get("points") or []
    live_done = build_live_finished(points)

    # ── P0-3 根因修复（issue #4）：bootstrap 必须产出可执行的 video 任务 ──
    # `build_tasks_from_discovery` 只有拿到 `video_counts[cid]>0` 才产 video task；
    # 否则所有未完成章都走 `other` 兜底 → registry 全 other → reconcile_queue 只收
    # video（见 task_registry.reconcile_queue）→ queue 恒空 → scheduler 报
    # "No pending task / probe empty" → 又一个 NOOP。而 combined 响应里已带本次
    # 登录会话读到的队首章点级数据（`points`），正好可反推出 video_counts。
    # 只修复"账上至少有一个 video 可执行任务"这一最小正确性；其余章保持 other，
    # 由 scheduler 每轮 Step 4.5 的 live 复核渐进补全（该机制已内建）。
    from app.registry.task_registry import materialize_video_counts_from_points
    video_counts = materialize_video_counts_from_points(course_key, points)

    discovery_tasks = build_tasks_from_discovery(chapters, video_counts=video_counts)
    if not discovery_tasks:
        return BootstrapReport(course_key, mode="bootstrap",
                               reason="no tasks from server catalog", status="error")

    dom_status: dict[str, str] = {}
    for ch in chapters:
        cid = str(ch.get("chapter_id") or "")
        if cid:
            dom_status[cid] = ch.get("status", "unknown")

    registry, _rep = reconcile_registry(
        course_key, {}, discovery_tasks, dom_status=dom_status,
        live_finished=live_done)
    save_registry(course_key, registry)

    server_completed = _server_completed_from_catalog(chapters)
    if persist_progress:
        _set_progress_completed(course_key, course_url, server_completed,
                                len(chapters))

    return BootstrapReport(
        course_key, mode="bootstrap", status="ok",
        server_completed=server_completed,
        total_tasks=len(registry),
        reason=f"materialized from server ({len(chapters)} chapters, "
               f"{len(registry)} tasks, server_completed={server_completed})",
    )


def _set_progress_completed(course_key: str, course_url: str,
                            server_completed: int, total: int) -> None:
    """把 `course_state.progress.completed` 设为服务端完成数（per-account, lock+RMW）。"""
    from state.course_state import update_course_state

    def updater(state):
        return _apply_server_progress(state, course_key, course_url,
                                      server_completed, total)

    update_course_state(course_key, updater)


def _apply_server_progress(state, course_key, course_url,
                           server_completed: int, total: int):
    """把 service 真源完成数写进该账号命名空间的 course_state；account 首次时创建它。

    bootstrap 是「账号首次进入课程」的**创建性质**动作（registry 空 = 已由 NOOP 护栏保证），
    因此这里允许在账号命名空间里尚无 course_state 时新建一个最小实例（status=ACTIVE），
    首跑即把 `progress.completed` 写成服务端完成数 —— 这样「progress 真源」才能落盘，
    而不是因为此前无状态文件而静默跳过。
    """
    from state.course_state import CourseProgress, CourseState
    if state is None:
        state = CourseState(status="ACTIVE",
                            course_identity=_identity_from(course_key, course_url))
    if state.progress is None:
        state.progress = CourseProgress(completed=server_completed, total=total)
    else:
        state.progress.completed = server_completed
        if state.progress.total is None:
            state.progress.total = total
    return state


def _identity_from(course_key: str, course_url: str):
    """由课程级 key（`course_id_clazz`）与 URL 构造 course_identity。

    `save_course_state` 用 `identity.key()` 作文件名，因此 identity 的 course_id/clazz_id
    必须与 course_key 一致（否则会存成错位文件）。URL 里有显式参数优先，否则从 course_key
    拆分（形如 `<course_id>_<clazz_id>`）。
    """
    from models import CourseIdentity
    from datetime import datetime, timezone as _tz

    c_id, cl_id, cpi = "", "", ""
    try:
        from resolvers.course_resolver import _parse_url_params
        _p = _parse_url_params(course_url)
        c_id = _p.get("course_id") or ""
        cl_id = _p.get("clazz_id") or ""
        cpi = _p.get("cpi") or ""
    except Exception:
        pass
    if not c_id and "_" in course_key:
        head, _, tail = course_key.partition("_")
        if head:
            c_id, cl_id = head, tail
    return CourseIdentity(
        course_id=c_id,
        clazz_id=cl_id,
        cpi=cpi,
        title=f"course_{c_id}",
        raw_url=course_url,
        resolved_at_utc=datetime.now(_tz.utc).isoformat(),
    )