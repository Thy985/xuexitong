"""P0-3（issue #4 尾）：账号首次进入课程 → 服务端真源材料化工单 + 把 progress.completed 写成服务端完成数。

回归：
  1. 账号命名空间无 registry → bootstrap 用服务端(catalog)材料化 work 列表；
     已完成章计入 `progress.completed`（服务端真源 —— 修复「completed 只反映本地做过几次」）。
  2. 已有进度 → NOOP：绝不覆盖、不打服务器（幂等）。
  3. 服务端抓目录失败 → 不写坏账、status=error。
  4. 全程用 account hook + mock fetch，不触发真站。
"""
import pytest

from app.registry.task_registry import TaskRecord


@pytest.fixture(autouse=True)
def _clear_hook():
    from models import set_account_id_hook
    yield
    set_account_id_hook(None)


@pytest.fixture
def storage_dirs(tmp_path, monkeypatch):
    from app.registry import task_registry as tr
    from state import course_state as cs
    state_root = tmp_path / "state"
    monkeypatch.setattr(cs, "STATE_DIR", state_root)
    monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses-legacy")
    monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course-legacy.json")
    monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry-legacy")
    return state_root


def _set_account(suffix: str):
    from models import set_account_id_hook
    set_account_id_hook(lambda: suffix)


def _ch(cid: str, status: str, title: str):
    return {"chapter_id": cid, "status": status, "title": title}


def _identity(course="265997861", clazz="151695658"):
    from models import CourseIdentity
    return CourseIdentity(course, clazz, "cpi", "t", "u",
                          "2025-01-01T00:00:00+00:00")


def _activate(id_):
    from state import course_state as cs
    cs.activate_course(id_)   # 创建 account-scoped course_state


class TestBootstrap:
    def test_materialize_and_server_progress(self, storage_dirs, monkeypatch):
        from app.registry import task_registry as tr
        from app.registry import bootstrap as BS
        from state import course_state as cs
        from app.registry.task_registry import load_registry
        id_ = _identity()
        _set_account("acc-b")
        _activate(id_)
        course_key = id_.key()  # plain <cid>_<clazz>

        chapters = [_ch("1217304706", "completed", "已完成章"),
                    _ch("1217304708", "pending", "待学章"),
                    _ch("1217304710", "completed", "另一已完成章")]
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify",
                            lambda *a, **k: {"chapters": chapters, "points": []})

        rep = BS.bootstrap_registry_from_server(course_key, "http://x")
        assert rep.mode == "bootstrap"
        assert rep.status == "ok"
        assert rep.server_completed == 2   # 2 个章 completed

        # registry 材料化了（待办章 → PENDING/other；已完成章不进 work 列表）
        reg = load_registry(course_key)
        assert reg
        pend = [t for t in reg.values() if t.status == "PENDING"]
        assert pend, "待办章应进入 work 列表"

        # 服务端完成数写入 account 命名空间的 course_state.progress.completed
        st = cs.load_course_state(course_key)
        assert st is not None and st.progress is not None
        assert st.progress.completed == 2

    def test_noop_when_account_registry_present(self, storage_dirs, monkeypatch):
        from app.registry import task_registry as tr
        from app.registry.bootstrap import bootstrap_registry_from_server
        _set_account("acc-noop")
        id_ = _identity()
        course_key = id_.key()
        tr.save_registry(course_key, {"existing": TaskRecord(
            task_id="existing", chapter_id="c", title="t", status="PENDING")})

        calls = {"n": 0}
        def _boom(*a, **k):
            calls["n"] += 1
            raise AssertionError("must not hit server when account has progress")
        # patch 的是运行中 import 的本地名（bootstrap 内 from tvdp.tdvp import ...）
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify", _boom)
        rep = bootstrap_registry_from_server(course_key, "http://x")
        assert rep.mode == "noop"
        assert calls["n"] == 0   # 没打服务器
        reg = tr.load_registry(course_key)
        assert list(reg.keys()) == ["existing"]   # 原样保留

    def test_fetch_none_yields_error_not_corrupt(self, storage_dirs, monkeypatch):
        from app.registry.bootstrap import bootstrap_registry_from_server
        _set_account("acc-err")
        course_key = _identity().key()
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify",
                            lambda *a, **k: None)
        rep = bootstrap_registry_from_server(course_key, "http://x")
        assert rep.status == "error"