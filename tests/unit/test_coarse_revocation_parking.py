"""章级粗读数不得把一个**服务端已确认**的点长期留在投递队列里（run 166 实证）。

真站后果（2026-09-22 run 35706997064 = `0d02f14`，schedule 腿）：
    `1217304731`（子网划分）账本里 `verification.level=SERVER_VERIFIED`、
    `passed_object_ids=[478ca072…]`（9/11 run 34601190420 由 isPassed 记下的），
    但 `completion_evidence.type=CONFLICT`、detail「chapter has unfinished points」
    —— 它是被**章级**读数打下来的，不是这个点自己没过。
    当晚引擎投给它：页面只有这一个 video、绑到的 objectid 正是它，
    180s 里 `paused=True / readyState=0 / currentTime=0` 一次没动（连 play() 都没发生），
    子进程 `FAIL(video metadata not ready in GHA headed)` → 整轮 verdict=FAIL → Action 红。
    `cf` 已 1/3：按现行规则明晚、后晚还会各吃一次**当晚唯一**的点位预算，第三次把整章冻成 BLOCKED。

为什么现行规则会放它进队列：`reconcile_queue` 的"已确认点不再重投"有一条例外 ——
章内没有能承载剩余学习量的兄弟视频点时「宁可多重投一次」（task_registry.py:722 +
`carried_chapters`）。4731 是单视频章，没有兄弟点，于是每次都以"该章还有别的点没完"为由
被重投。而那条理由不是**这个点**的证据。

沙箱实测（把 `TASKS_DIR` 改到临时目录后重算队列，不碰真实账本）：队列 26 项，
这 6 条"已确认未回队"记录里**只有 4731 在列** —— 修它的收益是一晚一次点位预算，
代价是零条真该学的点被跳过（下面 `test_fresh_point_level_conflict_...` 钉住回程）。
"""

from app.registry.reconcile import stale_completed_by_points
from app.registry.task_registry import TaskRecord, reconcile_queue

CID = "1217304731"
OBJ = ["478ca072d9edde61ec01dd8a8dc56271"]
COARSE = "chapter has unfinished points; stale_by=catalog"


def _rec(status="FAILED", *, verified="SERVER_VERIFIED", ids=OBJ,
         ev_type="CONFLICT", detail=COARSE, task_type="video", tid=CID,
         cf=1, key=None):
    t = TaskRecord(tid, CID, "子网划分", task_type=task_type, status=status)
    t.verification.level = verified
    t.completion_evidence.type = ev_type
    t.completion_evidence.source = "isPassed"
    t.completion_evidence.passed_object_ids = list(ids)
    t.completion_evidence.detail = detail
    t.consecutive_failures = cf
    t.attempt_count = 2
    t.attempts = 2
    return t


def _ids(q):
    return [i["task_id"] for i in q.items]


# ── 1) 判据本体：什么叫"被章级读数推翻" ───────────────────────────

def test_coarse_chapter_reading_is_recognised():
    assert _rec().revoked_by_chapter_reading() is True


def test_point_level_conflict_is_not_coarse():
    """「这个点自己没过」是点级证据，绝不能被当成粗读数而躲过重投。"""
    assert _rec(detail="live status overrides prior completion").revoked_by_chapter_reading() is False
    assert _rec(ev_type="SERVER_VERIFIED").revoked_by_chapter_reading() is False


# ── 2) 队列侧：已确认 + 只是被章级读数打下来的点，不再重投 ─────────

def test_single_video_chapter_verified_point_is_not_requeued():
    """4731 现场：单视频章、没有兄弟点承载 —— 现行规则会重投，修后不投。"""
    q = reconcile_queue("k", {CID: _rec()})
    assert _ids(q) == [], f"服务端已确认的点不该再吃点位预算，实际 {_ids(q)}"


def test_unverified_failed_point_is_still_requeued():
    """没放水：没过服务端确认的失败点仍按重试策略回队。"""
    q = reconcile_queue("k", {CID: _rec(verified="NONE", ev_type="NONE", ids=[])})
    assert _ids(q) == [CID]


def test_point_without_server_evidence_for_its_own_objectid_is_requeued():
    """`passed_object_ids` 为空 ⇒ 该点从没被确认过，保护不成立。"""
    q = reconcile_queue("k", {CID: _rec(ids=[])})
    assert _ids(q) == [CID]


def test_fresh_point_level_conflict_brings_it_back():
    """回程：实时复核把这个点自己判成未完成时（`downgrade_to_pending` 会把
    verification 写成 CONFLICT），保护立即失效 —— 保护强度永远不超过
    **最新那份点级证据**，不会把真没学的点永久搁浅。"""
    t = _rec()
    t.downgrade_to_pending(detail="live verify: point unfinished")
    assert t.point_is_server_verified() is False
    assert _ids(reconcile_queue("k", {CID: t})) == [CID]


def test_rollback_chapter_keeps_the_old_priority_path():
    """`rollback_count>0` 是"曾确认完成、又被服务端推翻"的独立信号，走 9/20 定案的
    优先补齐路径（1217304722），本条判据不接管它。"""
    t = _rec(status="UNKNOWN")
    t.rollback_count = 1
    assert _ids(reconcile_queue("k", {CID: t})) == [CID]


def test_parked_points_are_reported_for_the_log():
    """停放必须出声 —— 静默跳过是 D5 那族归因丢失的来路。"""
    from app.registry.task_registry import coarse_parked_verified_points
    assert coarse_parked_verified_points({CID: _rec()}) == [CID]
    rb = _rec(status="UNKNOWN")
    rb.rollback_count = 1
    assert coarse_parked_verified_points({CID: rb}) == []


def test_blocked_record_is_left_to_the_circuit_breaker():
    """BLOCKED 归熔断逻辑管（与点级修复同规约），这里不越权。"""
    assert _ids(reconcile_queue("k", {CID: _rec(status="BLOCKED")})) == []


# ── 3) 降级侧：点级快照也是章级读数，不得推翻点级服务端确认 ────────

def _snap(video_total, video_finished, cid=CID):
    return {cid: {"video_total": video_total, "video_finished": video_finished,
                  "has_video": True}}


def test_points_leg_cannot_stale_a_server_verified_point():
    """与 `stale_completed_by_catalog` 的 :549 闸门同理：章级快照（还可能陈旧，
    见 §4.13 TTL）不能推翻"这个点已被 isPassed 确认"。"""
    existing = {CID: _rec(status="COMPLETED")}
    out = stale_completed_by_points(existing, _snap(2, 1))
    assert out == [], f"章级快照读数不该推翻点级服务端确认，实际 {out}"


def test_points_leg_still_downgrades_unverified_point():
    """4708 保护不回归：没有点级服务端确认时，章级快照仍是有效降级信号。"""
    existing = {CID: _rec(status="COMPLETED", verified="NONE", ev_type="NONE", ids=[])}
    assert stale_completed_by_points(existing, _snap(2, 1)) == [CID]
