"""账本修复要按真源恢复 task_type，并按历史恢复降级前的 status。

降级那一笔同时写了两件事：`task_type: video → other` 和 `status: <原值> → PENDING`。
只恢复 task_type 会把已完成/已服务端核验的章重新排队再播一遍（每章 ~10min 真站成本），
所以降级前的 status 要从"该记录最后一次仍是 video"的历史版本取。
"""

from scripts.diag_video_points_ledger import pick_pre_demotion_state


def _v(**kw):
    return {"task_type": kw.get("task_type"), "status": kw.get("status")}


def test_returns_newest_video_version():
    versions = [_v(task_type="video", status="COMPLETED"),      # 新 → 旧
                _v(task_type="other", status="PENDING")]
    assert pick_pre_demotion_state(versions) == {"task_type": "video",
                                                 "status": "COMPLETED"}


def test_skips_versions_where_record_was_still_other():
    """记录可能更早就是 other（真非视频章）—— 取到的必须是最后一次 video 状态。"""
    versions = [_v(task_type="other", status="PENDING"),
                _v(task_type="video", status="FAILED"),
                _v(task_type="other", status="PENDING")]
    assert pick_pre_demotion_state(versions) == {"task_type": "video",
                                                 "status": "FAILED"}


def test_no_video_version_anywhere_yields_none():
    versions = [_v(task_type="other", status="PENDING"),
                _v(task_type="other", status="PENDING")]
    assert pick_pre_demotion_state(versions) is None


def test_empty_history_yields_none():
    assert pick_pre_demotion_state([]) is None
    assert pick_pre_demotion_state(None) is None


def test_missing_status_is_reported_not_invented():
    assert pick_pre_demotion_state([_v(task_type="video")]) is None
