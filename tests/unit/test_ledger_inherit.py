"""P0-2 继承（issue #4 第六层卡点）：legacy 命名空间真实账 → 当前账号命名空间。

回归目标：
  1. legacy 有 video、account 只有退化 other → 继承后 account 账含全部 legacy 任务，
     且 reconcile_queue 产出 ≥1 项（queue 非空 = scheduler 判 RUN 而非 NOOP）。
  2. 幂等：account 已有 video 任务 → 再次继承为 NOOP，account 账不变。
  3. 不破坏：account 既有记录（如某章 COMPLETED 的 UI 证据）不被 legacy 覆盖。
  4. legacy 空/缺失 → NOOP，绝不动 account 账。
  5. 全程用 account hook + 手写 legacy 文件，不打服务器、不碰真实 state/。
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _clear_hook():
    from models import set_account_id_hook
    yield
    set_account_id_hook(None)


@pytest.fixture
def storage_dirs(tmp_path, monkeypatch):
    """把 TASKS_DIR 重定向到 tmp/registry；legacy 与 account 命名空间同在一个 tmp 下。"""
    from app.registry import task_registry as tr
    from state import course_state as cs
    state_root = tmp_path / "state"
    monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry")
    monkeypatch.setattr(cs, "STATE_DIR", state_root)
    monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses-legacy")
    monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course-legacy.json")
    return state_root


def _set_account(suffix: str):
    from models import set_account_id_hook
    set_account_id_hook(lambda: suffix)


def _write_legacy_registry(storage_root, course_key, records):
    """手写 legacy 账（模拟 P0-2 前积累的真账），不经 account hook。"""
    d = storage_root / "registry" / course_key
    d.mkdir(parents=True, exist_ok=True)
    (d / "tasks.json").write_text(
        json.dumps({k: r.to_dict() for k, r in records.items()},
                   ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


class TestInheritFromLegacy:
    def test_inherits_video_tasks_into_account(self, storage_dirs):
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import (
            load_registry, reconcile_queue, save_registry, load_legacy_registry)
        key = "265997861_151695658"
        _set_account("acc-inherit")
        # legacy：一条 PENDING video（带 SERVER_VERIFIED 强证据的 COMPLETED 也带一条）
        legacy_video = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", title="点对点协议PPP",
            task_type="video", status="PENDING",
            verification=tr.Verification(level="NONE"),
        )
        legacy_completed = tr.TaskRecord(
            task_id="1217304700", chapter_id="1217304700", title="互联网概述",
            task_type="video", status="COMPLETED",
            verification=tr.Verification(level="SERVER_VERIFIED",
                                          verified_at_utc="2026-09-05T12:57:00+00:00",
                                          run_id="33966450863",
                                          source_detail="passed_object_ids=3"),
            completion_evidence=tr.CompletionEvidence(
                type="SERVER_VERIFIED", source="isPassed", run_id="33966450863",
                detail="passed_object_ids=3", passed_object_ids=["3"]),
        )
        _write_legacy_registry(storage_dirs, key,
                               {"1217304719": legacy_video,
                                "1217304700": legacy_completed})

        # account 命名空间：仅有退化 other 账（模拟 30 条退化账的最小结构）
        degenerate = tr.TaskRecord(
            task_id="1217304719:other", chapter_id="1217304719", title="点对点协议PPP",
            task_type="other", status="PENDING")
        save_registry(key, {"1217304719:other": degenerate})

        rep = inherit_from_legacy(key)
        assert rep.mode == "inherit"
        assert rep.status == "ok"
        assert rep.inherited == 2

        reg = load_registry(key)
        # legacy 的 video 任务已并入
        assert reg["1217304719"].task_type == "video"
        assert reg["1217304719"].status == "PENDING"
        assert reg["1217304700"].status == "COMPLETED"
        # account 既有退化 other 保留（未被覆盖/删除）
        assert reg["1217304719:other"].task_type == "other"
        assert rep.total_tasks == 3

        # 决定性回归点：queue 必须非空（含继承进来的 PENDING video）
        q = reconcile_queue(key, reg, set())
        assert len(q.items) >= 1, f"继承后 queue 必须非空，实际: {q.items}"
        tids = {i["task_id"] for i in q.items}
        assert "1217304719" in tids

    def test_degenerate_completed_only_ledger_still_inherits(self, storage_dirs):
        """issue #4 死循环核心回归：account 退化账仅含 1 条 COMPLETED video 时，
        inherit 必须仍执行（否则 queue 恒空、bootstrap 永久 NOOP）。
        COMPLETED video 不构成幂等护栏——只有「未完成」的 video 才算已继承。"""
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import save_registry, load_registry, reconcile_queue
        key = "265997861_151695658"
        _set_account("acc-degen")
        legacy_video = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", task_type="video",
            status="PENDING", title="t", verification=tr.Verification(level="NONE"))
        _write_legacy_registry(storage_dirs, key, {"1217304719": legacy_video})

        # account 退化账：唯一 video 已 COMPLETED（与 main 上 30 条账同构）
        deg_video = tr.TaskRecord(
            task_id="1217304706", chapter_id="1217304706", task_type="video",
            status="COMPLETED", title="物理层",
            verification=tr.Verification(level="UI", source_detail="dom"))
        deg_other = tr.TaskRecord(
            task_id="1217304721:other", chapter_id="1217304721", task_type="other",
            status="PENDING", title="t")
        save_registry(key, {"1217304706": deg_video, "1217304721:other": deg_other})

        rep = inherit_from_legacy(key)
        assert rep.mode == "inherit", \
            f"仅 COMPLETED video 的退化账必须仍继承，实际 mode={rep.mode}"
        reg = load_registry(key)
        assert reg["1217304719"].task_type == "video"
        assert reg["1217304719"].status == "PENDING"
        q = reconcile_queue(key, reg, set())
        assert any(i.get("task_id") == "1217304719" for i in q.items), \
            "继承后 queue 必须含 legacy PENDING video"

    def test_inherit_is_idempotent_when_account_has_open_video(self, storage_dirs):
        """真幂等：account 已有**未完成**的 video 任务 → 再次继承 NOOP，账不变。"""
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import save_registry, load_registry
        key = "265997861_151695658"
        _set_account("acc-idem2")
        legacy_video = tr.TaskRecord(
            task_id="1217304731", chapter_id="1217304731", task_type="video",
            status="PENDING", title="t", verification=tr.Verification(level="NONE"))
        _write_legacy_registry(storage_dirs, key, {"1217304731": legacy_video})

        # account 已有 open video（模拟首次继承已发生）
        open_video = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", task_type="video",
            status="PENDING", title="t", verification=tr.Verification(level="NONE"))
        save_registry(key, {"1217304719": open_video})

        rep = inherit_from_legacy(key)
        assert rep.mode == "noop"
        assert rep.inherited == 0
        assert load_registry(key) == {"1217304719": open_video}

    def test_account_existing_records_won_be_overridden(self, storage_dirs):
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import save_registry, load_registry
        key = "265997861_151695658"
        _set_account("acc-keep")
        # legacy 把某章记 PENDING；account 已把它跑成 COMPLETED(UI) —— 继承不得回退该记录
        legacy_pending = tr.TaskRecord(
            task_id="1217304706", chapter_id="1217304706", task_type="video",
            status="PENDING", title="物理层", verification=tr.Verification(level="NONE"))
        legacy_open = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", task_type="video",
            status="PENDING", title="点对点协议PPP",
            verification=tr.Verification(level="NONE"))
        _write_legacy_registry(storage_dirs, key,
                               {"1217304706": legacy_pending,
                                "1217304719": legacy_open})

        acct_completed = tr.TaskRecord(
            task_id="1217304706", chapter_id="1217304706", task_type="video",
            status="COMPLETED", title="物理层",
            verification=tr.Verification(level="UI",
                                          verified_at_utc="2026-10-02T01:04:00+00:00",
                                          source_detail="server DOM completed marker"),
            completion_evidence=tr.CompletionEvidence(
                type="UI", source="server DOM", detail="completed marker"))
        save_registry(key, {"1217304706": acct_completed})

        # account 尚无 open video → 幂等护栏放行；但既有 COMPLETED 记录必须保留（不被 legacy PENDING 回退）
        rep = inherit_from_legacy(key)
        assert rep.mode == "inherit"
        reg = load_registry(key)
        assert reg["1217304706"].status == "COMPLETED"
        assert reg["1217304706"].verification.level == "UI"
        assert reg["1217304719"].status == "PENDING"

    def test_empty_legacy_is_noop_and_touches_nothing(self, storage_dirs):
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import save_registry, load_registry
        key = "265997861_151695658"
        _set_account("acc-empty")
        # 不写任何 legacy 账
        save_registry(key, {})
        rep = inherit_from_legacy(key)
        assert rep.mode == "noop"
        assert rep.inherited == 0
        assert load_registry(key) == {}

    def test_inherit_does_not_mutate_legacy_source(self, storage_dirs):
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import save_registry
        key = "265997861_151695658"
        _set_account("acc-immutable")
        legacy_video = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", task_type="video",
            status="PENDING", title="t", verification=tr.Verification(level="NONE"))
        _write_legacy_registry(storage_dirs, key, {"1217304719": legacy_video})
        before = tr.load_legacy_registry(key)
        assert before["1217304719"].status == "PENDING"

        inherit_from_legacy(key)

        after = tr.load_legacy_registry(key)
        # legacy 源账不变
        assert after == before
        assert after["1217304719"].status == "PENDING"


class TestSchedulerInheritHook:
    """_ensure_bootstrap_on_start 在 fetch 前先跑 inherit_from_legacy。"""
    def test_inherits_before_bootstrap_fetch(self, storage_dirs, monkeypatch):
        from scheduler import scheduler as SCH
        from scheduler.scheduler import _ensure_bootstrap_on_start
        from app.registry import task_registry as tr
        from app.registry.bootstrap import inherit_from_legacy
        key = "265997861_151695658"
        _set_account("acc-sched")
        monkeypatch.setenv("CX_USER", "u")
        # 制造 legacy 账 + 空 account 账
        legacy_video = tr.TaskRecord(
            task_id="1217304719", chapter_id="1217304719", task_type="video",
            status="PENDING", title="t", verification=tr.Verification(level="NONE"))
        _write_legacy_registry(storage_dirs, key, {"1217304719": legacy_video})

        fetched = {"n": 0}
        def _fetch(*a, **k):
            fetched["n"] += 1
            return {"chapters": [], "points": []}
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify", _fetch)
        SCH._PROBE_FETCH_CACHE.clear()

        _ensure_bootstrap_on_start("http://x", key, "r1")

        # 继承发生在 fetch 之前，且 account 账因此非空 → bootstrap fetch 不再触发
        reg = tr.load_registry(key)
        assert reg.get("1217304719") is not None
        assert reg["1217304719"].task_type == "video"
        # 关键：因为继承已让 registry 非空，bootstrap 的 fetch 应当被幂等护栏挡下
        assert fetched["n"] == 0, "继承后 registry 非空 → 不应再打服务器"
