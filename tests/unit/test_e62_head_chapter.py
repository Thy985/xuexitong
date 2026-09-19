"""E6.2 live 复核的目标章 id 必须是章号，且"无视频"判定只能建立在真测到点上。

回归背景（预存在缺陷，非本轮引入）：
  head_cid = str(existing.get(head["task_id"]) or next(...))
existing 是 {task_id: TaskRecord}，`.get()` 返回的是 **TaskRecord 对象**，
`str()` 得到整条 repr。于是 live_verify_chapter 拿一个 repr 当 knowledge_id 去查
→ 必然 0 个视频点 → `total_v == 0` 命中"该章无视频"分支 → 把真实视频章永久降级为
task_type=other/PENDING，并从 video 队列里剔除。每次 scheduler run 掉一章：
origin/main 已含 18 章、HEAD 22 章、工作区再 +3（754/755/756）。
日志实证：`[scheduler] DIAG E6.2 head=TaskRecord(task_id='1217304754', ...`。
"""

import pytest

from scheduler.scheduler import head_chapter_id, points_prove_no_video


def _rec(task_id, chapter_id):
    class R:
        pass

    r = R()
    r.task_id = task_id
    r.chapter_id = chapter_id
    return r


# ── head_chapter_id ────────────────────────────────────────────────

def test_uses_queue_item_chapter_id():
    head = {"task_id": "1217304754", "chapter_id": "1217304754"}
    assert head_chapter_id(head, {}, []) == "1217304754"


def test_never_returns_record_repr():
    """旧实现的失效形状：registry 命中 → str(TaskRecord)。"""
    head = {"task_id": "1217304754", "chapter_id": ""}
    registry = {"1217304754": _rec("1217304754", "1217304754")}
    cid = head_chapter_id(head, registry, [])
    assert cid == "1217304754"
    assert "TaskRecord" not in cid and "chapter_id=" not in cid


def test_falls_back_to_task_list():
    head = {"task_id": "1217304755:x", "chapter_id": ""}
    tasks = [_rec("1217304755:x", "1217304755")]
    assert head_chapter_id(head, {}, tasks) == "1217304755"


def test_empty_head_yields_empty_string():
    assert head_chapter_id({}, {}, []) == ""
    assert head_chapter_id(None, {}, []) == ""


@pytest.mark.parametrize("head", [
    {"task_id": "1", "chapter_id": "1217304754"},
    {"task_id": "1", "chapter_id": ""},
])
def test_result_is_a_bare_chapter_id(head):
    registry = {"1": _rec("1", "1217304754")}
    cid = head_chapter_id(head, registry, [_rec("1", "1217304754")])
    assert cid.isdigit(), f"章号必须是纯数字串，实际 {cid!r}"


# ── points_prove_no_video ─────────────────────────────────────────

def test_no_points_means_unmeasured_not_video_free():
    """空点集 = 没测到（探测失败/传错章号），绝不能当作"该章无视频"。"""
    assert points_prove_no_video([]) is False
    assert points_prove_no_video(None) is False


def test_text_only_chapter_proves_no_video():
    pts = [{"type": "job"}, {"type": "attachment"}, {"type": "read"}]
    assert points_prove_no_video(pts) is True


def test_chapter_with_video_is_not_demoted():
    pts = [{"type": "job"}, {"type": "video", "isFinished": False}]
    assert points_prove_no_video(pts) is False
