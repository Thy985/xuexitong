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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Literal
from enum import Enum

from tvdp.tdvp import TaskStatus as TdvpStatus, TaskInfo as TdvpTaskInfo

# ── 类型定义 ───────────────────────────────────────────────────────
TaskPhase = Literal["DISCOVERED", "PENDING", "READY", "RUNNING", "VERIFYING",
                    "COMPLETED", "FAILED", "BLOCKED", "UNKNOWN"]
EvidenceLevel = Literal["NONE", "UI", "SERVER_VERIFIED", "RECHECK"]


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
class TaskRecord:
    """一个任务点的完整记录——真相源。"""
    task_id: str
    chapter_id: str
    title: str
    task_type: str = "video"
    status: TaskPhase = "DISCOVERED"
    priority: int = 0                              # 0 = 目录顺序；越高越优先
    attempts: int = 0
    consecutive_failures: int = 0
    max_attempts: int = 3
    lease: Lease = field(default_factory=Lease)
    verification: Verification = field(default_factory=Verification)
    created_at_utc: str = ""
    updated_at_utc: str = ""
    last_run_at_utc: Optional[str] = None
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
        """任务是否可执行（READY 或需重试的 FAILED）。"""
        return self.status in ("READY", "FAILED")

    @property
    def chapter_id_for_url(self) -> str:
        """返回用于构造 URL 的 chapterId；无则返回空。"""
        return self.chapter_id or ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_ch_idx", None)
        d.pop("_cell_idx", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRecord":
        ch_idx = d.pop("_ch_idx", 0)
        cell_idx = d.pop("_cell_idx", 0)
        lease = d.pop("lease", {}) or {}
        verification = d.pop("verification", {}) or {}
        return cls(
            task_id=d["task_id"],
            chapter_id=d.get("chapter_id", ""),
            title=d.get("title", ""),
            task_type=d.get("task_type", "video"),
            status=d.get("status", "DISCOVERED"),
            priority=d.get("priority", 0),
            attempts=d.get("attempts", 0),
            consecutive_failures=d.get("consecutive_failures", 0),
            max_attempts=d.get("max_attempts", 3),
            lease=Lease(**lease) if isinstance(lease, dict) else Lease(),
            verification=Verification(**verification) if isinstance(verification, dict)
                        else Verification(),
            created_at_utc=d.get("created_at_utc", ""),
            updated_at_utc=d.get("updated_at_utc", ""),
            last_run_at_utc=d.get("last_run_at_utc"),
            last_success_at_utc=d.get("last_success_at_utc"),
            last_failure_at_utc=d.get("last_failure_at_utc"),
            _ch_idx=ch_idx,
            _cell_idx=cell_idx,
        )

    def mark_running(self, run_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.status = "RUNNING"
        self.lease = Lease(run_id=run_id, started_at_utc=now,
                           expires_at_utc=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())
        self.last_run_at_utc = now
        self.updated_at_utc = now

    def mark_completed(self, run_id: str, evidence_level: EvidenceLevel = "SERVER_VERIFIED",
                       detail: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.status = "COMPLETED"
        self.lease = Lease()
        self.verification = Verification(level=evidence_level, verified_at_utc=now,
                                         run_id=run_id, source_detail=detail)
        self.last_success_at_utc = now
        self.updated_at_utc = now

    def mark_failed(self, run_id: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.attempts += 1
        self.consecutive_failures += 1
        self.lease = Lease()
        self.last_failure_at_utc = now
        self.updated_at_utc = now
        if self.consecutive_failures >= self.max_attempts:
            self.status = "BLOCKED"
        else:
            self.status = "FAILED"


@dataclass
class ExecutionQueue:
    """可执行任务队列——由 Reconciler 派生。"""
    items: list[dict] = field(default_factory=list)  # [{task_id, priority, state}]
    reconciled_at_utc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── 文件存储 ───────────────────────────────────────────────────────

TASKS_DIR = Path("state/registry")
QUEUE_FILE = TASKS_DIR / "execution_queue.json"


def _ensure_dir(course_key: str) -> Path:
    d = TASKS_DIR / course_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_registry(course_key: str) -> dict[str, TaskRecord]:
    f = TASKS_DIR / f"{course_key}.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {k: TaskRecord.from_dict(v) for k, v in data.items()}
    except Exception:
        return {}


def save_registry(course_key: str, registry: dict[str, TaskRecord]) -> None:
    d = _ensure_dir(course_key)
    tmp = d / "tasks.json.tmp"
    tmp.write_text(json.dumps({k: v.to_dict() for k, v in registry.items()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "tasks.json").write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
    tmp.unlink(missing_ok=True)


def load_queue(course_key: str) -> ExecutionQueue:
    f = QUEUE_FILE
    if not f.exists():
        return ExecutionQueue()
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return ExecutionQueue(**d)
    except Exception:
        return ExecutionQueue()


def save_queue(course_key: str, q: ExecutionQueue) -> None:
    f = QUEUE_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(q.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    f.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
    tmp.unlink(missing_ok=True)


# ── Queue Reconciliation ───────────────────────────────────────────

def reconcile_queue(course_key: str, registry: dict[str, TaskRecord],
                    done_chapter_ids: set[str]) -> ExecutionQueue:
    """从 Registry + 已完成的章节集合重算 Execution Queue。

    规则：
      - COMPLETED / BLOCKED / VERIFYING / RUNNING 的任务不入队
      - PENDING / UNKNOWN 且 chapter_id 不在 done_ids → READY
      - FAILED 且未达 max_attempts → READY
      - 按 _ch_idx（DOM 目录顺序）+ _cell_idx 排序，保证执行顺序
    """
    ready: list[TaskRecord] = []
    for t in registry.values():
        if t.chapter_id in done_chapter_ids:
            continue
        if t.status == "COMPLETED":
            continue
        if t.status in ("RUNNING", "VERIFYING"):
            continue
        if t.status == "BLOCKED":
            continue
        # PENDING / UNKNOWN / FAILED（可重试）
        if t.status == "FAILED" and t.consecutive_failures >= t.max_attempts:
            continue
        ready.append(t)

    # 按目录顺序排序
    ready.sort(key=lambda t: (t._ch_idx, t._cell_idx, t.task_id))

    items = []
    for i, t in enumerate(ready):
        items.append({
            "task_id": t.task_id,
            "chapter_id": t.chapter_id or "",
            "priority": i,
            "state": "READY",
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
