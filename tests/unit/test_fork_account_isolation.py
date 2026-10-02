"""fork 场景（issue #4 建议 1/2 的审查收口）：别人 fork + 配 Secrets 后就能跑。

复现 fork 用户的真实起点：仓库里带着**原作者**的 legacy 未 scoped 账本
（`state/registry/<key>/tasks.json`，78 条，随 fork 复制）+ 原作者账号命名空间；
fork 用户自己的账号 namespace（CX_USER 哈希不同）为空。

审查发现的缺陷与修复（本文件逐条钉住）：
  1. [致命] `inherit_from_legacy` 曾对新账号自动继承原作者 legacy 账 → 账本污染
     （原作者的 COMPLETED 被 SV 证据保护、永远不被推翻，fork 用户没看过的视频
     永远排不进队列），且 registry 非空挡住 P0-3 bootstrap。修复：默认不继承，
     `XUE_INHERIT_LEGACY=1` 仅限原作者本机迁移显式打开。
  2. fork 默认路径：空账号账 → bootstrap 从**fork 用户自己的**服务端真源材料化。
  3. cookie 缓存按账号隔离：同机切换 CX_USER 不得误用他人会话。
  4. CI（GITHUB_ACTIONS）上 run/scheduler 缺 Secrets → 拒绝运行（exit 2），
     而不是落到原作者 legacy 账本上跑并提交污染。
  5. `bootstrap_registry_from_server(force=True)`：账本对齐出口（issue #4 建议 1）。
"""

import json

import pytest


@pytest.fixture(autouse=True)
def _clear_hook():
    from models import set_account_id_hook
    yield
    set_account_id_hook(None)


@pytest.fixture
def fork_repo(tmp_path, monkeypatch):
    """模拟 fork 后的仓库：原作者 legacy 账 + 原作者账号命名空间都在，
    fork 用户账号（acc-fork）为空。state 全部重定向到 tmp。"""
    from app.registry import task_registry as tr
    from state import course_state as cs
    state_root = tmp_path / "state"
    monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry")
    monkeypatch.setattr(cs, "STATE_DIR", state_root)
    monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses")
    monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course.json")

    KEY = "265997861_151695658"
    # 原作者 legacy 账（未 scoped，随 fork 复制）：有完成有待学
    legacy_dir = state_root / "registry" / KEY
    legacy_dir.mkdir(parents=True, exist_ok=True)
    author_pending = tr.TaskRecord(task_id="1217304771", chapter_id="1217304771",
                                   task_type="video", status="PENDING",
                                   title="万维网WWW")
    author_done = tr.TaskRecord(task_id="1217304719", chapter_id="1217304719",
                                task_type="video", status="COMPLETED",
                                title="点对点协议PPP")
    author_done.verification.level = "SERVER_VERIFIED"
    author_done.completion_evidence.passed_object_ids = ["author-oid"]
    legacy_dir.joinpath("tasks.json").write_text(json.dumps(
        {"1217304771": author_pending.to_dict(), "1217304719": author_done.to_dict()},
        ensure_ascii=False), encoding="utf-8")
    # 原作者账号命名空间（同样随 fork 复制，但对 fork 用户不可见——路径不同）
    acc_dir = state_root / "accounts" / "author-hash" / "registry" / KEY
    acc_dir.mkdir(parents=True, exist_ok=True)
    acc_dir.joinpath("tasks.json").write_text("{}", encoding="utf-8")
    return state_root


# ── 1+2：fork 默认不继承，bootstrap 用 fork 用户自己的服务端真源 ────

