"""Persistent Course State Management

E5 状态管理层。所有状态持久化到仓库 state/ 目录，通过 Git commit 实现
跨 Run 持久化。

状态文件结构:
    state/
    ├── active_course.json      # 当前活跃课程 identity key
    └── courses/
        ├── <course_id>_<clazz_id>.json   # 每个课程的完整状态
        └── ...

安全要求:
    - 禁止写入 Secrets / Cookie / Session / token
    - 禁止写入完整敏感请求
    - artifact 中不泄露凭据
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

# ── 路径常量 ───────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.resolve()
STATE_DIR = REPO_ROOT / "state"
COURSES_DIR = STATE_DIR / "courses"
ACTIVE_FILE = STATE_DIR / "active_course.json"

# 状态目录默认权限（仅当前用户可读写）
_STATE_DIR_MODE = 0o700
_STATE_FILE_MODE = 0o600


# ── 类型定义 ───────────────────────────────────────────────────────
CourseStatus = Literal[
    "NEW", "ACTIVE", "RUNNING", "PARTIALLY_COMPLETED",
    "COMPLETED", "ARCHIVED", "ERROR", "BLOCKED"
]


@dataclass
class CourseIdentity:
    """课程身份信息（与 resolvers.CourseIdentity 对应但独立，避免循环依赖）。"""
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    raw_url: str
    resolved_at_utc: str

    def key(self) -> str:
        return f"{self.course_id}_{self.clazz_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CourseIdentity":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CourseProgress:
    """课程学习进度。"""
    completed: Optional[int] = None
    total: Optional[int] = None
    last_completed_task: Optional[str] = None  # chapter_id
    active_task: Optional[str] = None  # chapter_id

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CourseProgress":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CourseState:
    """单个课程的完整持久化状态。"""
    schema_version: int = 1
    course_identity: Optional[CourseIdentity] = None
    status: CourseStatus = "NEW"
    progress: Optional[CourseProgress] = None
    last_run: Optional[str] = None           # ISO UTC
    last_success: Optional[str] = None       # ISO UTC
    last_failure: Optional[str] = None       # ISO UTC
    last_completed_task: Optional[str] = None
    active_task: Optional[str] = None
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    history: list[dict] = field(default_factory=list)  # 每次 run 摘要

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "status": self.status,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
        if self.course_identity:
            d["course_identity"] = self.course_identity.to_dict()
        if self.progress:
            d["progress"] = self.progress.to_dict()
        if self.last_run:
            d["last_run"] = self.last_run
        if self.last_success:
            d["last_success"] = self.last_success
        if self.last_failure:
            d["last_failure"] = self.last_failure
        if self.last_completed_task:
            d["last_completed_task"] = self.last_completed_task
        if self.active_task:
            d["active_task"] = self.active_task
        if self.history:
            d["history"] = self.history[-50:]  # 保留最近 50 条
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CourseState":
        ci = d.pop("course_identity", None)
        prog = d.pop("progress", None)
        scheduler = d.pop("scheduler", None)
        state = cls(
            schema_version=d.pop("schema_version", 1),
            course_identity=CourseIdentity.from_dict(ci) if ci else None,
            status=d.pop("status", "NEW"),
            progress=CourseProgress.from_dict(prog) if prog else None,
            last_run=d.pop("last_run", None),
            last_success=d.pop("last_success", None),
            last_failure=d.pop("last_failure", None),
            last_completed_task=d.pop("last_completed_task", None),
            active_task=d.pop("active_task", None),
            run_count=d.pop("run_count", 0),
            success_count=d.pop("success_count", 0),
            failure_count=d.pop("failure_count", 0),
            history=d.pop("history", []),
        )
        if scheduler:
            state.scheduler = scheduler
        return state

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "status": self.status,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
        if self.course_identity:
            d["course_identity"] = self.course_identity.to_dict()
        if self.progress:
            d["progress"] = self.progress.to_dict()
        if self.last_run:
            d["last_run"] = self.last_run
        if self.last_success:
            d["last_success"] = self.last_success
        if self.last_failure:
            d["last_failure"] = self.last_failure
        if self.last_completed_task:
            d["last_completed_task"] = self.last_completed_task
        if self.active_task:
            d["active_task"] = self.active_task
        if self.history:
            d["history"] = self.history[-50:]  # 保留最近 50 条
        # E6: scheduler 字段
        if hasattr(self, 'scheduler') and self.scheduler:
            d["scheduler"] = self.scheduler
        return d


# ── 公共 API ───────────────────────────────────────────────────────

def _ensure_dirs():
    """确保状态目录存在。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COURSES_DIR.mkdir(parents=True, exist_ok=True)


def load_active_course() -> Optional[CourseIdentity]:
    """加载当前活跃课程身份。

    Returns:
        CourseIdentity 或 None（无活跃课程）
    """
    _ensure_dirs()
    if not ACTIVE_FILE.exists():
        return None
    try:
        data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
        key = data.get("active_identity")
        if not key:
            return None
        # 从 key 重建 Identity（不含 title/cpi，需从状态文件补充）
        course_id, clazz_id = key.rsplit("_", 1)
        cs = load_course_state(key)
        if cs and cs.course_identity:
            return cs.course_identity
        # 降级：返回简化版
        return CourseIdentity(
            course_id=course_id, clazz_id=clazz_id,
            cpi="", title=key, raw_url="",
            resolved_at_utc=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        print(f"[state] Error loading active course: {e}", file=sys.stderr)
        return None


def load_course_state(identity_key: str) -> Optional[CourseState]:
    """加载指定课程的状态。

    Args:
        identity_key: 由 CourseIdentity.key() 生成的 key

    Returns:
        CourseState 或 None（状态不存在）
    """
    _ensure_dirs()
    state_file = COURSES_DIR / f"{identity_key}.json"
    if not state_file.exists():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return CourseState.from_dict(data)
    except Exception as e:
        print(f"[state] Error loading state for {identity_key}: {e}",
              file=sys.stderr)
        return None


def save_course_state(state: CourseState) -> None:
    """原子保存课程状态。

    使用临时文件 + rename 确保原子性，避免并发写入破坏状态。
    """
    _ensure_dirs()
    if not state.course_identity:
        raise ValueError("Cannot save state without course_identity")

    key = state.course_identity.key()
    state_file = COURSES_DIR / f"{key}.json"

    # 原子写入：先写临时文件再 rename
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix="course_state_", dir=COURSES_DIR
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, str(state_file))
        state_file.chmod(_STATE_FILE_MODE)
    except Exception:
        # 清理临时文件
        Path(tmp_path).unlink(missing_ok=True)
        raise


