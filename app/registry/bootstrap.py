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

    discovery_tasks = build_tasks_from_discovery(chapters)
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