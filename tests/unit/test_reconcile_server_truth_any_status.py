# D14（2026-09-21 真站章 1217304738）：服务端已把该章第 1 个视频点判 finished
# （DOM 类 `ans-job-finished`，live 读数 isFinished=true），但 registry 里它是
# **UNKNOWN** —— 而 reconcile 的服务端真源治愈只认 BLOCKED（reconcile.py 里
# `old.status == "BLOCKED" and tid in live_finished`）。
#
# 后果链：该点被当成待学任务投出去 → 页面绝不为已完成点起流（A 方案探针实测：
# 点它的播放键只拿到 metadata，ct 冻结在 227，24s 不动）→ Step F 等 metadata
# 90s×2 超时 FAIL → 点记 FAILED cf=1、课程 failure_count 推到 6 并整体 BLOCKED，
# 今晚 nightly 直接跳过这门课。
#
# 结论：服务端 finished 判定本身就是"真实事件"（与当前状态无关）。任何非
# COMPLETED 记录都应被 SERVER_VERIFIED 治愈；否则引擎会反复投递一个永远播不
# 起来的已完成点，并把课程推向熔断。

import pytest

from app.registry.reconcile import reconcile_registry
from app.registry.task_registry import TaskRecord
from tvdp.tdvp import TaskEvidence, TaskInfo


def _rec(tid, cid, status):
    r = TaskRecord(tid, cid, "双视频章")
    r.status = status
    return r


def _discovery(tid, cid):
    return TaskInfo(tid, cid, "双视频章", "video", "PENDING", "UI", "x",
                    TaskEvidence("PENDING", "UI", ""))


@pytest.mark.parametrize("status", ["UNKNOWN", "FAILED", "PENDING", "DISCOVERED"])
def test_server_finished_heals_any_non_completed_status(status):
    old = _rec("1217304738", "1217304738", status)
    fixed, _rep = reconcile_registry(
        "k", {"1217304738": old},
        [_discovery("1217304738", "1217304738")],
        live_finished={"1217304738"},
    )
    assert fixed["1217304738"].status == "COMPLETED"
    assert fixed["1217304738"].verification.level == "SERVER_VERIFIED"


def test_only_the_exact_finished_point_is_healed():
    # 兄弟点的 finished 不算数（点身份必须精确）
    old = _rec("1217304738", "1217304738", "UNKNOWN")
    fixed, _rep = reconcile_registry(
        "k", {"1217304738": old},
        [_discovery("1217304738", "1217304738")],
        live_finished={"1217304738:video2"},
    )
    assert fixed["1217304738"].status != "COMPLETED"


# ── 生产路径接线（D14 续）────────────────────────────────────────────
# reconcile_registry 的分支只有拿到 live_finished 才会治愈，而 live 读数来自
# heal_blocked_by_live（冻结章）/ pick_conflict_chapters（有 COMPLETED 嫌疑的章）。
# 4738 base 是 FAILED 且该章没有 COMPLETED 记录 ⇒ 两条路都不读它，
# live_finished 永远为空，治愈形同虚设。冻结章既然已经读了 live，就该把
# 读数用到所有非 COMPLETED 记录上，并顺手收掉枚举里不存在的幻影点。

def _blocked_and_friends():
    v1 = _rec("1217304738", "1217304738", "FAILED")
    v1.consecutive_failures = 1
    v2 = _rec("1217304738:video2", "1217304738", "BLOCKED")
    v2.consecutive_failures = 3
    v3 = _rec("1217304738:video3", "1217304738", "DISCOVERED")
    return {v1.task_id: v1, v2.task_id: v2, v3.task_id: v3}


def _live_two_points():
    # 真站读数：两个视频点，第 1 个服务端已判 finished
    return [
        {"task_id": "1217304738", "type": "video", "isFinished": True,
         "objectid": "94382be48a99"},
        {"task_id": "1217304738:video2", "type": "video", "isFinished": False,
         "objectid": "e79a9a86eba1"},
    ]


def test_frozen_chapter_live_heal_covers_failed_record():
    from app.registry.reconcile import heal_blocked_by_live
    existing = _blocked_and_friends()
    fixed, rep = heal_blocked_by_live("k", existing, [], {}, lambda cid: _live_two_points())
    assert fixed["1217304738"].status == "COMPLETED"
    assert fixed["1217304738"].verification.level == "SERVER_VERIFIED"
    assert rep.healed_by_server == 1


def test_phantom_video_point_is_pruned_from_ledger():
    from app.registry.reconcile import heal_blocked_by_live
    existing = _blocked_and_friends()
    fixed, rep = heal_blocked_by_live("k", existing, [], {}, lambda cid: _live_two_points())
    assert "1217304738:video3" not in fixed, "枚举里不存在的幻影点该收掉"
    assert getattr(rep, "phantom_pruned", 0) == 1
