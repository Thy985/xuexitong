"""证据不得被重投覆盖；子进程自述的 verdict/归因必须原样传到汇总。

第 3 轮 M0 验证暴露的两件事（ACCEPTANCE §4.5）：

D3 证据覆盖：`_run_one_chapter` 的产物路径按 task_id 命名
    （`evidence/chapter_<task>.json` / `.scheduler.stdout.log`）。同一章被重投时**就地覆盖**
    上一轮产物 —— run1 的 708 播了 465s，run3 重投后那份日志只剩 23:20:04-31 的 5 个采样，
    我先前据 run1 日志下的结论因此不可复现。与"证据可复现"直接冲突。

D5 归因丢失：run2/run3 的 708 子进程自报 `verdict=DEGRADED`、`failure_stage=None`，
    父进程汇总却是 `FAIL` + `failure_stage=null` —— 一次失败既不知道是不是降级、
    也不知道失败在哪一段。根因不是"父进程按 exit_code 重映射"（那段覆盖代码一直在），
    而是父进程按本地码（cp936）读子进程的 UTF-8 产物，异常被静默吞掉。
"""

import json
import os
import sys
from pathlib import Path

import pytest

from scheduler.scheduler import _archive_existing, _run_one_chapter

FAKE_APP = Path(__file__).resolve().parents[1] / "fixtures" / "fake_app"
TASK = "1217304708"


@pytest.fixture
def fake_child(monkeypatch, tmp_path):
    """让子进程 `python -m app.run` 解析到 fake_app，且产物落在 tmp。"""
    monkeypatch.chdir(tmp_path)
    prev = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{FAKE_APP}{os.pathsep}{prev}")
    monkeypatch.delenv("FAKE_RUN_BEHAVIOR", raising=False)
    monkeypatch.delenv("FAKE_RUN_STAGE", raising=False)
    monkeypatch.delenv("FAKE_RUN_TITLE", raising=False)
    monkeypatch.delenv("FAKE_RUN_CORRUPT", raising=False)
    monkeypatch.setenv("XUE_VIDEO_DURATION_S", "600")   # 免开浏览器探时长
    return tmp_path


# ── D3 · 归档 ────────────────────────────────────────────────────

def test_archive_is_noop_when_file_absent(tmp_path):
    assert _archive_existing(tmp_path / "nope.json") is None


def test_archive_preserves_previous_content(tmp_path):
    p = tmp_path / "chapter_x.json"
    p.write_text('{"marker": "run1"}', encoding="utf-8")
    archived = _archive_existing(p)
    assert archived and Path(archived).exists()
    assert json.loads(Path(archived).read_text(encoding="utf-8"))["marker"] == "run1"
    assert not p.exists(), "原路径必须让位给新一轮产物"


def test_two_archives_in_the_same_second_do_not_collide(tmp_path):
    """同一秒内重投两次也不能互相抹掉。"""
    p = tmp_path / "chapter_x.json"
    kept = []
    for i in range(3):
        p.write_text(json.dumps({"marker": f"run{i}"}), encoding="utf-8")
        a = _archive_existing(p)
        assert a is not None
        kept.append(a)
    assert len(set(kept)) == 3, kept
    for i, a in enumerate(kept):
        assert json.loads(Path(a).read_text(encoding="utf-8"))["marker"] == f"run{i}"


def test_rerun_same_chapter_keeps_previous_evidence(fake_child):
    """真回归：连续两次跑同一章，第一轮的两个产物都必须还在。"""
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    first = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                             task_id=TASK, max_s=30)
    assert Path("evidence").exists()
    second = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                              task_id=TASK, max_s=30)
    archived = sorted(Path("evidence").glob("chapter_*.*"))
    assert second["verdict"] == "DEGRADED"
    assert first["timing_s"] >= 0
    # 至少两个带时间戳的归档 + 一个当前 json + 一个当前 log
    assert len([p for p in archived if ".json" in p.name]) >= 2, archived
    assert len([p for p in archived if ".log" in p.name]) >= 2, archived


# ── D5 · 归因 ────────────────────────────────────────────────────

def test_child_verdict_survives_nonzero_exit(fake_child):
    """子进程自报 DEGRADED 且 exit 1：父进程不得改写成 FAIL。"""
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    os.environ["FAKE_RUN_STAGE"] = "VIDEO_NOT_COMPLETED"
    one = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                           task_id=TASK, max_s=30)
    assert one["verdict"] == "DEGRADED", one
    assert one["failure_stage"] == "VIDEO_NOT_COMPLETED"
    assert one["passed"] is False


