"""failure_class_policy / select_chapters_to_read —— 2026-10-06 排查定案的纯函数单测。

背景（exe 用户报障「一直下一章、视频不放」）：
  1. session kicked / Heartbeat dead 等环境级根因曾被 _derive_failure_stage 遮蔽成
     VIDEO_NOT_COMPLETED 之类"内容味"标签，计入章节 cf → 整门课逐章 BLOCKED，
     一个系统级故障伪装成几十个章节级故障。
  2. 每轮全量深读所有未完成章（29 章 ~6min），用户眼里就是浏览器一直翻章不播放。
"""
import pytest

from app.registry.reconcile import failure_class_policy, ENV_FAILURE_STAGES
from tvdp.tdvp import select_chapters_to_read


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
    """Top-K 深读窗口：预测队首必读 + 目录序滑窗；窗口外章不会死锁（随进度前移）。"""

    def test_default_window_is_three_with_target_first(self):
        chapters = [_ch(f"c{i}") for i in range(5)]
        out = select_chapters_to_read(chapters, target_cid="c4", limit=3)
        assert [c["chapter_id"] for c in out] == ["c4", "c0", "c1"], (
            "队首预测章必读且排最前,其余按目录序补足 K 章")

    def test_completed_chapters_never_read(self):
        chapters = [_ch("c0", "completed"), _ch("c1"), _ch("c2", "completed"),
                    _ch("c3")]
        out = select_chapters_to_read(chapters, target_cid="", limit=3)
        assert [c["chapter_id"] for c in out] == ["c1", "c3"]

    def test_target_completed_is_not_forced(self):
        chapters = [_ch("c1"), _ch("c2")]
        out = select_chapters_to_read(chapters, target_cid="c9", limit=3)
        assert [c["chapter_id"] for c in out] == ["c1", "c2"]

    def test_none_limit_reads_all_legacy(self):
        chapters = [_ch(f"c{i}") for i in range(6)]
        out = select_chapters_to_read(chapters, target_cid="", limit=None)
        assert len(out) == 6

    def test_window_slides_forward(self):
        """前 2 章完成后,窗口自然前移到后续章 —— 未读章不会永远选不到。"""
        chapters = [_ch("c0", "completed"), _ch("c1", "completed"),
                    _ch("c2"), _ch("c3"), _ch("c4")]
        out = select_chapters_to_read(chapters, target_cid="", limit=3)
        assert [c["chapter_id"] for c in out] == ["c2", "c3", "c4"]

    def test_empty_catalog(self):
        assert select_chapters_to_read([], target_cid="c1", limit=3) == []
        assert select_chapters_to_read(None, target_cid="", limit=3) == []

    def test_single_chapter_course(self):
        out = select_chapters_to_read([_ch("only")], target_cid="only", limit=3)
        assert [c["chapter_id"] for c in out] == ["only"]

    def test_chapter_without_id_skipped(self):
        chapters = [{"chapter_id": "", "status": "incomplete"}, _ch("c1")]
        out = select_chapters_to_read(chapters, target_cid="", limit=3)
        assert [c["chapter_id"] for c in out] == ["c1"]
