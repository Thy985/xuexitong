"""E6: Task Registry + Execution Queue

核心设计：
  Task Registry  = 真相源（由 Course Discovery 驱动更新）
  Execution Queue = 派生计划（每次 wake 时从 Registry 重算）

任务状态机：
  DISCOVERED → PENDING → READY → RUNNING → VERIFYING → COMPLETED
                                                    ↓
                                               FAILED → READY（可重试）
                                                    ↓
                                              BLOCKED（超过重试上限）

证据强度：
  UI         = 页面 DOM 观察（弱）
  SERVER     = 服务端 isPassed 验证（强）
  RECHECK    = 二次发现确认（最强）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Literal
from enum import Enum

from tvdp.tdvp import TaskStatus as TdvpStatus, TaskInfo as TdvpTaskInfo

# ── 类型定义 ───────────────────────────────────────────────────────
TaskPhase = Literal["DISCOVERED", "PENDING", "READY", "RUNNING", "VERIFYING",
                    "COMPLETED", "FAILED", "BLOCKED", "UNKNOWN", "STALE"]
EvidenceLevel = Literal["NONE", "UI", "SERVER_VERIFIED", "RECHECK", "CONFLICT"]


# ── 数据模型 ───────────────────────────────────────────────────────

@dataclass
class Verification:
    level: EvidenceLevel = "NONE"
    verified_at_utc: Optional[str] = None
    run_id: Optional[str] = None
    source_detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Verification":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Lease:
    run_id: Optional[str] = None
    started_at_utc: Optional[str] = None
    expires_at_utc: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lease":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CompletionEvidence:
    """任务完成的唯一 canonical 证据——用于回答 Which/When/Why/Server evidence。

    由 mark_completed 强制要求携带。绝不允许空证据的隐式完成。
    """
    type: EvidenceLevel = "NONE"               # SERVER_VERIFIED | UI | RECHECK | NONE
    source: str = ""                            # isPassed | server DOM | ...
    run_id: str = ""
    observed_at_utc: str = ""
    detail: str = ""
    passed_object_ids: list = field(default_factory=list)  # isPassed=true 的对象 ID

    def __post_init__(self):
        if not self.observed_at_utc:
            self.observed_at_utc = datetime.now(timezone.utc).isoformat()

    def is_valid(self) -> bool:
        """只有具备明确 server/runtime 证据才算有效；NONE 或空 detail 视为无效。"""
        return self.type not in ("NONE", "") and bool(self.source)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CompletionEvidence":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FailureRecord:
    """一次失败的可审计记录（含 stage / run_id / detail）。"""
    stage: str = ""                             # _derive_failure_stage() 值
    run_id: str = ""
    detail: str = ""
    consecutive_failures: int = 0
    occurred_at_utc: str = ""

    def __post_init__(self):
        if not self.occurred_at_utc:
            self.occurred_at_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FailureRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskRecord:
    """一个任务点的完整记录——真相源。

    状态机：
      DISCOVERED → PENDING → READY → RUNNING → VERIFYING → COMPLETED
                                                      ↓
                                                 FAILED → READY（可重试）
                                                      ↓
                                                BLOCKED（连续失败 ≥ max_attempts）

    完成的唯一路径是 mark_completed() 且必须携带有效证据；
    mark_failed() 由真实 runtime FAIL/ERROR/TIMEOUT 调用并立即持久化。
    """
    task_id: str
    chapter_id: str
    title: str
    task_type: str = "video"
    status: TaskPhase = "DISCOVERED"
    priority: int = 0                              # 0 = 目录顺序；越高越优先
    attempt_count: int = 0
    attempts: int = 0                              # 兼容旧字段（= attempt_count）
    consecutive_failures: int = 0
    max_attempts: int = 3
    lease: Lease = field(default_factory=Lease)
    verification: Verification = field(default_factory=Verification)   # 兼容旧字段
    completion_evidence: CompletionEvidence = field(default_factory=CompletionEvidence)
    failure: FailureRecord = field(default_factory=FailureRecord)
    created_at_utc: str = ""
    updated_at_utc: str = ""
    last_run_id: Optional[str] = None              # 最近一次执行 run_id
    last_run_at_utc: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_success_at_utc: Optional[str] = None
    last_failure_at_utc: Optional[str] = None
    _ch_idx: int = field(default=0, repr=False)    # DOM 目录索引（内部用）
    _cell_idx: int = field(default=0, repr=False)

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at_utc:
            self.created_at_utc = now
        if not self.updated_at_utc:
            self.updated_at_utc = now

    @property
    def key(self) -> str:
        return self.task_id

    @property
    def is_executable(self) -> bool:
        """任务是否可执行（READY、PENDING、STALE、刚发现的 DISCOVERED，或可重试的 FAILED）。"""
        if self.status in ("READY", "PENDING", "STALE", "DISCOVERED"):
            return True
        if self.status == "FAILED" and self.consecutive_failures < self.max_attempts:
            return True
        return False

    @property
    def chapter_id_for_url(self) -> str:
        """返回用于构造 URL 的 chapterId；无则返回空。"""
        return self.chapter_id or ""

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def mark_discovered(self) -> None:
        """由 Discovery/Reconciliation 置为待执行（无证据时不落入 COMPLETED）。"""
        now = self._now()
        self.status = "DISCOVERED"
        self.updated_at_utc = now

    def mark_started(self, run_id: str) -> None:
        """进入 RUNNING（获得 lease），记录 last_started_at。"""
        now = self._now()
        self.status = "RUNNING"
        self.lease = Lease(run_id=run_id, started_at_utc=now,
                           expires_at_utc=(datetime.now(timezone.utc)
                                           + timedelta(minutes=15)).isoformat())
        self.last_run_id = run_id
        self.last_run_at_utc = now
        self.last_started_at = now
        self.updated_at_utc = now

    def mark_verifying(self) -> None:
        self.status = "VERIFYING"
        self.updated_at_utc = self._now()

    def mark_completed(self, *, run_id: str,
                       evidence_level: EvidenceLevel = "SERVER_VERIFIED",
                       source: str = "isPassed",
                       detail: str = "",
                       passed_object_ids: list | None = None) -> None:
        """—— 唯一的完成入口，强制要求有效 evidence. ——

        没有传入有效证据（type=SOURCE 为空）时抛 ValueError，绝不允许隐式完成。
        """
        if not run_id:
            raise ValueError("mark_completed requires a run_id")
        if evidence_level in ("NONE", ""):
            raise ValueError(
                "mark_completed requires completion evidence; "
                "URL/nextUnit/navigation inferences are NOT valid completion"
            )
        now = self._now()
        self.status = "COMPLETED"
        self.attempt_count += 1
        self.consecutive_failures = 0            # 成功 → 重置失败计数
        self.lease = Lease()
        self.verification = Verification(level=evidence_level, verified_at_utc=now,
                                         run_id=run_id, source_detail=detail or source)
        self.completion_evidence = CompletionEvidence(
            type=evidence_level,
            source=source,
            run_id=run_id,
            observed_at_utc=now,
            detail=detail or source,
            passed_object_ids=list(passed_object_ids or []),
        )
        self.failure = FailureRecord()
        self.last_run_id = run_id
        self.last_run_at_utc = now
        self.last_started_at = now
        self.last_finished_at = now
        self.last_success_at_utc = now
        self.updated_at_utc = now

    def mark_failed(self, *, run_id: str, detail: str = "",
                    failure_stage: str = "") -> "bool":
        """标记失败：consecutive_failures+1，达阈值 → BLOCKED，否则 FAILED。
        调用方必须立即 save_registry() 持久化。
        返回 True 表示已达阈值进入 BLOCKED。
        """
        now = self._now()
        self.attempt_count += 1
        self.attempts = self.attempt_count              # 兼容同步
        self.consecutive_failures += 1
        self.lease = Lease()
        self.failure = FailureRecord(
            stage=((failure_stage or "").upper()),      # 统一大写（NO_CARDS_IFRAME 等）
            run_id=run_id or "",
            detail=detail or "",
            consecutive_failures=self.consecutive_failures,
            occurred_at_utc=now,
        )
        blocked = self.consecutive_failures >= self.max_attempts
        self.status = "BLOCKED" if blocked else "FAILED"
        self.last_run_id = run_id or self.last_run_id
        self.last_run_at_utc = now
        self.last_started_at = self.last_started_at or now
        self.last_finished_at = now
        self.last_failure_at_utc = now
        self.updated_at_utc = now
        return self.status

    def mark_stale(self, detail: str = "") -> None:
        """E6.2 校准：实时状态覆盖历史完成。

        当「实时服务端/DOM 显示该 task 未完成」而 registry 却记着 COMPLETED 时，
        先把其完成证据降级为 CONFLICT 并标记 STALE（不立刻丢完成状态，
        保留证据以便溯源），由 reconcile 决定是保留还是真正回退 PENDING。
        """
        now = self._now()
        self.status = "STALE"
        if self.completion_evidence and self.completion_evidence.type not in ("NONE", ""):
            self.completion_evidence = CompletionEvidence(
                type="CONFLICT",
                source=self.completion_evidence.source,
                run_id=self.completion_evidence.run_id,
                observed_at_utc=now,
                detail=(detail or "live status overrides prior completion")[:400],
                passed_object_ids=list(self.completion_evidence.passed_object_ids or []),
            )
        self.updated_at_utc = now

    def downgrade_to_pending(self, detail: str = "") -> None:
        """E6.2：完成状态被实时状态推翻，回退为 PENDING（可重入队列）。

        保留 completion_evidence/verification 以便审计（不删除历史），
        但 status 回到 PENDING → is_executable 为真 → 队列重选。
        """
        now = self._now()
        self.status = "PENDING"
        self.verification = Verification(level="CONFLICT", verified_at_utc=now,
                                         run_id="", source_detail=detail or "live re-check pending")
        self.updated_at_utc = now

    def to_dict(self) -> dict:
        d = asdict(self)
        d["attempts"] = self.attempt_count
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRecord":
        ch_idx = d.pop("_ch_idx", 0)
        cell_idx = d.pop("_cell_idx", 0)
        lease = d.pop("lease", {}) or {}
        verification = d.pop("verification", {}) or {}
        cev = d.pop("completion_evidence", None) or {}
        fail = d.pop("failure", None) or {}
        attempts = d.get("attempts", 0)
        attempt_count = d.get("attempt_count", attempts)
        return cls(
            task_id=d["task_id"],
            chapter_id=d.get("chapter_id", ""),
            title=d.get("title", ""),
            task_type=d.get("task_type", "video"),
            status=d.get("status", "DISCOVERED"),
            priority=d.get("priority", 0),
            attempt_count=attempt_count,
            attempts=attempt_count,
            consecutive_failures=d.get("consecutive_failures", 0),
            max_attempts=d.get("max_attempts", 3),
            lease=Lease(**lease) if isinstance(lease, dict) else Lease(),
            verification=Verification(**verification) if isinstance(verification, dict)
                        else Verification(),
            completion_evidence=(CompletionEvidence.from_dict(cev)
                                 if isinstance(cev, dict) else CompletionEvidence()),
            failure=FailureRecord.from_dict(fail) if isinstance(fail, dict) else FailureRecord(),
            created_at_utc=d.get("created_at_utc", ""),
            updated_at_utc=d.get("updated_at_utc", ""),
            last_run_id=d.get("last_run_id"),
            last_run_at_utc=d.get("last_run_at_utc"),
            last_started_at=d.get("last_started_at"),
            last_finished_at=d.get("last_finished_at"),
            last_success_at_utc=d.get("last_success_at_utc"),
            last_failure_at_utc=d.get("last_failure_at_utc"),
            _ch_idx=ch_idx,
            _cell_idx=cell_idx,
        )


@dataclass
class ExecutionQueue:
    """可执行任务队列——由 Reconciler 派生。"""
    items: list[dict] = field(default_factory=list)  # [{task_id, priority, state}]
    reconciled_at_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── 文件存储 ───────────────────────────────────────────────────────

# 固定锚定到仓库 state/registry（与 state/course_state.py 的 REPO_ROOT 一致），
# 避免相对 CWD 在任意目录运行脚本时污染仓库。
_REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = _REPO_ROOT / "state" / "registry"


def _queue_file(course_key: str) -> Path:
    """每个课程的队列文件独立，避免多课程互相覆盖。"""
    return Path(TASKS_DIR) / course_key / "execution_queue.json"


def _ensure_dir(course_key: str) -> Path:
    d = Path(TASKS_DIR) / course_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写同目录临时文件再 os.replace，避免半写文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def load_registry(course_key: str) -> dict[str, TaskRecord]:
    f = _ensure_dir(course_key) / "tasks.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {k: TaskRecord.from_dict(v) for k, v in data.items()}
    except Exception:
        return {}


def save_registry(course_key: str, registry: dict[str, TaskRecord]) -> None:
    d = _ensure_dir(course_key)
    text = json.dumps({k: v.to_dict() for k, v in registry.items()},
                      ensure_ascii=False, indent=2)
    _atomic_write_text(d / "tasks.json", text)


def load_queue(course_key: str) -> ExecutionQueue:
    f = _queue_file(course_key)
    if not f.exists():
        return ExecutionQueue()
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return ExecutionQueue(**d)
    except Exception:
        return ExecutionQueue()


def save_queue(course_key: str, q: ExecutionQueue) -> None:
    _atomic_write_text(_queue_file(course_key), json.dumps(
        q.to_dict(), ensure_ascii=False, indent=2))


# ── Queue Reconciliation ───────────────────────────────────────────

# ── Queue Reconciliation ───────────────────────────────────────────

def chapter_tasks(registry: dict[str, TaskRecord], chapter_id: str) -> list[TaskRecord]:
    """本章节的所有任务（task_type 区分）。"""
    return [t for t in registry.values() if t.chapter_id == chapter_id]


def chapter_aggregate_status(registry: dict[str, TaskRecord], chapter_id: str) -> str:
    """E6.2 §7: Chapter status = aggregate(Task statuses)。

    - 没有任何任务 → UNKNOWN
    - 所有任务均 COMPLETED → COMPLETED
    - 存在 RUNNING/VERIFYING → RUNNING
    - 存在 FAILED → FAILED（有未完成任务）
    - 否则（存在 PENDING/DISCOVERED/READY/UNKNOWN/unsupported）→ PENDING

    只有「所有 task 都已完成」才把章节判为 COMPLETED，从而防止
    「video 已完成」单独覆盖整个 chapter（E6.2 §9）。
    """
    recs = chapter_tasks(registry, chapter_id)
    if not recs:
        return "UNKNOWN"
    statuses = {r.status for r in recs}
    if statuses <= {"COMPLETED"}:
        return "COMPLETED"
    if "STALE" in statuses:
        # 有任务被实时状态标记 STALE → 章节不等于完成，需复核
        return "STALE"
    if statuses & {"RUNNING", "VERIFYING"}:
        return "RUNNING"
    if "FAILED" in statuses:
        return "FAILED"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PENDING"


def done_chapter_ids_from_registry(registry: dict[str, TaskRecord]) -> set[str]:
    """从 Registry 派生已完成的章节集合（derived cache，非权威）。

    E6.2：一个 chapter 只有当其「所有 task 都完成」才算 done。
    单有一个 video task 是 COMPLETED 但该章仍有其它 pending/unsupported task，
    不算完成 → 不会因 video 完成而掩盖整个 chapter。

    - SERVER_VERIFIED / RECHECK / UI 证据的 COMPLETED task 才算完成。
    - NONE/空证据 → 不计入。
    绝不从 nextUnit / URL chapterId change / 页面导航推断完成。
    """
    done: set[str] = set()
    chapters_with_task = {t.chapter_id for t in registry.values() if t.chapter_id}
    for cid in chapters_with_task:
        recs = chapter_tasks(registry, cid)
        if not recs:
            continue
        all_done = True
        for t in recs:
            if t.status != "COMPLETED":
                all_done = False
                break
            lvl = (t.completion_evidence.type
                   if t.completion_evidence and t.completion_evidence.type != "NONE"
                   else t.verification.level)
            if lvl not in ("SERVER_VERIFIED", "RECHECK", "UI"):
                all_done = False
                break
        if all_done:
            done.add(cid)
    return done


def reconcile_queue(course_key: str, registry: dict[str, TaskRecord],
                    done_chapter_ids: set[str] | None = None) -> ExecutionQueue:
    """从 Registry（+可选 done 集合）重算 Execution Queue。

    Registry 是权威状态；done_chapter_ids 是 derived cache：
      - 未显式传入时，自动从 registry 内 COMPLETED+证据 推导。
      - COMPLETED(有证据) → 不进入 READY
      - PENDING / DISCOVERED / READY → 进入 READY
      - UNKNOWN → 不自动执行（除非 policy 提升）
      - RUNNING → 根据 lease 判断（过期则视为失败/可重试）
      - FAILED → 未达阈值可重试进 READY；已达阈值跳过
      - BLOCKED → 不执行
    """
    if done_chapter_ids is None:
        done_chapter_ids = done_chapter_ids_from_registry(registry)

    ready: list[TaskRecord] = []

    def _lease_expired(t: TaskRecord) -> bool:
        exp = t.lease.expires_at_utc
        if not exp:
            return True
        try:
            return datetime.fromisoformat(exp) <= datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return True

    for t in registry.values():
        # E6.2: Queue 只接受真正的可执行 task（video）。
        # 非 video 任务（quiz/discussion/other/unsupported）当前不被 video runtime 支持，
        # 不进入队列（避免 runtime 误认为可学视频）。
        if (t.task_type or "video") != "video":
            continue
        # 已完成（有证据）→ 跳过
        if t.status == "COMPLETED" and done_chapter_ids and t.chapter_id in done_chapter_ids:
            continue
        if t.status == "COMPLETED":
            continue
        # BLOCKED → 不执行
        if t.status == "BLOCKED":
            continue
        if t.status == "UNKNOWN":
            # 不自动执行；除非有显式重新发现的 pending 证据
            continue
        # FAILED → 根据 retry policy
        if t.status == "FAILED":
            if t.consecutive_failures >= t.max_attempts:
                continue
            ready.append(t)
            continue
        # RUNNING / VERIFYING → lease 过期可重新入队，否则跳过
        if t.status in ("RUNNING", "VERIFYING"):
            if not _lease_expired(t):
                continue
            ready.append(t)
            continue
        # DISCOVERED / PENDING / READY 等其它可执行态
        if t.is_executable:
            ready.append(t)

    # 排序：有 chapter_id 优先，再按目录顺序
    ready.sort(key=lambda t: (
        0 if t.chapter_id else 1,
        t._ch_idx, t._cell_idx, t.task_id,
    ))

    items = []
    for i, t in enumerate(ready):
        items.append({
            "task_id": t.task_id,
            "chapter_id": t.chapter_id or "",
            "priority": i,
            "state": "READY" if t.status != "FAILED" else "RETRY",
            "course_key": course_key,
        })

    q = ExecutionQueue(
        items=items,
        reconciled_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    save_queue(course_key, q)
    return q


def pick_next_task(queue: ExecutionQueue) -> Optional[TaskRecord]:
    """从队列中选第一个任务。返回 registry 中的 TaskRecord 对象。"""
    if not queue.items:
        return None
    first = queue.items[0]
    reg = load_registry(first.get("course_key", ""))
    return reg.get(first["task_id"])
