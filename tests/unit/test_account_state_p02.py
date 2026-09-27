"""P0-2（issue #4）：账号隔离 storage namespace —— same course + different account ≠ same state。

核心回归：
  1. 同一 course_id+clazz_id，两个不同账号 → 状态落到各自 accounts/<acc>/ 命名空间，
     互不可见（不再像旧版 fork 后沿原作者裸 course_id_clazz_id 路径）。
  2. 无登录（离线/测试默认）→ 回退 legacy 裸路径（向后兼容）。
  3. 旧 legacy unscoped state 不会被自动绑定成某个账号的 state。
"""
import pytest


def _record(task_id="k", task_type="video"):
    from app.registry import task_registry as tr
    return tr.TaskRecord(task_id=task_id, chapter_id="c", title="t",
                         task_type=task_type)


@pytest.fixture(autouse=True)
def _clear_account_hook():
    """每个用例结束清空账号 hook，避免模块级全局在两个用例间泄漏。"""
    from models import set_account_id_hook
    yield
    set_account_id_hook(None)


@pytest.fixture
def storage_dirs(tmp_path, monkeypatch):
    """把 state 与 registry 重定向到临时目录，绝不写仓库真实 state/。"""
    from app.registry import task_registry as tr
    from state import course_state as cs

    state_root = tmp_path / "state"
    monkeypatch.setattr(cs, "STATE_DIR", state_root)
    monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses-legacy")
    monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course-legacy.json")
    monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry-legacy")
    return state_root


def _set_account(cx: str, suffix: str) -> None:
    """把账号 hook 设成返回 suffix —— 使 ns 名确定且显式可见。"""
    from models import set_account_id_hook
    set_account_id_hook(lambda: suffix)


def _identity(course="265997861", clazz="151695658"):
    from models import CourseIdentity
    return CourseIdentity(
        course_id=course, clazz_id=clazz, cpi="cpi",
        title="t", raw_url="u",
        resolved_at_utc="2025-01-01T00:00:00+00:00",
    )


class TestAccountIsolation:
    """same course + diff account → 不同 namespace，course state 互不可见。"""

    def test_course_state_isolated_by_account(self, storage_dirs):
        from state import course_state as cs
        id_ = _identity()
        _set_account("cx", "alice")
        cs.activate_course(id_)
        assert cs.load_course_state(id_.key()) is not None

        acc_courses = storage_dirs / "accounts" / "alice" / "courses"
        assert (acc_courses / f"{id_.key()}.json").exists()
        assert (storage_dirs / "accounts" / "alice" / "active_course.json").exists()

    def test_other_account_does_not_see_state(self, storage_dirs):
        from state import course_state as cs
        id_ = _identity()
        _set_account("cx", "alice")
        cs.activate_course(id_)
        # 切到 bob：同一课程不应读到 alice 的状态
        _set_account("cx", "bob")
        assert cs.load_course_state(id_.key()) is None

    def test_registry_isolated_by_account(self, storage_dirs):
        from app.registry import task_registry as tr
        id_ = _identity()
        _set_account("cx", "alice")
        tr.save_registry(id_.key(), {})
        assert (storage_dirs / "accounts" / "alice" / "registry" / id_.key()).exists()
        # bob 读不到 alice 的 registry
        _set_account("cx", "bob")
        assert not (storage_dirs / "accounts" / "bob" / "registry" / id_.key()).exists()
        assert tr.load_registry(id_.key()) == {}

    def test_same_account_stable_path(self, storage_dirs):
        from app.registry import task_registry as tr
        id_ = _identity()
        _set_account("cx", "same")
        # 保存一个真实的 task 记录，再同一账号读回，验证稳定往返
        tr.save_registry(id_.key(), {"k": _record("vid")})
        p = storage_dirs / "accounts" / "same" / "registry" / id_.key() / "tasks.json"
        assert p.exists()
        reg = tr.load_registry(id_.key())
        assert reg and reg["k"].task_type == "video"


class TestLegacyFallback:
    """无账号（离线/测试）→ 落回 legacy 裸路径，向后兼容、不得自动绑定账号。"""

    def test_no_account_uses_legacy_course_path(self, storage_dirs):
        from state import course_state as cs
        _set_account("", "")
        id_ = _identity()
        cs.activate_course(id_)
        legacy = storage_dirs / "courses-legacy"
        assert (legacy / f"{id_.key()}.json").exists()

    def test_no_account_uses_legacy_registry(self, storage_dirs):
        from app.registry import task_registry as tr
        _set_account("", "")
        id_ = _identity()
        tr.save_registry(id_.key(), {})
        assert (storage_dirs / "registry-legacy" / id_.key() / "tasks.json").exists()

    def test_account_state_never_reaches_legacy_dir(self, storage_dirs):
        """账号写操作绝不落进 legacy 裸目录（旧文件也不会被“代理沿用”）。"""
        from app.registry import task_registry as tr
        id_ = _identity()
        _set_account("cx", "carol")
        tr.save_registry(id_.key(), {})
        legacy = storage_dirs / "registry-legacy" / id_.key()
        assert not legacy.exists()