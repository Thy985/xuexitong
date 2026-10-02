"""多章点级深读 → 铸造 heal → 队列只装真活（2026-10-02 排查定案的回归网）。

run 36993138621 NOOP 的完整因果：单章深读锚死在 719（PPT 章，无视频点）→
video_counts 全空 → 队列 0 READY。全课程扫描证实剩余视频工作 = 10 点 / 6 章
（721×4、740、743、771×2、773、775），但台账里这些章没有任何 video 记录。

修复链路（本文件逐环钉住）：
  1. fetch 深读全部 DOM 未完成章（形状不变：平铺 points，task_id 前缀分章）；
  2. reconcile 铸造时消费 live_finished（bootstrap 早就传了、mint 路径从未消费）
     —— 已 finished 的点铸造即 COMPLETED，不再盲目重投；
  3. healed 记录（无 oid、source="live job points"）获得 point_is_server_verified
     保护，不被章级粗读数打回 STALE 重投；
  4. combined_verify 按章过滤，多章点级不合计进单章快照；
  5. 端到端：probe 一轮点亮全部章，队列只剩真正未完成的视频点。
"""

import pytest

import app.registry.task_registry as R
import tvdp.tdvp as T
from app.registry.reconcile import reconcile_registry
from app.registry.task_registry import (
    TaskRecord, done_chapter_ids_from_registry, reconcile_queue,
)
from scheduler.scheduler import _PROBE_FETCH_CACHE, _run_tdvp_probe
from scheduler.scheduler import combined_verify_from_points
from tvdp.tdvp import TaskEvidence, TaskInfo


def _video_info(tid, cid, title="T"):
    return TaskInfo(tid, cid, title, "video", "PENDING", "UI", "x",
                    TaskEvidence("PENDING", "UI", ""))


def _verified_video(tid, cid, title):
    r = TaskRecord(tid, cid, title, task_type="video", status="COMPLETED")
    r.verification.level = "SERVER_VERIFIED"
    r.completion_evidence.type = "SERVER_VERIFIED"
    r.completion_evidence.source = "isPassed"
    r.completion_evidence.passed_object_ids = [f"obj-{tid}"]
    return r


# ── combined_verify_from_points：多章过滤 ──────────────────────────

def test_combined_verify_filters_to_requested_chapter():
    pts = [
        {"task_id": "1217304721", "type": "video", "isFinished": True},
        {"task_id": "1217304721:video2", "type": "video", "isFinished": False},
        {"task_id": "1217304771", "type": "video", "isFinished": False},
    ]
    v = combined_verify_from_points(pts, "1217304721")
    assert v["video_total"] == 2 and v["video_finished"] == 1, \
        "多章点级必须先过滤再聚合，否则别章点数污染本章快照"
    assert v["live_finished"] == {"1217304721"}
    assert v["live_pending"] == {"1217304721:video2"}
    assert all(p["task_id"].startswith("1217304721") for p in v["points"])
    assert combined_verify_from_points(pts, "1217304799") is None


# ── reconcile 铸造 heal（D14-mint）──────────────────────────────────

def test_mint_heals_server_finished_point():
    fixed, rep = reconcile_registry(
        "k", {}, [_video_info("1217304721", "1217304721", "广播信道")],
        {}, live_finished={"1217304721"})
    rec = fixed["1217304721"]
    assert rec.status == "COMPLETED", "服务端已判 finished 的点铸造即完成"
    assert rec.verification.level == "SERVER_VERIFIED"
    assert rec.point_is_server_verified()
    assert rep.healed_by_server == 1
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    assert "1217304721" not in {i["task_id"] for i in q.items}, \
        "已看完的点不得进队列（站点不为已完成点起流）"


def test_mint_keeps_unfinished_point_executable():
    fixed, rep = reconcile_registry(
        "k", {}, [_video_info("1217304721:video2", "1217304721", "广播信道")],
        {}, live_finished={"1217304721"})
    assert fixed["1217304721:video2"].status == "DISCOVERED"
    assert rep.healed_by_server == 0
    q = reconcile_queue("k", fixed, done_chapter_ids_from_registry(fixed))
    assert "1217304721:video2" in {i["task_id"] for i in q.items}


# ── healed 记录（无 oid）的点级保护 ─────────────────────────────────

def test_healed_point_without_objectid_is_still_server_verified():
    r = TaskRecord("x", "x", "t")
    r.verification.level = "SERVER_VERIFIED"
    r.completion_evidence.type = "SERVER_VERIFIED"
    r.completion_evidence.source = "live job points"
    assert r.point_is_server_verified(), \
        "live 点级 heal 无 oid 也算点级确认，否则被章级粗读数打回 STALE 重投"
    r.completion_evidence.source = "isPassed"      # 无 oid 的 isPassed 不算
    assert not r.point_is_server_verified()


# ── read_chapter_job_points 读空诊断 ────────────────────────────────

class _FakeFrame:
    def __init__(self, url, fail_selector=False):
        self.url = url
        self._fail = fail_selector

    def wait_for_selector(self, *a, **k):
        if self._fail:
            raise TimeoutError("selector timeout")

    def evaluate(self, *a, **k):
        return []


class _FakePage:
    def __init__(self, frames):
        self.frames = frames

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass


