# D12：read_chapter_job_points 的去重键 `marker|text[:20]` 吞掉空文本的兄弟视频点。
#
# 真站诊断（2026-09-21，章 1217304708）：两个视频任务点的 item.innerText 同为**空**、
# icon marker 完全相同 → JS 去重键碰撞 → 第二个点在读数前就被丢 → live 报
# total=1，`:video2` 的服务端真源恢复（§4.9 方案1）永远命不中。
# 修法：每行带上点的稳定身份 objectid（.ans-insertvideo-online[objectid]），
# 去重下沉为纯函数 job_rows_to_points —— 有 objectid 的行按身份去重
# （同一点被 item 与内嵌 icon 双访问 → 同 oid 折叠；两个不同点 → 不折叠）。

from tvdp.tdvp import build_live_finished, job_rows_to_points

VID_MARKER = "ans-job-icon ans-job-video ans-job-icon-clear "


def _row(oid=None, typ="video", finished=True, text="", marker=VID_MARKER):
    return {"marker": marker, "type": typ, "isFinished": finished,
            "titleText": text, "objectid": oid}


def test_two_empty_text_video_points_both_survive():
    # 708 实况：两行 marker/文本全同，只有 objectid 能区分
    rows = [_row(oid="19da22cc8a80"), _row(oid="53d6b1122d40")]
    pts = job_rows_to_points(rows, "1217304708")
    assert [p["task_id"] for p in pts] == ["1217304708", "1217304708:video2"]
    assert [p["type"] for p in pts] == ["video", "video"]


def test_same_point_visited_twice_collapses_by_objectid():
    # 旧去重要保住的能力：item 与其内嵌 icon 各命中一次 → 同 oid 折叠为一点
    rows = [_row(oid="19da22cc8a80"), _row(oid="19da22cc8a80")]
    pts = job_rows_to_points(rows, "1217304708")
    assert len(pts) == 1
    assert pts[0]["task_id"] == "1217304708"


def test_rows_without_objectid_keep_legacy_text_dedupe():
    rows = [_row(), _row()]                      # 无 oid、文本同 → 折叠
    pts = job_rows_to_points(rows, "1217304708")
    assert len(pts) == 1


def test_non_video_points_get_type_suffixed_task_ids():
    rows = [_row(oid="a1", typ="video", finished=False),
            _row(typ="homework", finished=False, text="本章任务",
                 marker="ans-job-icon ans-homework ")]
    pts = job_rows_to_points(rows, "1217304708")
    assert pts[0]["task_id"] == "1217304708"
    assert pts[1]["task_id"] == "1217304708:homework"


def test_live_finished_now_sees_the_second_point():
    # §4.9 恢复路径的端到端（纯函数层）：708 两点都 finished →
    # live_finished 必须同时含 <cid> 和 <cid>:video2
    rows = [_row(oid="19da22cc8a80"), _row(oid="53d6b1122d40")]
    pts = job_rows_to_points(rows, "1217304708")
    assert build_live_finished(pts) == {"1217304708", "1217304708:video2"}


# D13（2026-09-21 真站章 1217304738）：cards 帧里存在一个**裸** `.ans-job-icon`
# —— 无类型 class、无 objectid、无文本，其 item 启发式仍把 type 判成 video。
# job_rows_to_points 于是给它 mint 出 `1217304738:video3`：一个没有媒体身份、
# 永远不可能完成的幻影任务（实测它停在 DISCOVERED，而该章 DOM 只有 2 个视频模块）。
# 视频点的稳定身份就是 oid —— 无 oid 不成点，也不该占视频点序号
# （否则 chapter_video_summary 计数虚高，投递交割全部跟着错）。
def test_video_row_without_objectid_creates_no_phantom_point():
    rows = [
        _row(oid="94382be48a99", finished=True),
        _row(oid="e79a9a86eba1", finished=False),
        _row(oid="", marker="ans-job-icon ", text=""),
    ]
    pts = job_rows_to_points(rows, "1217304738")
    assert [p["task_id"] for p in pts] == ["1217304738", "1217304738:video2"]
    assert build_live_finished(pts) == {"1217304738"}


def test_non_video_point_without_objectid_still_survives():
    # 只掐"无 oid 的视频点"；作业/文档等本就没有 oid，不能被这条规则误杀
    rows = [_row(oid="", typ="homework", marker="ans-job-icon ans-job-work ", text="达标测试")]
    pts = job_rows_to_points(rows, "1217304738")
    assert [p["task_id"] for p in pts] == ["1217304738:homework"]
