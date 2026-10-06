"""failure_class_policy / select_chapters_to_read —— 2026-10-06 排查定案的纯函数单测。

背景（exe 用户报障「一直下一章、视频不放」）：
  1. session kicked / Heartbeat dead 等环境级根因曾被 _derive_failure_stage 遮蔽成
     VIDEO_NOT_COMPLETED 之类"内容味"标签，计入章节 cf → 整门课逐章 BLOCKED，
     一个系统级故障伪装成几十个章节级故障。
  2. 每轮全量深读所有未完成章（29 章 ~6min），用户眼里就是浏览器一直翻章不播放。
"""
import pytest
from datetime import datetime, timezone

from app.registry.reconcile import failure_class_policy, ENV_FAILURE_STAGES
from tvdp.tdvp import (select_chapters_to_read, chapter_video_work_state,
                       run_windowed_deep_read)


class TestFailureClassPolicy:
    @pytest.mark.parametrize("stage", sorted(ENV_FAILURE_STAGES))
    def test_env_stages_classified_env(self, stage):
        assert failure_class_policy(stage) == "ENV", (
            f"{stage} 必须归入 ENV —— 计入章节 cf 会把系统级故障伪装成章节级 BLOCKED")

    @pytest.mark.parametrize("stage,verdict", [
        ("SESSION_KICKED", "FAIL(session kicked during playback)"),
        (None, "CRASH"),
        ("", "CRASH"),
        ("", "FAIL(session kicked during login)"),
        ("", "FAIL(login failed in GHA)"),
    ])
    def test_verdict_fallback_env(self, stage, verdict):
        """引擎旧格式/崩溃路径没有 stage 时，verdict 兜底也要能识别环境类。"""
        assert failure_class_policy(stage, verdict) == "ENV"

    @pytest.mark.parametrize("stage", [
        "PLAYBACK_STALLED", "PLAYBACK_NOT_STARTED", "VIDEO_NOT_COMPLETED",
        "VIDEO_DURATION_INVALID", "ISPASSED_FALSE", "NO_NEXTUNIT_NO_ENDED",
        "UNKNOWN", "",
    ])
    def test_content_stages_classified_chapter(self, stage):
        """内容级失败才是章节 cf / BLOCKED 的合法来路。"""
        assert failure_class_policy(stage, "FAIL — passed=0/10") == "CHAPTER"

    def test_missing_stage_and_benign_verdict_is_chapter(self):
        """stage 缺失且 verdict 无环境特征 → 保守按章节失败（旧行为）。"""
        assert failure_class_policy(None, "FAIL — passed=3/10(obs)") == "CHAPTER"

    def test_phantom_stage_is_neither_env_nor_chapter(self):
        assert failure_class_policy("TARGET_NOT_ON_PAGE") == "PHANTOM"

    def test_stage_match_is_case_insensitive_and_trimmed(self):
        assert failure_class_policy(" session_kicked ") == "ENV"


def _ch(cid, status="incomplete"):
    return {"chapter_id": cid, "status": status}