def test_point_read_logs_diagnostics_on_empty(capsys):
    pts = T.read_chapter_job_points(_FakePage([]), "1217304719", "c", "z", "p")
    assert pts == []
    err = capsys.readouterr().err
    assert "point-read 1217304719" in err and "pts=0" in err
    assert "cards_frames=0" in err, "帧都没挂载要能看出来"


def test_point_read_logs_cards_frame_without_markers(capsys):
    page = _FakePage([_FakeFrame("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?x=1",
                                 fail_selector=False)])
    pts = T.read_chapter_job_points(page, "1217304719", "c", "z", "p")
    assert pts == []
    err = capsys.readouterr().err
    assert "cards_frames=1" in err, "帧在但没有可识别点行（719 的 PPT 章形状）"


# ── 端到端：probe 一轮点亮全部章，队列只剩真活 ──────────────────────

@pytest.fixture
def probe_env(monkeypatch):
    store: dict = {}
    points: dict = {}
    saved_queues: list = []
    monkeypatch.setattr(R, "load_registry", lambda key: dict(store))
    monkeypatch.setattr(R, "save_registry", lambda key, reg: store.update(reg))
    monkeypatch.setattr(R, "save_queue", lambda key, q: saved_queues.append(q))
    monkeypatch.setattr(R, "load_chapter_points", lambda key: dict(points))
    monkeypatch.setattr(R, "save_chapter_points",
                        lambda key, pts: (points.clear(), points.update(pts)))
    return store, points, saved_queues


def test_probe_lights_all_pending_chapters_and_queues_real_work(probe_env,
                                                                monkeypatch):
    store, points, saved_queues = probe_env
    # 台账 = run 36993138621 后的真实形状：只剩已完成 video + :other
    store.update({
        "1217304741": _verified_video("1217304741", "1217304741", "OSPF协议"),
        "1217304741:other": TaskRecord("1217304741:other", "1217304741",
                                       "OSPF协议", task_type="other",
                                       status="PENDING"),
    })
    _PROBE_FETCH_CACHE.clear()

    chapters = [
        {"chapter_id": "1217304741", "title": "OSPF协议", "status": "completed",
         "job_remaining": 1, "chapter_index": 39, "cell_index": 39},
        {"chapter_id": "1217304719", "title": "点对点协议PPP", "status": "pending",
         "job_remaining": 1, "chapter_index": 40, "cell_index": 40},
        {"chapter_id": "1217304721", "title": "广播信道", "status": "pending",
         "job_remaining": 6, "chapter_index": 41, "cell_index": 41},
        {"chapter_id": "1217304771", "title": "万维网WWW", "status": "pending",
         "job_remaining": 3, "chapter_index": 42, "cell_index": 42},
    ]
    points_rows = [
        # 719：PPT 章 —— 读数里没有 video 行（真实扫描结果）
        # 721：5 个视频点，第 1 个服务端已判 finished
        {"task_id": "1217304721", "type": "video", "isFinished": True,
         "objectid": "o1", "marker": "ans-job-icon ans-job-video"},
        {"task_id": "1217304721:video2", "type": "video", "isFinished": False,
         "objectid": "o2", "marker": "ans-job-icon ans-job-video"},
        {"task_id": "1217304721:video3", "type": "video", "isFinished": False,
         "objectid": "o3", "marker": "ans-job-icon ans-job-video"},
        {"task_id": "1217304721:video4", "type": "video", "isFinished": False,
         "objectid": "o4", "marker": "ans-job-icon ans-job-video"},
        {"task_id": "1217304721:video5", "type": "video", "isFinished": False,
         "objectid": "o5", "marker": "ans-job-icon ans-job-video"},
        # 771：2 个视频点均未完成
        {"task_id": "1217304771", "type": "video", "isFinished": False,
         "objectid": "o6", "marker": "ans-job-icon ans-job-video"},
        {"task_id": "1217304771:video2", "type": "video", "isFinished": False,
         "objectid": "o7", "marker": "ans-job-icon ans-job-video"},
    ]
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {"chapters": chapters,
                                                   "points": points_rows})
    monkeypatch.setattr(T, "live_verify_chapter",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("多章读数已覆盖 head 章，不得再开复核")))

    nxt = _run_tdvp_probe(
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        "?chapterId=1217304719&courseId=1&clazzid=2&cpi=3", "k")

    # 1) 快照点亮：有视频点的章全部落快照；719（无视频）不落
    assert points.get("1217304721", {}).get("video_total") == 5
    assert points.get("1217304721", {}).get("video_finished") == 1
    assert points.get("1217304771", {}).get("video_total") == 2
    assert "1217304719" not in points

    # 2) 铸造 heal：已 finished 的点直接 COMPLETED，台账自愈
    assert store["1217304721"].status == "COMPLETED"
    assert store["1217304721"].point_is_server_verified()

    # 3) 无视频章不被铸成幻影 video（719 的历史教训）
    assert "1217304719" not in store or \
        store["1217304719"].task_type != "video" or \
        store["1217304719"].status != "DISCOVERED"

    # 4) 队列只剩真正未完成的视频点：721×4 + 771×2
    qids = [it["task_id"] for it in saved_queues[-1].items]
    assert qids == ["1217304721:video2", "1217304721:video3",
                    "1217304721:video4", "1217304721:video5",
                    "1217304771", "1217304771:video2"], \
        f"队列应只剩 6 个真活，实际 {qids}"

    # 5) probe 选中队首真活
    assert nxt == "1217304721:video2", f"实际选中 {nxt!r}"