def activate_course(identity: CourseIdentity) -> None:
    """设置活跃课程。

    - 更新 active_course.json
    - 将旧活跃课程标记为 ARCHIVED（如果不同）
    - 将新课程状态初始化为 ACTIVE
    """
    _ensure_dirs()

    old_active = load_active_course()
    new_key = identity.key()

    # 写活跃标记
    active_data = {"active_identity": new_key,
                   "activated_at_utc": datetime.now(timezone.utc).isoformat()}
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(active_data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, str(ACTIVE_FILE))
        ACTIVE_FILE.chmod(_STATE_FILE_MODE)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    # 加载或创建新课程状态
    existing = load_course_state(new_key)
    if existing:
        if existing.status == "ARCHIVED":
            # 从归档恢复
            existing.status = "ACTIVE"
            existing.course_identity = identity
            save_course_state(existing)
        # 否则保持现有状态（允许继续之前的学习）
    else:
        # 新建
        now_utc = datetime.now(timezone.utc).isoformat()
        new_state = CourseState(
            schema_version=1,
            course_identity=identity,
            status="ACTIVE",
            progress=CourseProgress(),
            last_run=now_utc,
        )
        save_course_state(new_state)

    # 归档旧课程（如果不同）
    if old_active and old_active.key() != new_key:
        archive_course(old_active)


def archive_course(identity: CourseIdentity) -> None:
    """将课程标记为归档。"""
    _ensure_dirs()
    key = identity.key()
    state = load_course_state(key)
    if state:
        state.status = "ARCHIVED"
        save_course_state(state)


def update_state_after_run(
    state: CourseState,
    passed: bool,
    timing_s: float,
    chapter_id: str,
    verdict: str,
) -> None:
    """根据 run 结果更新课程状态。

    Args:
        state: 当前课程状态
        passed: 本次 run 是否 PASS 10/10
        timing_s: 运行耗时
        chapter_id: 学习的章节 ID
        verdict: 最终判定字符串
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    state.run_count += 1
    state.last_run = now_utc

    if passed:
        state.success_count += 1
        state.last_success = now_utc
        state.last_completed_task = chapter_id
        state.active_task = None  # 任务完成，清除活跃任务
        # 更新进度
        if state.progress:
            state.progress.last_completed_task = chapter_id
            state.progress.active_task = None
    else:
        state.failure_count += 1
        state.last_failure = now_utc
        state.active_task = chapter_id
        if state.progress:
            state.progress.active_task = chapter_id

    # 记录 run 历史摘要
    state.history.append({
        "run_at_utc": now_utc,
        "timing_s": round(timing_s, 1),
        "passed": passed,
        "verdict": verdict,
        "chapter_id": chapter_id,
    })

    # 更新状态
    if passed:
        state.status = "ACTIVE"  # 保持活跃，可能还有更多任务
    elif state.failure_count >= 3:
        state.status = "BLOCKED"  # 连续失败多次

    save_course_state(state)


def get_courses_list() -> list[dict]:
    """列出所有课程状态摘要。"""
    _ensure_dirs()
    result = []
    active = load_active_course()
    active_key = active.key() if active else None

    for f in sorted(COURSES_DIR.glob("*.json")):
        try:
            state = CourseState.from_dict(
                json.loads(f.read_text(encoding="utf-8"))
            )
            result.append({
                "key": state.course_identity.key() if state.course_identity else f.stem,
                "title": state.course_identity.title if state.course_identity else "?",
                "course_id": state.course_identity.course_id if state.course_identity else "?",
                "clazz_id": state.course_identity.clazz_id if state.course_identity else "?",
                "status": state.status,
                "run_count": state.run_count,
                "success_count": state.success_count,
                "failure_count": state.failure_count,
                "last_run": state.last_run,
                "last_success": state.last_success,
                "is_active": f.stem == active_key,
            })
        except Exception as e:
            result.append({"key": f.stem, "error": str(e)})

    return result


# ── 便捷函数 ───────────────────────────────────────────────────────

def initialize_course(identity: CourseIdentity) -> CourseState:
    """初始化新课程。"""
    activate_course(identity)
    return load_course_state(identity.key()) or CourseState(
        schema_version=1, course_identity=identity, status="ACTIVE"
    )


def run_course(identity: CourseIdentity,
               chapter_id: str,
               passed: bool,
               timing_s: float,
               verdict: str) -> CourseState:
    """执行一次课程 run 并更新状态。"""
    state = load_course_state(identity.key())
    if not state:
        state = CourseState(
            schema_version=1, course_identity=identity, status="ACTIVE"
        )
    update_state_after_run(state, passed, timing_s, chapter_id, verdict)
    return state


# 导入 sys/os 用于错误输出
import sys
import os