class TestSelectChaptersToRead:
    """候选队列：目录未完成章按目录序、target 最前、skip 剔除已证明章。

    窗口槽位/回填/读取预算由 run_windowed_deep_read 负责 —— 没有跨轮记忆时，
    「视频已看完/无视频点」的章会永久占槽，窗口停在原地不前移。
    """

    def test_orders_target_first_then_catalog_order(self):
        chapters = [_ch(f"c{i}") for i in range(5)]
        out = select_chapters_to_read(chapters, target_cid="c4")
        assert [c["chapter_id"] for c in out] == ["c4", "c0", "c1", "c2", "c3"]

    def test_completed_chapters_never_enter_queue(self):
        chapters = [_ch("c0", "completed"), _ch("c1"),
                    _ch("c2", "completed"), _ch("c3")]
        out = select_chapters_to_read(chapters)
        assert [c["chapter_id"] for c in out] == ["c1", "c3"]

    def test_target_completed_is_not_forced(self):
        chapters = [_ch("c1"), _ch("c2")]
        out = select_chapters_to_read(chapters, target_cid="c9")
        assert [c["chapter_id"] for c in out] == ["c1", "c2"]

    def test_skip_removes_proven_no_work_chapters(self):
        chapters = [_ch("c0"), _ch("c1"), _ch("c2")]
        out = select_chapters_to_read(chapters, skip=lambda cid: cid == "c1")
        assert [c["chapter_id"] for c in out] == ["c0", "c2"]

    def test_skip_can_remove_target_too(self):
        chapters = [_ch("c0"), _ch("c1")]
        out = select_chapters_to_read(chapters, target_cid="c0",
                                      skip=lambda cid: cid == "c0")
        assert [c["chapter_id"] for c in out] == ["c1"]

    def test_skipper_exception_treated_as_not_skipped(self):
        def bad(cid):
            raise RuntimeError("boom")
        chapters = [_ch("c0"), _ch("c1")]
        out = select_chapters_to_read(chapters, skip=bad)
        assert len(out) == 2, "跳过器自身故障 → 宁可多读不可漏读"

    def test_empty_catalog(self):
        assert select_chapters_to_read([], target_cid="c1") == []
        assert select_chapters_to_read(None) == []

    def test_chapter_without_id_skipped(self):
        chapters = [{"chapter_id": "", "status": "incomplete"}, _ch("c1")]
        out = select_chapters_to_read(chapters)
        assert [c["chapter_id"] for c in out] == ["c1"]


class TestChapterVideoWorkState:
    def test_unfinished_video_is_work(self):
        assert chapter_video_work_state(
            [{"type": "video", "isFinished": False}]) == "work"

    def test_all_videos_finished_is_done(self):
        assert chapter_video_work_state(
            [{"type": "video", "isFinished": True}] * 2) == "done"

    def test_only_non_video_points_is_done(self):
        assert chapter_video_work_state(
            [{"type": "quiz", "isFinished": False}]) == "done"

    def test_mixed_finished_and_unfinished_is_work(self):
        assert chapter_video_work_state(
            [{"type": "video", "isFinished": True},
             {"type": "video", "isFinished": False}]) == "work"

    def test_empty_read_is_unknown(self):
        assert chapter_video_work_state([]) == "unknown"


