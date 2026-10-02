"""E6.2 复核必须把「服务端已判 finished」的点喂给 reconcile（D14 治愈入口）。

回归（run 36984897107，issue #4 第七个卡点）：E6.2 的 reconcile 只传 live_pending、
不传 live_finished。整章早已看完的候选（1217304741，进页 0% 服务端就回
isPassed=true、E6.2 读数 video_total=2 / finished=2）治不好，UNKNOWN+rollback
优先规则把它重新投出去 —— 白看 9.5 分钟已完成的视频。

两条腿都要接：combined（目录会话顺带读到的点级）与独立复核（live_verify_chapter）。
"""

import pytest

import app.registry.task_registry as R
import tvdp.tdvp as T
from app.registry.task_registry import TaskRecord
from scheduler.scheduler import _PROBE_FETCH_CACHE, _run_tdvp_probe
from scheduler.scheduler import combined_verify_from_points


def _verified_video(tid, cid, title, status="COMPLETED"):
    r = TaskRecord(tid, cid, title, task_type="video", status=status)
    r.verification.level = "SERVER_VERIFIED"
    r.completion_evidence.type = "SERVER_VERIFIED"
    r.completion_evidence.source = "isPassed"
    r.completion_evidence.passed_object_ids = [f"obj-{tid}"]
    return r


@pytest.fixture
def mem_registry(monkeypatch):
    """load/save_registry、queue、点级快照全部走内存，测试互不污染。"""
    store: dict = {}
    points: dict = {}
    monkeypatch.setattr(R, "load_registry", lambda key: dict(store))
    monkeypatch.setattr(R, "save_registry", lambda key, reg: store.update(reg))
    monkeypatch.setattr(R, "save_queue", lambda key, q: None)
    monkeypatch.setattr(R, "load_chapter_points", lambda key: dict(points))
    monkeypatch.setattr(R, "save_chapter_points",
                        lambda key, pts: (points.clear(), points.update(pts)))
    return store


# ── combined_verify_from_points（纯函数）────────────────────────────

def test_combined_verify_carries_live_finished():
    pts = [
        {"task_id": "1217304741", "type": "video", "isFinished": True, "titleText": "a"},
        {"task_id": "1217304741:video2", "type": "video", "isFinished": True,
         "titleText": "b"},
    ]
    v = combined_verify_from_points(pts, "1217304741")
    assert v["video_total"] == 2 and v["video_finished"] == 2
    assert v["live_finished"] == {"1217304741", "1217304741:video2"}
    assert v["live_pending"] == set()
    assert v["points"] == pts   # 过滤后返回新列表（多章点级需按章过滤），内容一致


def test_combined_verify_rejects_points_from_another_chapter():
    """深读目标被 _corrected_target_cid 校正后，点级可能不属于队首章 —— 拿别章的
    点数当本章快照会把 has_video 写错、冻错章。必须返回 None 交回独立复核。"""
    pts = [{"task_id": "1217304719", "type": "video", "isFinished": False}]
    assert combined_verify_from_points(pts, "1217304741") is None
    assert combined_verify_from_points(pts, "1217304719")["video_total"] == 1
    assert combined_verify_from_points([], "1217304719") is None
    assert combined_verify_from_points(None, "1217304719") is None


# ── probe 级接线（combined 腿）──────────────────────────────────────

def test_probe_heals_fully_finished_chapter_instead_of_rewatching(mem_registry,
                                                                  monkeypatch):
    """741 全章已看完（2/2 finished）：probe 必须经 live_finished 治愈它并返回
    None，而不是把它重新投出去重看。"""
    CID = "1217304741"
    plain = _verified_video(CID, CID, "OSPF协议", status="UNKNOWN")
    plain.mark_rollback()
    plain.mark_rollback()                        # rb=2：rollback 优先规则本来会投它
    plain.completion_evidence.type = "CONFLICT"  # 曾被章级粗读数打下来（保留 passed ids）
    plain.completion_evidence.detail = "chapter has unfinished points"
    mem_registry.update({
        CID: plain,
        f"{CID}:video2": _verified_video(f"{CID}:video2", CID, "OSPF协议"),
        f"{CID}:other": TaskRecord(f"{CID}:other", CID, "OSPF协议",
                                   task_type="other", status="PENDING"),
    })
    _PROBE_FETCH_CACHE.clear()

    def _no_live_verify(*a, **kw):
        raise AssertionError("同会话已读到该章点级时不得再开独立复核浏览器")

    monkeypatch.setattr(T, "live_verify_chapter", _no_live_verify)
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {
                            "chapters": [{"chapter_id": CID, "title": "OSPF协议",
                                          "status": "pending", "job_remaining": 0}],
                            "points": [
                                {"task_id": CID, "type": "video",
                                 "isFinished": True, "titleText": "OSPF"},
                                {"task_id": f"{CID}:video2", "type": "video",
                                 "isFinished": True, "titleText": "OSPF-2"},
                            ]})

    nxt = _run_tdvp_probe(
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        f"?chapterId={CID}&courseId=1&clazzid=2&cpi=3", "k")

    assert nxt is None, f"整章早已看完，probe 不得再选它重看，实际选中 {nxt!r}"
    fixed = mem_registry[CID]
    assert fixed.status == "COMPLETED", "live_finished 必须治愈 UNKNOWN 记录"
    assert fixed.verification.level == "SERVER_VERIFIED"


# ── probe 级接线（独立复核腿）───────────────────────────────────────

def test_probe_wires_live_finished_from_independent_verify(mem_registry,
                                                           monkeypatch):
    """combined 无点级时走 live_verify_chapter：它返回的 live_finished 同样必须
    接进 reconcile —— 否则修复只覆盖 combined 一条腿。"""
    A, B = "1217304706", "1217304719"
    mem_registry.update({
        A: _verified_video(A, A, "已完成章"),
        B: TaskRecord(B, B, "点对点协议PPP", task_type="video", status="PENDING"),
        f"{B}:other": TaskRecord(f"{B}:other", B, "点对点协议PPP",
                                 task_type="other", status="PENDING"),
    })
    _PROBE_FETCH_CACHE.clear()

    calls = []

    def _fake_live_verify(cid, *a, **kw):
        calls.append(cid)
        return {
            "video_total": 1, "video_finished": 1,
            "live_pending": set(), "live_finished": {B},
            "points": [{"task_id": B, "type": "video", "isFinished": True,
                        "titleText": "PPP"}],
        }

    monkeypatch.setattr(T, "live_verify_chapter", _fake_live_verify)
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {
                            "chapters": [
                                {"chapter_id": A, "title": "已完成章",
                                 "status": "completed", "job_remaining": 0},
                                {"chapter_id": B, "title": "点对点协议PPP",
                                 "status": "pending", "job_remaining": 1},
                            ],
                            "points": [],   # 目录会话没读到点级 → 走独立复核
                        })

    nxt = _run_tdvp_probe(
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        f"?chapterId={B}&courseId=1&clazzid=2&cpi=3", "k")

    assert calls == [B], f"独立复核必须发生在队首章上，实际 {calls!r}"
    assert nxt is None, "该点服务端已判 finished，治愈后队列应空"
    assert mem_registry[B].status == "COMPLETED"
    assert mem_registry[B].verification.level == "SERVER_VERIFIED"