class TestForkDefaultNoInherit:
    def test_inherit_is_noop_for_fork_account_by_default(self, fork_repo):
        """[致命缺陷回归] fork 账号（空账）+ 原作者 legacy 在场 → 默认 NOOP。"""
        from models import set_account_id_hook
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import load_registry

        set_account_id_hook(lambda: "acc-fork")
        rep = inherit_from_legacy("265997861_151695658")

        assert rep.mode == "noop", "fork 账号默认不得继承原作者 legacy 账"
        assert "disabled" in rep.reason
        assert load_registry("265997861_151695658") == {}, \
            "fork 账号命名空间必须保持为空，交给 bootstrap 从服务端真源材料化"

    def test_inherit_opt_in_preserves_author_migration_path(self, fork_repo):
        """原作者本机迁移：显式 XUE_INHERIT_LEGACY=1 后机制仍可用。"""
        from models import set_account_id_hook
        from app.registry.bootstrap import inherit_from_legacy
        from app.registry.task_registry import load_registry

        set_account_id_hook(lambda: "acc-fork")
        monkey_env = pytest.MonkeyPatch()
        try:
            monkey_env.setenv("XUE_INHERIT_LEGACY", "1")
            rep = inherit_from_legacy("265997861_151695658")
        finally:
            monkey_env.undo()
        assert rep.mode == "inherit" and rep.inherited == 2
        reg = load_registry("265997861_151695658")
        assert reg["1217304771"].status == "PENDING"

    def test_fork_first_run_bootstraps_from_own_server_truth(self, fork_repo,
                                                             monkeypatch):
        """端到端：fork 用户首轮 scheduler → bootstrap 材料**他自己**的服务端真源。

        服务端（stub）说 fork 用户已完成 1217304719、没学过 1217304771 —— 与
        原作者 legacy 账相反。bootstrap 结果必须跟服务端走，不是跟 legacy 走。
        """
        from models import set_account_id_hook
        from scheduler.scheduler import _ensure_bootstrap_on_start, _PROBE_FETCH_CACHE
        from app.registry import task_registry as tr

        set_account_id_hook(lambda: "acc-fork")
        monkeypatch.setenv("CX_USER", "fork-user")
        chapters = [
            {"chapter_id": "1217304719", "title": "点对点协议PPP",
             "status": "completed", "job_remaining": 0},
            {"chapter_id": "1217304771", "title": "万维网WWW",
             "status": "pending", "job_remaining": 3},
        ]
        points = [{"task_id": "1217304771", "type": "video",
                   "isFinished": False, "objectid": "fork-oid"}]

        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify",
                            lambda *a, **k: {"chapters": chapters,
                                             "points": points})
        _PROBE_FETCH_CACHE.clear()
        _ensure_bootstrap_on_start("http://x?chapterId=1217304771",
                                   "265997861_151695658", "r1")

        reg = tr.load_registry("265997861_151695658")
        # 服务端真源：已完成章不产任务记录，但**原作者 legacy 的记录也绝不能出现**
        # （若发生继承，这里会有作者版本的 1217304719 COMPLETED 污染记录）
        assert reg.get("1217304719") is None or \
            reg["1217304719"].status == "COMPLETED", \
            "legacy 继承若发生，作者记录会混入 fork 账本"
        # 服务端真源：771 未学 → 可执行任务（fork 用户自己的真源说了算）
        assert reg["1217304771"].task_type == "video"
        assert reg["1217304771"].status in ("PENDING", "DISCOVERED")
        # 队列非空：fork 用户首轮即可被调度推进
        q = tr.reconcile_queue("265997861_151695658", reg,
                               tr.done_chapter_ids_from_registry(reg),
                               points_map=tr.load_chapter_points(
                                   "265997861_151695658"))
        assert "1217304771" in {i["task_id"] for i in q.items}


# ── 3：cookie 缓存按账号隔离 ────────────────────────────────────────

class TestCookieAccountScoping:
    def test_cookie_file_differs_per_account(self, monkeypatch, tmp_path):
        import utils.cookie_store as cs
        monkeypatch.setattr(cs, "COOKIE_DIR", tmp_path / ".cache")
        from models import set_account_id_hook

        set_account_id_hook(lambda: "acc-a")
        assert cs._cookie_file().name == "cookies-acc-a.json"
        set_account_id_hook(lambda: "acc-b")
        assert cs._cookie_file().name == "cookies-acc-b.json"
        set_account_id_hook(lambda: "acc-a")
        assert cs._cookie_file().name == "cookies-acc-a.json"

    def test_saved_cookies_are_not_visible_to_other_account(self, monkeypatch,
                                                            tmp_path):
        import utils.cookie_store as cs
        monkeypatch.setattr(cs, "COOKIE_DIR", tmp_path / ".cache")
        from models import set_account_id_hook

        class _Ctx:
            def cookies(self):
                return [{"name": "UID", "value": "session-a"}]

        set_account_id_hook(lambda: "acc-a")
        cs.save_cookies(_Ctx())
        assert cs.load_cookies() == [{"name": "UID", "value": "session-a"}]

        set_account_id_hook(lambda: "acc-b")
        assert cs.load_cookies() is None, \
            "切换账号后不得读到上一个账号的会话 cookie"
        cs.clear_cookies()          # 不得误删 acc-a 的文件
        set_account_id_hook(lambda: "acc-a")
        assert cs.load_cookies() == [{"name": "UID", "value": "session-a"}]

    def test_no_account_falls_back_to_legacy_filename(self, monkeypatch, tmp_path):
        import utils.cookie_store as cs
        monkeypatch.setattr(cs, "COOKIE_DIR", tmp_path / ".cache")
        from models import set_account_id_hook
        set_account_id_hook(None)
        monkeypatch.delenv("CX_USER", raising=False)
        assert cs._cookie_file().name == "cookies.json"