class TestRunWindowedDeepRead:
    """回填循环：工作章占槽（limit），无工作章回填，读取封顶 2K，无视频章记忆。"""

    @staticmethod
    def _ch(cid):
        return {"chapter_id": cid, "status": "incomplete"}

    @staticmethod
    def _read(results: dict):
        return lambda ch: results[ch["chapter_id"]]

    def test_work_chapters_occupy_slots_and_stop_at_limit(self):
        vid = [{"task_id": "c", "type": "video", "isFinished": False}]
        results = {f"c{i}": (vid, 1) for i in range(1, 5)}
        cands = [self._ch(f"c{i}") for i in range(1, 5)]
        pts, no_video = run_windowed_deep_read(
            cands, limit=2, read_fn=self._read(results))
        assert no_video == []
        assert pts == vid * 2, "槽满即停,后续章下一轮再读"

    def test_no_work_chapter_refills_and_marks(self):
        quiz_only = [{"task_id": "c1:quiz", "type": "quiz", "isFinished": False}]
        empty_frame = ([], 1)          # 帧在而无任务点行（如 PPP 章 1217304719 实测）
        vid = [{"task_id": "c3", "type": "video", "isFinished": False}]
        results = {"c1": (quiz_only, 1), "c2": empty_frame, "c3": (vid, 1)}
        cands = [self._ch("c1"), self._ch("c2"), self._ch("c3")]
        pts, no_video = run_windowed_deep_read(
            cands, limit=2, read_fn=self._read(results))
        assert no_video == ["c1", "c2"], "无视频点章要跨轮记忆(has_video=False)"
        assert pts == quiz_only + vid
        # c1/c2 回填不占槽,c3 占窗口槽;槽未满且队列尽 → 本轮收工

    def test_all_finished_videos_refill_without_no_video_mark(self):
        all_finished = [{"task_id": "c1", "type": "video", "isFinished": True}]
        vid = [{"task_id": "c2", "type": "video", "isFinished": False}]
        results = {"c1": (all_finished, 1), "c2": (vid, 1)}
        cands = [self._ch("c1"), self._ch("c2")]
        pts, no_video = run_windowed_deep_read(
            cands, limit=1, read_fn=self._read(results))
        assert no_video == [], "全 finished 章由 materialize 记账" \
                               "(has_video=True finished=total),不落 no_video"
        assert pts == all_finished + vid

    def test_empty_read_without_frame_occupies_slot(self):
        results = {"c1": ([], 0), "c2": ([], 0)}
        cands = [self._ch("c1"), self._ch("c2")]
        pts, no_video = run_windowed_deep_read(
            cands, limit=1, read_fn=self._read(results))
        assert no_video == [], "帧没挂载 = 读取失败,不能宣布『无视频』"
        assert pts == []

    def test_budget_caps_refill(self):
        quiz_only = [{"task_id": "x", "type": "quiz", "isFinished": False}]
        results = {f"c{i}": (quiz_only, 1) for i in range(1, 7)}
        cands = [self._ch(f"c{i}") for i in range(1, 7)]
        pts, no_video = run_windowed_deep_read(
            cands, limit=2, read_fn=self._read(results))
        assert len(no_video) == 4, "读取封顶 2*limit=4,回填不能变成变相全量"
        assert len(pts) == 4

    def test_legacy_full_read_when_limit_none(self):
        quiz_only = [{"task_id": "x", "type": "quiz", "isFinished": False}]
        results = {f"c{i}": (quiz_only, 1) for i in range(1, 4)}
        cands = [self._ch(f"c{i}") for i in range(1, 4)]
        pts, no_video = run_windowed_deep_read(
            cands, limit=None, read_fn=self._read(results))
        assert no_video == [], "旧行为（全量）不涉及回填记忆"
        assert len(pts) == 3


class TestNoVideoWorkSkipper:
    """跨轮跳过器：24h 内的点级快照证明「无视频工作」→ 跳过深读。"""

    def _snap(self, **kw):
        base = {"video_total": 0, "video_finished": 0, "has_video": False,
                "updated_at": datetime.now(timezone.utc).isoformat()}
        base.update(kw)
        return base

    def _skip(self, monkeypatch, snaps):
        from scheduler import scheduler as sched
        monkeypatch.setattr("app.registry.task_registry.load_chapter_points",
                            lambda key: snaps)
        return sched._make_no_video_work_skipper("k")

    def test_skips_fresh_all_finished(self, monkeypatch):
        skip = self._skip(monkeypatch, {"c1": self._snap(
            has_video=True, video_total=3, video_finished=3)})
        assert skip("c1") is True

    def test_does_not_skip_unfinished_videos(self, monkeypatch):
        skip = self._skip(monkeypatch, {"c1": self._snap(
            has_video=True, video_total=3, video_finished=2)})
        assert skip("c1") is False, "还有未看完的视频点,必须读"

    def test_skips_fresh_no_video_chapter(self, monkeypatch):
        skip = self._skip(monkeypatch, {"c1": self._snap(has_video=False)})
        assert skip("c1") is True, "读过且帧在而无视频点的章,不再占槽"

    def test_stale_snapshot_not_skipped(self, monkeypatch):
        stale = self._snap(has_video=False)
        stale["updated_at"] = "2026-01-01T00:00:00+00:00"
        skip = self._skip(monkeypatch, {"c1": stale})
        assert skip("c1") is False, "过期快照不可信(课程内容可能已变)"

    def test_missing_snapshot_not_skipped(self, monkeypatch):
        skip = self._skip(monkeypatch, {})
        assert skip("c1") is False

    def test_failure_safe(self, monkeypatch):
        def boom(key):
            raise RuntimeError("boom")
        monkeypatch.setattr("app.registry.task_registry.load_chapter_points", boom)
        skip = self._skip(monkeypatch, {})
        assert skip("c1") is False, "跳过器故障绝不阻塞窗口"
