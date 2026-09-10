"""Regression — P1-12 / 3D：progress.completed 必须由 registry 的 done 章驱动推进（0→N）。

事故（ACTION_HISTORY_AUDIT / AGENT_RULE R-1b）：62 runs 全绿但 `progress.completed` 恒 0。
根因是**会计断**：runtime 从不把「已完成章」同步进 `course_state.progress.completed` ——
唯一写入者 `sync_progress_to_course_state` 只在测试被调用，运行时完全不更新（Audit §2）。

本地 registry（L3）明明显示某章已全部 COMPLETED（SERVER_VERIFIED 证据），
但一次真实 `run_scheduler` 之后 `progress.completed` 仍停留旧值（不推进）。

Expected invariant（L5 / Progress Outcome）：
  run_scheduler 结束后，若 registry 中存在 done 章（`done_chapter_ids_from_registry` 非空），
  `course_state.progress.completed` 必须等于 done 章数（由 registry 派生，单一真源）。
  它只对「本地账」负责，**不冒充**服务器接受点（见 PROGRESS_OUTCOME_DATAFLOW.md）。

纪律：保持 scheduler 主循环 / registry / course_state 真实；只 mock 叶子 `_run_tdvp_probe` 与
`_run_one_chapter`（真实 watchdog 已由 P0-01/P0-07 用 fake app 覆盖）。
"""
from unittest.mock import patch

import pytest

from state.course_state import (
    CourseState, CourseIdentity, CourseProgress, initialize_course,
    load_course_state, save_course_state,
)

CH_DONE = "1217304706"
KEY = "265997861_151695658"


def _done_chapter(chapter_id: str, title: str = "物理层要点"):
    """构造一个「全部 task COMPLETED(SERVER_VERIFIED)」的 chapter 任务集合。"""
    from app.registry.task_registry import (TaskRecord, CompletionEvidence, Verification)
    return TaskRecord(
        task_id=f"{chapter_id}_v1", chapter_id=chapter_id, title=title,
        task_type="video", status="COMPLETED", priority=0,
        completion_evidence=CompletionEvidence(
            type="SERVER_VERIFIED", source="nextUnit", run_id="r1",
            detail="server verified", observed_at_utc="2026-01-01T00:00:00Z"),
        verification=Verification(level="SERVER_VERIFIED",
                                  verified_at_utc="2026-01-01T00:00:00Z",
                                  run_id="r1", source_detail="server verified"),
    )


@pytest.fixture
def act(monkeypatch, tmp_path):
    from app.registry import task_registry as tr
    with patch("state.course_state.STATE_DIR", tmp_path / "state"), \
         patch("state.course_state.COURSES_DIR", tmp_path / "state" / "courses"), \
         patch("state.course_state.ACTIVE_FILE", tmp_path / "state" / "active_course.json"), \
         patch.object(tr, "TASKS_DIR", tmp_path / "state" / "registry"):
        identity = CourseIdentity(
            course_id="265997861", clazz_id="151695658", cpi="506830460",
            title="计算机网络", raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
        )
        initialize_course(identity)
        save_course_state(CourseState(
            course_identity=identity, status="ACTIVE",
            progress=CourseProgress(completed=0, total=None,
                                    last_completed_task=None, active_task=None)))
        yield identity


def test_progress_completed_reflects_done_chapters_after_run(act, monkeypatch, tmp_path):
    """reproducer：本地 registry 已有 done 章，跑完调度后 progress.completed 必须反映它。

    修复前：运行时从不把 done 章同步进 `progress.completed` → 恒 0 → 本测试红。
    修复后：run_scheduler 结束时按 registry 派生并写回 progress → 绿。
    """
    from scheduler import scheduler as sched
    from app.registry.task_registry import (save_registry, load_registry,
                                  done_chapter_ids_from_registry)

    # 1. 种子 registry：一章的 task 全部 COMPLETED(SERVER_VERIFIED) == 恰一个 done 章
    registry = {f"{CH_DONE}_v1": _done_chapter(CH_DONE)}
    save_registry(KEY, registry)
    assert done_chapter_ids_from_registry(load_registry(KEY)) == {CH_DONE}

    # 2. 空转调度器（真实主循环 + registry/queue 真实；只 mock 两个叶子）
    def fake_probe(course_url, course_key, run_id="local", exclude_chapters=None):
        return None  # 无新候选 → 本轮不新选章（无推进生产事故的常发面）

    def fake_run_one(*a, **k):
        return {"passed": True, "verdict": "PASS", "failure_stage": None,
                "exit_code": 0, "timing_s": 0.5, "timed_out": False}

    monkeypatch.setattr(sched, "_run_tdvp_probe", fake_probe)
    monkeypatch.setattr(sched, "_run_one_chapter", fake_run_one)
    out = sched.run_scheduler(course_url="", trigger="manual", run_id="r3d", max_chapters=1)

    # 3. 关键不变式：即使本轮【没有】新通过章，L5 progress.completed 也必须反映 registry done 章
    state = load_course_state(KEY)
    assert state.progress is not None
    expected = len(done_chapter_ids_from_registry(load_registry(KEY)))
    assert state.progress.completed == expected, (
        f"L5 计量断：registry 已有 done 章({CH_DONE}) 但 progress.completed={state.progress.completed}；"
        f"运行时没把完成度同步进 course_state（期望 {expected}）")
    assert state.progress.completed > 0