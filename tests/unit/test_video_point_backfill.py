"""点级快照要喂给 discovery，让缺失的 `:videoN` 记录被补齐。

第 3 轮 M0 后实测（`diag_video_points_ledger --repair-points`，点级真源）：
  1217304730 快照 video_total=2 / finished=1，registry 里却只有 1 条视频记录；
  1217304738、1217304741 是 3 个视频点、finished 1，也只有 1 条记录。
即**第 2、3 个视频点在账上根本不存在**。主 reconcile 走的是
`build_tasks_from_discovery(chapters_raw)` —— 不传 video_counts，于是每章恒 1 个视频点；
只有 E6.2 对当轮队首章传过一次 counts（1217304708 因此才有 `:video2`）。

后果：章级快照说"还有视频点没做完"，但没有承载它的记录 → 只能反复重播第 1 点。
所以补齐兄弟记录才是 D1 的正解，前面的"兄弟承载"判据要等它落地才真正生效。

**2026-09-22 补注**：上面那批 9/20 读数本身已被实测推翻 —— 今天对 `1217304730` 三路（引擎枚举 /
cards DOM 标记 / 服务端 live 点读）都是 **1** 个视频点、对 `1217304738` 都是 **2** 个，而快照写着
2 和 3（D12/D13 那类错计数被固化进了缓存）。因此"用快照补兄弟记录"必须**只在快照新鲜时**成立，
见 `video_counts_from_points` 的时效判据与 `tests/unit/test_points_snapshot_ttl.py`。本文件的 fixture
因此一律带 `updated_at`（生产写入口 `set_chapter_point_snapshot` 每次都盖时间戳）。
"""
from datetime import datetime, timezone

from app.registry.reconcile import reconcile_registry
from app.registry.task_registry import TaskRecord, video_counts_from_points
from tvdp.tdvp import build_tasks_from_discovery

CID = "1217304730"


def _snap(total, finished=1, cid=CID, has_video=True):
    return {cid: {"video_total": total, "video_finished": finished,
                  "has_video": has_video,
                  "updated_at": datetime.now(timezone.utc).isoformat()}}


# ── video_counts_from_points ─────────────────────────────────────

def test_snapshot_total_becomes_discovery_count():
    assert video_counts_from_points(_snap(2)) == {CID: 2}


def test_chapters_without_video_are_omitted_not_zeroed():
    """没有视频点的章不能进 counts：进去等于宣布"该章 0 个视频"，会被当非视频章降级。"""
    assert video_counts_from_points(_snap(0, has_video=False)) == {}
    assert video_counts_from_points(_snap(0)) == {}


def test_unread_chapters_are_omitted():
    points = {**_snap(3, finished=3, cid="1217304700"), "1217304701": {}}
    assert video_counts_from_points(points) == {"1217304700": 3}


def test_empty_and_none_input():
    assert video_counts_from_points({}) == {}
    assert video_counts_from_points(None) == {}


# ── 补齐链路：counts → discovery → reconcile ──────────────────────

def _chapters():
    return [{"chapter_id": CID, "title": "信道复用技术", "status": "pending",
             "text": "", "chapter_index": 5, "cell_index": 5, "job_remaining": 1}]


def test_discovery_emits_one_task_per_video_point():
    tasks = build_tasks_from_discovery(_chapters(), video_counts={CID: 3})
    vids = sorted(t.task_id for t in tasks if t.task_type == "video")
    assert vids == sorted([CID, f"{CID}:video2", f"{CID}:video3"]), vids


def test_reconcile_adds_missing_siblings_and_keeps_finished_point():
    """补记录时不得把已完成的第 1 点重写成待办 —— 那等于把学过的抹掉。"""
    first = TaskRecord(CID, CID, "信道复用技术", task_type="video", status="COMPLETED")
    first.verification.level = "SERVER_VERIFIED"
    first.completion_evidence.passed_object_ids = ["96bf782cc04a753ee2efe6785431f8b2"]
    existing = {CID: first}
    tasks = build_tasks_from_discovery(_chapters(), video_counts={CID: 2})
    merged, _report = reconcile_registry("k", existing, tasks, {})

    assert CID in merged and f"{CID}:video2" in merged, sorted(merged)
    assert merged[CID].status == "COMPLETED", "第 1 点的已完成态必须留住"
    assert merged[f"{CID}:video2"].task_type == "video"
    assert merged[f"{CID}:video2"].status != "COMPLETED", "新兄弟点应是待办而非已完成"


