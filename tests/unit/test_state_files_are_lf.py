# -*- coding: utf-8 -*-
"""`state/` 下被 git 跟踪的文件必须逐字节与平台无关 —— 本机落盘不许写入 CR。

事故（2026-09-22，批量收幻影那一晚）：`save_registry` 之后 `git diff --stat` 报
`3945 insertions / 4195 deletions`，而真实内容改动是 **250 行删除、0 行新增**。
`_atomic_write_text` 走 `write_text(text, encoding=…)` 而不带 `newline`，Windows 的文本
模式于是把每个 `\n` 翻成 `\r\n`；仓库里跟踪的 `state/registry/<key>/tasks.json` 是 CI
（Linux）写的 LF 文件。一次普通落盘把整本账变成"全文件重写"，真改动被淹掉 —— 审查者
无法分辨"改了账"和"翻了 EOL"，而这本账是跨 Run 的唯一真源。

测试怎么做到在 Linux 上也变红：翻译是 C 运行库的行为，本机实测（`os.linesep` 改不掉它，
`newline="\\n"` 才有效），所以这里把 Windows 的默认语义**模拟**进这两个写入口
（`Path.write_text` / `os.fdopen`）：调用方没显式给 `newline` 时按 `\\r\\n` 翻译。
这样漏写 `newline` 的实现在任何平台上都会立刻产出 CR。
"""
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app.registry.task_registry as tr                     # noqa: E402
import state.course_state as cs                              # noqa: E402
from models import CourseIdentity                            # noqa: E402

KEY = "265997861_151695658"


@pytest.fixture
def windows_text_mode(monkeypatch):
    """让"忘记传 newline"在 Linux 上也算错：按 Windows 默认语义翻译换行。"""
    real_write_text = pathlib.Path.write_text
    real_fdopen = os.fdopen

    def winish_write_text(self, data, encoding=None, errors=None, newline=None):
        if newline is None:
            # 自己翻译完就以 newline="" 交出，否则真实写入会再翻译一次（\r\r\n）。
            data = data.replace("\n", "\r\n")
            newline = ""
        return real_write_text(self, data, encoding=encoding, errors=errors,
                               newline=newline)

    def winish_fdopen(fd, mode="r", buffering=-1, **kw):
        if "w" in mode and kw.get("newline") is None:
            kw["newline"] = "\r\n"
        return real_fdopen(fd, mode, buffering, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", winish_write_text)
    monkeypatch.setattr(os, "fdopen", winish_fdopen)
    return monkeypatch


@pytest.fixture
def tmp_ledger(tmp_path, windows_text_mode):
    """账本与课程状态都落在 tmp —— 跑在真 worktree 里会写坏真账。"""
    windows_text_mode.setattr(tr, "TASKS_DIR", tmp_path / "state" / "registry")
    windows_text_mode.setattr(cs, "STATE_DIR", tmp_path / "state")
    windows_text_mode.setattr(cs, "COURSES_DIR", tmp_path / "state" / "courses")
    windows_text_mode.setattr(cs, "ACTIVE_FILE", tmp_path / "state" / "active_course.json")
    return tmp_path


def _identity():
    return CourseIdentity(course_id="265997861", clazz_id="151695658", cpi="506830460",
                          title="course_265997861", raw_url="https://mooc1.chaoxing.com/x",
                          resolved_at_utc="2026-09-03T13:36:56+00:00")


def test_atomic_writer_keeps_the_text_byte_for_byte(tmp_ledger):
    target = tmp_ledger / "state" / "registry" / "tasks.json"
    tr._atomic_write_text(target, '{\n  "a": 1\n}\n')
    assert target.read_bytes() == b'{\n  "a": 1\n}\n'


def test_save_registry_writes_no_carriage_returns(tmp_ledger):
    reg = {
        "1217304708": tr.TaskRecord("1217304708", "1217304708", "数据链路层"),
        "1217304708:video2": tr.TaskRecord("1217304708:video2", "1217304708",
                                           "数据链路层", task_type="video"),
    }
    tr.save_registry(KEY, reg)
    f = tmp_ledger / "state" / "registry" / KEY / "tasks.json"
    raw = f.read_bytes()
    assert b"\r" not in raw, "账本被平台换行污染：git diff 会变成整文件重写"
    assert json.loads(raw.decode("utf-8"))["1217304708:video2"]["chapter_id"] == "1217304708"


def test_queue_and_snapshot_writers_are_too(tmp_ledger):
    tr.save_queue(KEY, tr.ExecutionQueue(items=[{"task_id": "1217304708", "chapter_id":
                                                "1217304708", "priority": 0}]))
    tr.set_chapter_point_snapshot(KEY, "1217304708", video_total=2, video_finished=1,
                                  has_video=True)
    d = tmp_ledger / "state" / "registry" / KEY
    for name in ("execution_queue.json", "chapter_points.json"):
        assert b"\r" not in (d / name).read_bytes(), name


def test_course_state_file_has_no_carriage_returns(tmp_ledger):
    cs.save_course_state(cs.CourseState(course_identity=_identity()))
    f = tmp_ledger / "state" / "courses" / f"{KEY}.json"
    assert f.exists(), "课程状态没落到 tmp —— fixture 漏了一个目录常量"
    assert b"\r" not in f.read_bytes()
