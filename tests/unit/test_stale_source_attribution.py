"""R6 可观测性：L1 降级的两条腿必须在账本里留下自己的名字。

真站 run 35678657158 把已经 COMPLETED、且同轮 live 读数 `finished_video=2` 的
`1217304738` 打回队列重播，吃掉了当晚唯一的点位预算。事后定位时发现两条腿
（`stale_completed_by_catalog` 目录层 / `stale_completed_by_points` 点级快照）的返回值
在 scheduler 里被合成一个 `stale_ids`，`mark_stale` 的 detail 又是写死的一句话 ——
于是"谁干的"这条信息在日志和账本里都不存在。本测试钉住修好后的形态。
"""
from app.registry.task_registry import TaskRecord
from scheduler.scheduler import mark_stale_with_source


def _completed(tid: str) -> TaskRecord:
    r = TaskRecord(tid, tid, "章标题")
    r.mark_completed(run_id="run-1", evidence_level="SERVER_VERIFIED",
                     source="isPassed", passed_object_ids=["a" * 32])
    assert r.status == "COMPLETED"
    return r


def test_catalog_leg_names_itself_in_the_ledger():
    rec = _completed("1217304738")

    ids = mark_stale_with_source({"1217304738": rec},
                                 by_catalog=["1217304738"], by_points=[])

    assert ids == ["1217304738"]
    assert rec.status == "STALE"
    assert rec.completion_evidence.detail.endswith("stale_by=catalog")


def test_points_leg_names_itself():
    rec = _completed("x")

    mark_stale_with_source({"x": rec}, by_catalog=[], by_points=["x"])

    assert rec.completion_evidence.detail.endswith("stale_by=points")


def test_both_legs_name_both():
    rec = _completed("x")

    mark_stale_with_source({"x": rec}, by_catalog=["x"], by_points=["x"])

    assert rec.completion_evidence.detail.endswith("stale_by=catalog+points")


def test_each_record_gets_its_own_source_set():
    a, b = _completed("a"), _completed("b")

    ids = mark_stale_with_source({"a": a, "b": b},
                                 by_catalog=["a", "b"], by_points=["a"])

    assert ids == ["a", "b"]          # 合并去重、保持顺序
    assert a.completion_evidence.detail.endswith("stale_by=catalog+points")
    assert b.completion_evidence.detail.endswith("stale_by=catalog")


def test_missing_record_and_evidence_less_record_never_raise():
    no_evidence = TaskRecord("n", "n", "无证据")

    ids = mark_stale_with_source({"n": no_evidence},
                                 by_catalog=["n", "ghost"], by_points=[])

    assert ids == ["n", "ghost"]                 # 原样返回，交给上层队列处理
    assert no_evidence.status == "STALE"         # 无完成证据也要降级，只是没 detail 可写