# ── 4：CI 缺 Secrets 拒绝运行 ───────────────────────────────────────

class TestCiSecretsGuard:
    def _run_main(self, monkeypatch, action, github_actions, capsys_text=None):
        import app.run as runmod
        err = runmod.validate_action_secrets(action, github_actions=github_actions)
        return err

    def test_ci_scheduler_without_secrets_is_refused(self, monkeypatch):
        import app.run as runmod
        monkeypatch.delenv("CX_USER", raising=False)
        monkeypatch.delenv("CX_PASS", raising=False)
        err = self._run_main(monkeypatch, "scheduler", github_actions=True)
        assert err and "CX_USER" in err

    def test_ci_run_without_secrets_is_refused(self, monkeypatch):
        import app.run as runmod
        monkeypatch.delenv("CX_USER", raising=False)
        monkeypatch.delenv("CX_PASS", raising=False)
        assert self._run_main(monkeypatch, "run", github_actions=True)

    def test_ci_scheduler_with_secrets_passes(self, monkeypatch):
        import app.run as runmod
        monkeypatch.setenv("CX_USER", "u")
        monkeypatch.setenv("CX_PASS", "p")
        assert self._run_main(monkeypatch, "scheduler", github_actions=True) is None

    def test_local_scheduler_without_secrets_still_allowed(self, monkeypatch):
        """本地离线诊断（无 GITHUB_ACTIONS）不拦截 —— legacy 路径是它的合法用途。"""
        import app.run as runmod
        monkeypatch.delenv("CX_USER", raising=False)
        monkeypatch.delenv("CX_PASS", raising=False)
        assert self._run_main(monkeypatch, "scheduler", github_actions=False) is None


# ── 5：force bootstrap（账本对齐出口）───────────────────────────────

class TestForceBootstrap:
    def test_force_wipes_and_rebuilds_from_server(self, tmp_path, monkeypatch):
        from models import set_account_id_hook
        from app.registry import task_registry as tr
        from state import course_state as cs
        from app.registry.bootstrap import bootstrap_registry_from_server
        state_root = tmp_path / "state"
        monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry")
        monkeypatch.setattr(tr, "_registry_dir", lambda: state_root / "registry")
        monkeypatch.setattr(cs, "STATE_DIR", state_root)
        monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses")
        monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course.json")
        set_account_id_hook(None)
        monkeypatch.delenv("CX_USER", raising=False)

        KEY = "265997861_151695658"
        poisoned = tr.TaskRecord(task_id="ghost", chapter_id="ghost",
                                 task_type="video", status="COMPLETED",
                                 title="污染记录")
        tr.save_registry(KEY, {"ghost": poisoned})

        chapters = [{"chapter_id": "1217304771", "title": "万维网WWW",
                     "status": "pending", "job_remaining": 3}]
        points = [{"task_id": "1217304771", "type": "video",
                   "isFinished": False, "objectid": "oid"}]
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify",
                            lambda *a, **k: {"chapters": chapters,
                                             "points": points})

        rep = bootstrap_registry_from_server(KEY, "http://x", force=True)

        assert rep.mode == "bootstrap" and rep.status == "ok"
        reg = tr.load_registry(KEY)
        assert "ghost" not in reg, "污染记录必须被 force 清掉"
        assert "1217304771" in reg and reg["1217304771"].task_type == "video"

    def test_force_on_empty_registry_is_just_bootstrap(self, tmp_path, monkeypatch):
        from models import set_account_id_hook
        from app.registry import task_registry as tr
        from state import course_state as cs
        from app.registry.bootstrap import bootstrap_registry_from_server
        state_root = tmp_path / "state"
        monkeypatch.setattr(tr, "TASKS_DIR", state_root / "registry")
        monkeypatch.setattr(tr, "_registry_dir", lambda: state_root / "registry")
        monkeypatch.setattr(cs, "STATE_DIR", state_root)
        monkeypatch.setattr(cs, "COURSES_DIR", state_root / "courses")
        monkeypatch.setattr(cs, "ACTIVE_FILE", state_root / "active_course.json")
        set_account_id_hook(None)
        monkeypatch.delenv("CX_USER", raising=False)
        chapters = [{"chapter_id": "1217304771", "title": "万维网WWW",
                     "status": "pending", "job_remaining": 3}]
        monkeypatch.setattr("tvdp.tdvp.fetch_course_detail_and_verify",
                            lambda *a, **k: {"chapters": chapters, "points": []})
        rep = bootstrap_registry_from_server("k", "http://x", force=True)
        assert rep.mode == "bootstrap" and rep.status == "ok"
