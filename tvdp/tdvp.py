"""TDVP — Task Discovery & Verification Protocol

E7: 两阶段探针协议（Passive Probe + Active Probe）
-----------------------------------------------
1. Passive Probe（低成本）：从 studentstudy 页面解析章节/任务列表和 UI 完成标记
2. Active Probe（高成本）：对 pending/unknown 任务调用真实 Runtime 验证

不修改现有 Browser Runtime，不实现自动连续执行，不实现 Scheduler。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

# ── 类型别名 ───────────────────────────────────────────────────────
TaskStatus = Literal["COMPLETED", "PENDING", "UNKNOWN"]
ProbeSource = Literal["UI", "SERVER_VERIFIED", "UNKNOWN"]
TaskType = Literal["video", "quiz", "discussion", "other"]


# ── 数据模型 ───────────────────────────────────────────────────────

@dataclass
class TaskEvidence:
    """单个任务点的证据记录。"""
    status: TaskStatus
    confidence: ProbeSource
    source_detail: str
    observed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskInfo:
    """一个任务点的完整信息。"""
    task_id: str
    chapter_id: str
    title: str
    task_type: TaskType
    status: TaskStatus
    confidence: ProbeSource
    source_detail: str
    evidence: TaskEvidence
    discovered_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def key(self) -> str:
        return self.task_id

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "chapter_id": self.chapter_id,
            "title": self.title,
            "task_type": self.task_type,
            "status": self.status,
            "confidence": self.confidence,
            "source_detail": self.source_detail,
            "evidence": self.evidence.to_dict(),
            "discovered_at_utc": self.discovered_at_utc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskInfo":
        ev = d.pop("evidence", None)
        if ev is None:
            ev = TaskEvidence(
                status=d.get("status", "UNKNOWN"),
                confidence=d.get("confidence", "UNKNOWN"),
                source_detail=d.get("source_detail", ""),
            ).to_dict()
        return cls(
            task_id=d["task_id"],
            chapter_id=d.get("chapter_id", d["task_id"]),
            title=d.get("title", ""),
            task_type=d.get("task_type", "other"),
            status=d.get("status", "UNKNOWN"),
            confidence=d.get("confidence", "UNKNOWN"),
            source_detail=d.get("source_detail", ""),
            evidence=TaskEvidence(**ev) if isinstance(ev, dict) else TaskEvidence("UNKNOWN", "UNKNOWN", ""),
            discovered_at_utc=d.get("discovered_at_utc", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class ChapterInfo:
    """一个章节的信息。"""
    chapter_id: str
    title: str
    tasks: list[TaskInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "task_count": len(self.tasks),
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass
class CourseDiscovery:
    """一门课程的完整探测结果。"""
    course_id: str
    clazz_id: str
    course_key: str
    chapters: list[ChapterInfo]
    discovered_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def all_tasks(self) -> list[TaskInfo]:
        tasks = []
        for ch in self.chapters:
            tasks.extend(ch.tasks)
        return tasks

    @property
    def completed_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "COMPLETED"]

    @property
    def pending_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "PENDING"]

    @property
    def unknown_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "UNKNOWN"]

    def to_dict(self) -> dict:
        return {
            "course_key": self.course_key,
            "course_id": self.course_id,
            "clazz_id": self.clazz_id,
            "chapter_count": len(self.chapters),
            "task_count": len(self.all_tasks),
            "completed_count": len(self.completed_tasks),
            "pending_count": len(self.pending_tasks),
            "unknown_count": len(self.unknown_tasks),
            "chapters": [c.to_dict() for c in self.chapters],
            "discovered_at_utc": self.discovered_at_utc,
        }


# ── Passive Probe ──────────────────────────────────────────────────

def parse_task_status_from_page(html: str, chapter_id: str) -> list[TaskInfo]:
    """从 studentstudy 页面 HTML 解析任务列表。

    UI 标记规则（学习通）：
      - 数字 0 = 未完成 (pending)
      - 数字 1+ = 已完成 (completed)
      - 无数字标记 = unknown

    返回：该章节下的所有任务点列表。
    """
    tasks = []

    # 模式1: 小节标题 + 后面的数字（如 "1.6 计算机网络的体系结构" + "1"）
    pattern_title = re.compile(
        r'([\d]+(?:\.[\d]+)?)\s+([\u4e00-\u9fff][\u4e00-\u9fff\s\w]{1,30})',
        re.UNICODE
    )
    # 找紧跟每个标题后面的数字标记
    matches = list(pattern_title.finditer(html))

    for m in matches:
        num = m.group(1)
        title = m.group(2)
        # 在这个匹配之后的 HTML 片段中找状态数字
        start = html.find(num)
        if start == -1:
            continue
        snippet = html[start:start+300]

        # 找紧跟标题的 >数字< 模式（学习通 UI 标记）
        digit_match = re.search(r'>\s*(\d)\s*<', snippet)
        if digit_match:
            val = int(digit_match.group(1))
            if val > 0:
                status, confidence, detail = "COMPLETED", "UI", f"UI marker={val}"
            else:
                status, confidence, detail = "PENDING", "UI", "UI marker=0"
        else:
            status, confidence, detail = "UNKNOWN", "UI", "no status marker found"

        task_id = f"{chapter_id}_{num.replace('.', '_')}"
        tasks.append(TaskInfo(
            task_id=task_id,
            chapter_id=chapter_id,
            title=title.strip(),
            task_type="video",
            status=status,
            confidence=confidence,
            source_detail=detail,
            evidence=TaskEvidence(status, confidence, detail),
        ))

    return tasks


# ── Evidence Aggregator ────────────────────────────────────────────

def aggregate_evidence(
    passive_results: dict[str, TaskInfo],
    active_results: Optional[dict[str, TaskInfo]] = None,
) -> dict[str, TaskInfo]:
    """合并被动探测和主动验证结果，确定 canonical task state。

    规则：
      - SERVER_VERIFIED > UI
      - COMPLETED + SERVER_VERIFIED 是最强证据
      - 如果被动说 PENDING(UI)，主动成功（COMPLETED），升级为 COMPLETED(SERVER_VERIFIED)
    """
    merged = {}

    for task_id, task in passive_results.items():
        merged[task_id] = task

    if active_results:
        for task_id, active_task in active_results.items():
            if task_id not in merged:
                merged[task_id] = active_task
                continue

            existing = merged[task_id]
            if active_task.confidence == "SERVER_VERIFIED":
                merged[task_id] = active_task
            elif active_task.status == "COMPLETED" and existing.status == "PENDING":
                merged[task_id] = TaskInfo(
                    task_id=task_id,
                    chapter_id=active_task.chapter_id,
                    title=active_task.title,
                    task_type=active_task.task_type,
                    status="COMPLETED",
                    confidence="SERVER_VERIFIED",
                    source_detail=f"active_probe_verified({existing.source_detail})",
                    evidence=TaskEvidence("COMPLETED", "SERVER_VERIFIED",
                                          f"active_probe_verified({existing.source_detail})"),
                )

    return merged


# ── Task Registry ──────────────────────────────────────────────────

TASKS_FILE = Path(__file__).parent.parent / "state" / "tdvp_tasks.json"


def load_task_registry(course_key: str) -> dict[str, TaskInfo]:
    """从 state/tdvp_tasks.json 加载已注册的任务。"""
    if not TASKS_FILE.exists():
        return {}
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        return {k: TaskInfo.from_dict(v) for k, v in data.get(course_key, {}).items()}
    except Exception:
        return {}


def save_task_registry(course_key: str, tasks: dict[str, TaskInfo]) -> None:
    """保存任务注册表到 state/tdvp_tasks.json。"""
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8")) if TASKS_FILE.exists() else {}
    except Exception:
        data = {}
    data[course_key] = {k: v.to_dict() for k, v in tasks.items()}
    tmp = TASKS_FILE.with_suffix(TASKS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TASKS_FILE)


def get_pending_tasks(course_key: str, registry: Optional[dict[str, TaskInfo]] = None) -> list[TaskInfo]:
    """获取待验证任务列表（PENDING 或 UNKNOWN）。"""
    if registry is None:
        registry = load_task_registry(course_key)
    return sorted(
        [t for t in registry.values() if t.status in ("PENDING", "UNKNOWN")],
        key=lambda t: t.task_id,
    )


def get_completed_tasks(course_key: str, registry: Optional[dict[str, TaskInfo]] = None) -> list[TaskInfo]:
    """获取已完成任务列表。"""
    if registry is None:
        registry = load_task_registry(course_key)
    return [t for t in registry.values() if t.status == "COMPLETED"]


# ── Progress Synchronization ──────────────────────────────────────

def sync_progress_to_course_state(
    course_key: str,
    discovered: CourseDiscovery,
) -> dict:
    """将发现结果同步到 course_state.json 的 task_queue。"""
    from state.course_state import load_course_state, save_course_state, CourseProgress

    state = load_course_state(course_key)
    if not state:
        return {"error": f"No course state for {course_key}"}

    all_tasks = discovered.all_tasks
    completed = discovered.completed_tasks
    pending = discovered.pending_tasks
    unknown = discovered.unknown_tasks

    state.progress = CourseProgress(
        completed=len(completed),
        total=len(all_tasks),
        last_completed_task=completed[-1].task_id if completed else None,
        active_task=pending[0].task_id if pending else None,
    )

    # 构建 task_queue
    task_queue = [t.task_id for t in sorted(pending + unknown, key=lambda t: t.task_id)]

    # 保存 discovery 历史
    if not hasattr(state, 'discoveries'):
        state.discoveries = []
    state.discoveries.append({
        "discovered_at_utc": discovered.discovered_at_utc,
        "total_tasks": len(all_tasks),
        "completed": len(completed),
        "pending": len(pending),
        "unknown": len(unknown),
    })

    save_course_state(state)
    return {
        "course_key": course_key,
        "total": len(all_tasks),
        "completed": len(completed),
        "pending": len(pending),
        "unknown": len(unknown),
        "task_queue": task_queue,
        "next_task": task_queue[0] if task_queue else None,
    }


# ── Active Probe ──────────────────────────────────────────────────

def run_active_probe(
    course_url: str,
    task_id: str,
    chapter_id: Optional[str] = None,
    output: str = "./evidence/probe_result.json",
) -> Optional[TaskInfo]:
    """执行 Active Probe：调用真实 Runtime 验证单个任务。"""
    cmd = [
        sys.executable, "app/run.py",
        "--action", "run",
        "--course-url", course_url,
        "--chapter-id", task_id,
        "--output", output,
    ]

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
        timing = time.time() - t0

        if proc.returncode == 0 and Path(output).exists():
            with open(output) as f:
                result = json.load(f)
            res = result.get("result", {})
            passed = res.get("exit_code", 1) == 0

            return TaskInfo(
                task_id=task_id,
                chapter_id=chapter_id or task_id,
                title=result.get("title", f"Task {task_id}"),
                task_type="video",
                status="COMPLETED" if passed else "PENDING",
                confidence="SERVER_VERIFIED" if passed else "UI",
                source_detail=f"active_probe: exit_code={res.get('exit_code')}, timing={timing:.1f}s",
                evidence=TaskEvidence(
                    "COMPLETED" if passed else "PENDING",
                    "SERVER_VERIFIED" if passed else "UI",
                    f"runtime_{timing:.1f}s",
                ),
            )
    except Exception as e:
        print(f"[active_probe] Error probing {task_id}: {e}", file=sys.stderr)

    return None


# ── Main Entry ─────────────────────────────────────────────────────

def discover_course(
    course_url: str,
    html: str,
    chapter_id: Optional[str] = None,
) -> CourseDiscovery:
    """执行完整发现流程。"""
    from resolvers.course_resolver import _parse_url_params

    params = _parse_url_params(course_url)
    course_id = params.get("course_id", "")
    clazz_id = params.get("clazz_id", "")
    course_key = f"{course_id}_{clazz_id}"

    chapters = {}
    target_chapter = chapter_id or params.get("chapter_id")

    if target_chapter:
        tasks = parse_task_status_from_page(html, target_chapter)
        chapters[target_chapter] = ChapterInfo(
            chapter_id=target_chapter,
            title=f"Chapter {target_chapter}",
            tasks=tasks,
        )
    else:
        # 提取所有章节 ID
        chapter_ids = set()
        if params.get("chapter_id"):
            chapter_ids.add(params["chapter_id"])
        for m in re.finditer(r'chapterId[=:](\d+)', html):
            chapter_ids.add(m.group(1))

        for cid in sorted(chapter_ids):
            tasks = parse_task_status_from_page(html, cid)
            chapters[cid] = ChapterInfo(
                chapter_id=cid,
                title=f"Chapter {cid}",
                tasks=tasks,
            )

    return CourseDiscovery(
        course_id=course_id,
        clazz_id=clazz_id,
        course_key=course_key,
        chapters=list(chapters.values()),
    )


def run_passive_probe(course_url: str, html: str, chapter_id: Optional[str] = None) -> CourseDiscovery:
    """执行 Passive Probe（纯 HTML 解析，不启动浏览器）。"""
    return discover_course(course_url, html, chapter_id=chapter_id)