def test_missing_failure_stage_is_declared_not_left_blank(fake_child):
    """非 PASS 却没有 failure_stage：必须显式说明"运行期没交回失败段"，
    而不是留 null 让汇总看起来像已经归因过。"""
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    os.environ.pop("FAKE_RUN_STAGE", None)
    one = _run_one_chapter("http://x?courseId=1&clazzId=2&cpi=3", TASK,
                           task_id=TASK, max_s=30)
    assert one["verdict"] == "DEGRADED"
    assert one["failure_stage"] == "UNREPORTED_BY_RUNTIME", one


def test_pass_without_stage_stays_clean(fake_child):
    """PASS 不该被安上"未归因"标签 —— 那会把正常成功污染成异常。"""
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "PASS"
    one = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                           task_id=TASK, max_s=30)
    assert one["verdict"] == "PASS"
    assert one["failure_stage"] is None
    assert one["passed"] is True


def test_non_ascii_evidence_reaches_the_parent(fake_child):
    """真因：父进程按本地码读子进程的 UTF-8 产物。

    Windows 下 `open(path)` 用 cp936 解 UTF-8 字节 → UnicodeDecodeError，被
    `except Exception: pass` 整段吞掉，verdict 退回退出码推出的 FAIL、
    failure_stage 保持 None —— 与上面的 `test_child_verdict_survives_nonzero_exit`
    相比只多了一个中文字段，症状却和 M0 第 2/3 轮 708 的 D5 完全一致。
    这条只在 Windows 复现，是又一处本地/云不对称。
    """
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    os.environ["FAKE_RUN_STAGE"] = "VIDEO_NOT_COMPLETED"
    os.environ["FAKE_RUN_TITLE"] = "数据通信的基础知识"
    one = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                           task_id=TASK, max_s=30)
    assert one["verdict"] == "DEGRADED", one
    assert one["failure_stage"] == "VIDEO_NOT_COMPLETED", one


def test_unreadable_evidence_is_announced_not_swallowed(fake_child, capsys):
    """读产物失败必须出声。

    整段 `except Exception: pass` 是这个缺陷能活过三轮验证的原因：解码异常被
    吞掉后汇总照样是一个像模像样的 FAIL，看不出"归因根本没读到"。
    """
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    os.environ["FAKE_RUN_CORRUPT"] = "1"
    one = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                           task_id=TASK, max_s=30)
    out = capsys.readouterr().out
    assert one["verdict"] == "FAIL"          # 退回退出码推出的结论，不假装成功
    assert "chapter_1217304708.json" in out, out
    assert "产物读取失败" in out, out
    assert "JSONDecodeError" in out, out


def test_point_level_task_id_writes_a_regular_file(fake_child):
    """D10：`<cid>:video2` 的产物必须是**常规文件**。

    Windows 把 `chapter_x:video2.json` 解析成 `chapter_x` 的 NTFS 备用数据流 ——
    真站 `dir /r` 实测留下 0 字节空壳 `chapter_1217304708` 与
    `chapter_1217304708:video2.json:$DATA`，于是 `_archive_existing()` 归档的是空壳、
    D3 对点级任务整体失效。Linux 上 `:` 合法，所以只有本地会这样。
    """
    os.environ["FAKE_RUN_BEHAVIOR"] = "evidence"
    os.environ["FAKE_RUN_VERDICT"] = "DEGRADED"
    os.environ["FAKE_RUN_STAGE"] = "VIDEO_NOT_COMPLETED"
    one = _run_one_chapter("http://x?courseId=1&clazzid=2&cpi=3", TASK,
                           task_id=f"{TASK}:video2", max_s=30)
    assert one["verdict"] == "DEGRADED", one
    names = [p.name for p in Path("evidence").glob("chapter_*")]
    assert f"chapter_{TASK}_video2.json" in names, names
    assert f"chapter_{TASK}_video2.scheduler.stdout.log" in names, names
    shell = Path(f"evidence/chapter_{TASK}")
    assert not shell.exists(), f"点级产物又落进备用数据流了（留下空壳 {shell}）"
    body = json.loads((Path("evidence") / f"chapter_{TASK}_video2.json")
                      .read_text(encoding="utf-8"))
    assert body["result"]["verdict"] == "DEGRADED"
