"""P0-12 (cpi identity) + P0-10 (video_total==0 不 other-drop 真视频章)。

── P0-12 ─────────────────────────────────────────────────────────
Failure mode: 课程身份把不同 cpi（同一 course_id+clazz_id 的不同班级/教师实例）混淆，
    或解析路径一处理 cpi、另一处丢 cpi → identity 不稳定/跨班串写。
Expected invariant:
    identity.key() 稳定且与 cpi 无关（course_id_clazz_id），
    但 CourseIdentity 仍**携带 cpi**（供 URL 构造），不得被静默丢弃；
    不同 cpi 的 URL → 同一稳定 key + 各自保留自己的 cpi。
依据：REGRESSION_MATRIX P0-12；HISTORICAL_BUG_CASES §3.5（identity 收敛）。

── P0-10 ─────────────────────────────────────────────────────────
Failure mode: 评估 reads 真实视频章的 job 点把 video_total 读成 0 → 被误判
    “纯文本/非视频章” → 在 scheduler 里 task_type 置 other → 永久踢出队列（漏课）。
Expected: chapter_video_summary 对确实含 video 点的真实 job_points 返回 total>0，
    不会误报 0 —— 这是不 other-drop 的前置不变量。
"""

from models import CourseIdentity

from tvdp.tdvp import chapter_video_summary

CID = "265997861"
CLZ = "151695658"


def _id(cpi: str) -> CourseIdentity:
    return CourseIdentity(
        course_id=CID, clazz_id=CLZ, cpi=cpi, title="t",
        raw_url="", resolved_at_utc="2026-01-01T00:00:00Z",
    )


class TestP12CpiIdentity:
    """身份 key 稳定、且携带 cpi（不混淆不同班级）。"""

    def test_different_cpi_same_key_but_carries_cpi(self):
        a = _id("506830460")
        b = _id("999999999")
        # key 稳定且与 cpi 无关
        assert a.key() == b.key()
        assert a.key() == f"{CID}_{CLZ}"
        # 但 cpi 不被丢弃 —— 各自保留，供 URL 构造/区别班级
        assert a.cpi == "506830460"
        assert b.cpi == "999999999"

    def test_cpi_roundtrips_through_resolve(self):
        # from_url 解析应正确带回 cpi
        from models import CourseParams
        url = ("https://mooc1.chaoxing.com/mycourse/studentstudy?"
               f"chapterId=1217304706&courseId={CID}&clazzid={CLZ}"
               "&cpi=506830460&enc=abc&mooc2=1")
        cp = CourseParams.from_url(url)
        assert cp.cpi == "506830460"


class TestP10VideoSummaryDoesNotMissRealVideos:
    """真实含 video 点的 job_points → video_total>0（不误报 0 → 不 other-drop）。"""

    def test_video_points_yield_positive_total(self):
        pts = [
            {"task_id": "1217304710", "type": "video", "isFinished": False},
            {"task_id": "1217304710", "type": "video", "isFinished": True},
            {"task_id": "1217304710:quiz", "type": "quiz", "isFinished": False},
        ]
        total, finished = chapter_video_summary(pts)
        assert total == 2          # 有 2 个视频点 → 不可能误报 0
        assert finished == 1

    def test_all_finished_counts_and_stays_video(self):
        pts = [{"task_id": "a", "type": "video", "isFinished": True}]
        total, finished = chapter_video_summary(pts)
        assert total == 1 and finished == 1  # 仍是视频章（不因 finished 变非视频）