"""loop 启动向导的纯逻辑单测:章数解析、env 优先级、EOF/换课分支。

向导设计约束(见 app/loop.py::_launch_wizard):
  - 回车 = 全默认(可跳过);非法/越界输入 = 保持当前值,绝不报错中断
  - EOF(管道/计划任务)= 跳过向导,返回调用方默认
  - CLI 显式 > XUE_LOOP_MAX_CHAPTERS > 向导输入 > 默认 1
  - 向导答案只作用于本次会话,不写回 state/.env(此处由不落盘的实现保证)
"""

import pytest

import state.course_state as course_state
from app import loop


class _ActiveStub:
    """load_active_course 的最小替身:key()/title 与 CourseIdentity 对齐。"""

    def key(self) -> str:
        return "1_2"

    title = "demo course"


@pytest.fixture(autouse=True)
def _active_course(monkeypatch):
    monkeypatch.setattr(course_state, "load_active_course", lambda: _ActiveStub())
    monkeypatch.setattr(course_state, "load_course_state", lambda key: None)


def test_parse_max_chapters_variants():
    assert loop._parse_max_chapters("", 1) == 1          # 回车 = 保持
    assert loop._parse_max_chapters("3", 1) == 3
    assert loop._parse_max_chapters(" 2 ", 1) == 2       # 容忍空白
    assert loop._parse_max_chapters("abc", 1) == 1       # 非法 = 保持
    assert loop._parse_max_chapters("0", 4) == 4         # 越界 = 保持
    assert loop._parse_max_chapters("-2", 4) == 4


def test_env_max_chapters(monkeypatch):
    monkeypatch.delenv("XUE_LOOP_MAX_CHAPTERS", raising=False)
    assert loop._env_max_chapters() is None
    monkeypatch.setenv("XUE_LOOP_MAX_CHAPTERS", "3")
    assert loop._env_max_chapters() == 3
    monkeypatch.setenv("XUE_LOOP_MAX_CHAPTERS", "abc")   # 非法 = 未设
    assert loop._env_max_chapters() is None
    monkeypatch.setenv("XUE_LOOP_MAX_CHAPTERS", "0")     # 越界 = 未设
    assert loop._env_max_chapters() is None


def test_wizard_eof_returns_fallback(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError))
    assert loop._launch_wizard(None) is None            # 调用方落默认 1
    assert loop._launch_wizard(5) == 5                  # CLI 显式不受影响


def test_wizard_chapters_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "3")
    assert loop._launch_wizard(None) == 3
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert loop._launch_wizard(None) == 1               # 回车 = 默认 1


def test_wizard_explicit_cli_skips_env_and_keeps(monkeypatch):
    monkeypatch.setenv("XUE_LOOP_MAX_CHAPTERS", "9")
    monkeypatch.setattr("builtins.input", lambda *a: "")  # 回车
    assert loop._launch_wizard(5) == 5                  # CLI 显式 > env


def test_wizard_switch_course(monkeypatch):
    called = {}
    monkeypatch.setattr(loop, "_prompt_and_activate_course",
                        lambda: called.setdefault("switched", True))
    monkeypatch.setattr("builtins.input", lambda *a: "s")
    assert loop._launch_wizard(None) is None            # None → run_loop 落默认 1
    assert called.get("switched") is True
