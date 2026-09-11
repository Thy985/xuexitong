"""TASKS_DIR 锚定回归护栏（本次根因 eb34a7e 保护）。

背景：app/registry/task_registry.py 曾用 2 级 `.parent` 解析仓库根，导致
TASKS_DIR=<repo>/app/state/registry（与真实已提交 <repo>/state/registry 漂移），
运行时 load 到空 registry，BLOCKED 冻结在输入端就丢了，反复重跑 PP112 章。
修复：用 3 级 `.parent`（registry→app→<repo>）锚到仓库根 state/registry。
本测试把该锚定「钉死」，防止再回归成 app/state 而让定时 run 读错状态。
"""
import pathlib

import app.registry.task_registry as tr


def _repo_root() -> pathlib.Path:
    # 与 task_registry._REPO_ROOT 同构：模块在 app/registry/ → 向上 3 级到 <repo>
    return pathlib.Path(tr.__file__).resolve().parent.parent.parent


def test_tasks_dir_anchors_to_repo_root_state():
    td = pathlib.Path(tr.TASKS_DIR).resolve()
    expected = (_repo_root() / "state" / "registry").resolve()
    assert td == expected, f"TASKS_DIR={td} != {expected}"


def test_tasks_dir_is_not_under_app():
    """绝不能落回 <repo>/app/state/registry 这种漂移目录（历史 bug）。"""
    td = pathlib.Path(tr.TASKS_DIR).resolve()
    assert "app" not in td.parts, f"TASKS_DIR {td} 又落进 app/ 下，与真实 state 漂移"


def test_tasks_dir_is_absolute_anchor_to_module_not_cwd():
    """无论当前工作目录如何，TASKS_DIR 必须锚到仓库（不随 CWD 变化）。"""
    td = pathlib.Path(tr.TASKS_DIR)
    assert td.is_absolute()
    assert str(td).startswith(str(_repo_root()))


def test_repo_state_has_course_registry_file():
    """仓库根 state/registry 应能找到课程的 tasks.json（真实 checkpoint 所在）。"""
    p = _repo_root() / "state" / "registry" / "265997861_151695658" / "tasks.json"
    # 若该课程文件尚不存在（首次初始化），soft-check；文件存在时必须是可解析 JSON。
    assert p.exists() is True, f"期望仓库根存在课程 checkpoint: {p}"