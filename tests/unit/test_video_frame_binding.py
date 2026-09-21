# P1 修复：目标视频点绑定（objectid → 帧）。

# 真站结构（章 1217304708 只读探针，2026-09-21）：cards 帧里每个视频任务点是
# `.ans-insertvideo-online[objectid]`，每个点有独立的 ananas/modules/video/index.html
# 帧且 `<video>.src` 含该点的 objectid。旧 `get_video_state` 无绑定 —— 帧遍历顺序
# 决定观测目标：投 `1217304708:video2` 时读到的是点 1 的帧（ct=655/655），
# 方案A 用点 1 的进度/时长满足护栏 → 目标点 2（ct=0）被误判完成。
# 绑定语义：观测、续播、时长探测、完成判定只认「src 含目标 objectid」的那一帧。

from app.e2_headed_gha import (
    bind_video_state,
    next_unit_decision,
    pick_target_objectid,
)


def _probe(src: str, ct: float = 0.0) -> dict:
    return {"currentTime": ct, "duration": None, "paused": True,
            "readyState": 0, "playbackRate": 1, "ended": False, "src": src}


# ── pick_target_objectid：第 N 个视频点 ↔ objectid ──────────────────

def test_nth_video_point_resolves_to_its_objectid():
    ids = ["19da22ccaaaa", "53d6b112bbbb"]
    assert pick_target_objectid(ids, 2) == "53d6b112bbbb"
    assert pick_target_objectid(ids, 1) == "19da22ccaaaa"


def test_natural_whole_chapter_dispatch_binds_nothing():
    # target_vi=0 = 自然连播整章，沿用旧（无绑定）行为
    assert pick_target_objectid(["aa", "bb"], 0) is None


def test_out_of_range_target_resolves_to_nothing():
    assert pick_target_objectid(["aa"], 2) is None
    assert pick_target_objectid([], 1) is None


# ── bind_video_state：在多帧探针结果里按 objectid 选中目标帧 ─────────

def test_bound_frame_is_the_one_whose_src_carries_target_objectid():
    probes = [_probe("https://x/ananas/19da22ccaaaa/enc.mp4?k=1", ct=655),
              _probe("https://x/ananas/53d6b112bbbb/enc.mp4?k=2", ct=0)]
    st = bind_video_state(probes, "53d6b112bbbb")
    assert st["found"] is True
    assert st["frame"] == "bound"
    assert st["src"] == "https://x/ananas/53d6b112bbbb/enc.mp4?k=2"
    assert st["currentTime"] == 0


def test_unresolvable_target_is_reported_not_faked_with_another_frame():
    probes = [_probe("https://x/ananas/19da22ccaaaa/enc.mp4")]
    st = bind_video_state(probes, "53d6b112bbbb")
    assert st["found"] is False
    assert st["reason"] == "target_frame_not_found"
    assert st["target_objectid"] == "53d6b112bbbb"


def test_no_target_objectid_means_no_binding_decision():
    # 绑定关闭时返回 None，调用方回退到旧的帧遍历顺序
    probes = [_probe("https://x/ananas/19da22ccaaaa/enc.mp4")]
    assert bind_video_state(probes, "") is None
    assert bind_video_state(probes, None) is None


def test_probe_without_src_never_matches():
    probes = [{"currentTime": 0, "duration": None, "src": ""},
              {"currentTime": 0, "duration": None, "src": None}]
    st = bind_video_state(probes, "53d6b112bbbb")
    assert st["found"] is False
    assert st["reason"] == "target_frame_not_found"


# ── 方案A 护栏：绑定帧自身零进度时不得判完成 ─────────────────────────

def test_plan_a_with_zero_progress_on_bound_frame_is_not_completion():
    # P1 实况：isPassed 来自点 1、initial_duration=656 也是点 1 的，
    # 绑定帧（点 2）max_ct=0 → 只能判「切换」，绝不判完成
    assert next_unit_decision(True, True, 0.0, ended_seen=False,
                              initial_duration=656.0,
                              bound_max_ct=0.0) == "exit_switch"


def test_plan_a_with_real_progress_on_bound_frame_still_completes():
    assert next_unit_decision(True, True, 120.0, ended_seen=False,
                              initial_duration=656.0,
                              bound_max_ct=120.0) == "exit_complete"


def test_plan_a_guard_is_off_when_no_binding_is_active():
    # bound_max_ct 缺省 None → 旧语义完全不变（向后兼容回归锚点）
    assert next_unit_decision(True, True, 0.0, ended_seen=False,
                              initial_duration=656.0) == "exit_complete"