def test_probe_wires_snapshot_counts_into_discovery(monkeypatch):
    """上面两条只证明"库会用 counts"；这条钉住主 reconcile **确实传了** counts。

    没有它，把 scheduler 里那行接线删掉也能全绿 —— 而那正是 730/738/741 缺兄弟记录的原因。
    """
    import app.registry.task_registry as R
    import tvdp.tdvp as T
    from scheduler.scheduler import _run_tdvp_probe

    seen = {}

    def spy(chapters_raw, fallback_chapter="", video_counts=None):
        seen["video_counts"] = video_counts
        return []

    monkeypatch.setattr(T, "build_tasks_from_discovery", spy)
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {"chapters": _chapters(), "points": []})
    monkeypatch.setattr(T, "live_verify_chapter", lambda *a, **kw: None)
    monkeypatch.setattr(R, "load_chapter_points", lambda key: _snap(2))
    monkeypatch.setattr(R, "load_registry", lambda key: {})
    monkeypatch.setattr(R, "save_registry", lambda key, reg: None)
    monkeypatch.setattr(R, "save_queue", lambda key, q: None)

    _run_tdvp_probe(f"https://mooc1.chaoxing.com/mycourse/studentstudy"
                    f"?chapterId={CID}&courseId=1&clazzid=2&cpi=3", "k")

    assert seen.get("video_counts") == {CID: 2}, (
        f"主 reconcile 必须把点级快照换算成的 counts 交给 discovery，实际 {seen!r}")


def test_corrected_target_cid_skips_completed_chapter():
    """回归（issue #4 第三层卡点）：URL 锚定章已完成时，深读目标改到第一个未完成章。

    active_course.raw_url 沿用旧 initialize 的 chapterId（可能已完成）。若仍读它的点，
    video 标记落空 → queue 空 → scheduler 报 "No pending task"。校正必须让它落到
    真正待推进的章上。
    """
    from tvdp.tdvp import _corrected_target_cid
    chapters = [
        {"chapter_id": "1217304706", "title": "已完成", "status": "completed"},
        {"chapter_id": "1217304719", "title": "点对点协议PPP", "status": "pending"},
        {"chapter_id": "1217304721", "title": "广播信道", "status": "pending"},
    ]
    assert _corrected_target_cid("1217304706", chapters) == "1217304719"
    # target 未完成 → 原样返回
    assert _corrected_target_cid("1217304719", chapters) == "1217304719"
    # 无目录 / 空 target → 原样
    assert _corrected_target_cid("", chapters) == ""
    assert _corrected_target_cid("1217304706", []) == "1217304706"
    # 全部已完成 → 回退原 target（无未完成章可读）
    all_done = [{"chapter_id": "1217304706", "title": "x", "status": "completed"}]
    assert _corrected_target_cid("1217304706", all_done) == "1217304706"


def test_probe_persists_combined_points_as_snapshot(monkeypatch):
    """回归（issue #4 第五层卡点）：probe 拿到校正后的 combined.points 必须落成
    点级快照，否则 discovery 重建拿不到 video_counts → 该章走 other → queue 空 →
    "No pending task / probe empty"。退化账存在时也必须能自愈。
    """
    import os
    import app.registry.task_registry as R
    import tvdp.tdvp as T
    from scheduler.scheduler import _run_tdvp_probe, _PROBE_FETCH_CACHE
    from app.registry.task_registry import TaskRecord, save_registry

    _PROBE_FETCH_CACHE.clear()
    # 种一个退化账（唯一 video 是已完成章 1217304706，1217304719 是 other PENDING）
    save_registry("k", {
        "1217304706": TaskRecord(task_id="1217304706", chapter_id="1217304706",
                                 title="c", task_type="video", status="COMPLETED"),
        "1217304719:other": TaskRecord(task_id="1217304719:other",
                                       chapter_id="1217304719", title="t",
                                       task_type="other", status="PENDING"),
    })
    monkeypatch.setenv("CX_USER", "u")
    monkeypatch.setattr(T, "fetch_course_detail_and_verify",
                        lambda url, cid="", **kw: {
                            "chapters": [
                                {"chapter_id": "1217304706", "title": "已完成",
                                 "status": "completed", "job_remaining": 0},
                                {"chapter_id": "1217304719", "title": "PPP",
                                 "status": "pending", "job_remaining": 1},
                            ],
                            "points": [{"task_id": "1217304719", "type": "video",
                                        "isFinished": False, "titleText": "PPP"}],
                        })
    monkeypatch.setattr(T, "live_verify_chapter", lambda *a, **kw: None)

    nxt = _run_tdvp_probe(
        "https://mooc1.chaoxing.com/mycourse/studentstudy"
        "?chapterId=1217304719&courseId=1&clazzid=2&cpi=3", "k")

    assert nxt == "1217304719", f"probe 必须推进到第一个未完成章，实际 {nxt!r}"
    # 点级快照必须已写入（退化账自愈的证据）
    snap = R.load_chapter_points("k")
    assert snap.get("1217304719", {}).get("has_video") is True, \
        f"probe 必须把 combined.points 落成快照，实际 {snap!r}"
