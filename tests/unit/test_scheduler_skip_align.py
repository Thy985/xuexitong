"""调度器：BLOCKED 跳过 + 章视角对齐（_align_chapter_url）回归。

背景：run 34578071336 反复重试 1217304719（点对点协议PPP，headed-Xvfb 抓不到
<video>），直到 consecutive_failures 才累计跨过 max_attempts 进 BLOCKED。修复：
- _task_is_blocked: 显式把 BLOCKED/达上限任务在真正执行前跳过（换下一候选）。
- _align_chapter_url: 用任务自己的 chapter_id 重建课程 URL，避免沿用 state 里
  旧章 raw_url 的 chapterId 锚点（页面对齐到本轮任务）。
"""
import pytest

from scheduler.scheduler import _task_is_blocked, _align_chapter_url
from app.registry.task_registry import TaskRecord


def _mk(task_id, cid, status="READY", consecutive=0, ma=3, task_type="video"):
    return TaskRecord(task_id=task_id, chapter_id=cid, title="t",
                      task_type=task_type, status=status,
                      consecutive_failures=consecutive, max_attempts=ma)


class TestTaskIsBlocked:
    def test_blocked_status_true(self):
        reg = {"A": _mk("A", "1", status="BLOCKED", consecutive=3, ma=3)}
        assert _task_is_blocked("k", "A", "1", reg) is True

    def test_cap_reached_non_blocked_true(self):
        # FAILED 但 consecutive == max_attempts → 达上限视为不可重跑 → True
        reg = {"B": _mk("B", "2", status="FAILED", consecutive=3, ma=3)}
        assert _task_is_blocked("k", "B", "2", reg) is True

    def test_retryable_failed_false(self):
        reg = {"C": _mk("C", "3", status="FAILED", consecutive=1, ma=3)}
        assert _task_is_blocked("k", "C", "3", reg) is False

    def test_pending_ready_false(self):
        reg = {"D": _mk("D", "4", status="READY", consecutive=0, ma=3)}
        assert _task_is_blocked("k", "D", "4", reg) is False

    def test_by_chapter_not_task_id(self):
        # task_id 缺省匹配不到时，按 chapter_id 兜底定位
        reg = {"X": _mk("X", "99", status="BLOCKED")}
        assert _task_is_blocked("k", "Y", "99", reg) is True

    def test_missing_false(self):
        assert _task_is_blocked("k", "ZZ", "none", {}) is False


class TestAlignChapterUrl:
    BASE = ("https://mooc1.chaoxing.com/mycourse/studentstudy"
            "?chapterId=1217304706&courseId=265997861&clazzid=151695658"
            "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
            "&mooc2=1&hidetype=0")

    def test_anchor_to_target_chapter(self):
        out = _align_chapter_url(self.BASE, "1217304719")
        assert out and "chapterId=1217304719" in out
        # 保持课程/cpi/enc 等参数
        for k in ("courseId=265997861", "clazzid=", "cpi=", "enc="):
            assert k in out

    def test_no_chapter_returns_original(self):
        assert _align_chapter_url(self.BASE, "") == self.BASE

    def test_garbage_falls_back(self):
        # 解析失败的输入 → 原样返回，不抛异常
        out = _align_chapter_url("not-a-url-without-params", "123")
        assert isinstance(out, str) and out