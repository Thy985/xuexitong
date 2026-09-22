# -*- coding: utf-8 -*-
"""R5 / #20：看门狗预算必须由子进程自己报出的时长来扩展。

父进程的时长探测在结构上就输不了：它在**子进程起播之前**跑，页面那会儿根本还没有
激活的播放器。真站实测（run 35678657158 / 35684654409 与其子日志）：
    第 1 点 25s → `st={'found': True, 'currentTime': 227, 'duration': None}` → 回退静态 900s；
    第 ≥2 点先点激活再轮询 45s，但子进程解析目标 oid 约 33s + 点击→metadata 约 32s，也常不够。
后果不是理论风险：`chapter 1217304738 verdict=PASS timing_s=868.1` —— 距 900s 墙钟只剩 32s，
"已经通过却被砍成 TIMEOUT"离发生过只差一次网络抖动。

而时长这个信息其实一直在：**子进程自己会打** `Video ready: duration=1130s ...`（真 app 的 Step F）。
所以把预算的判定挪到能看见它的地方 —— 看门狗轮询时读子进程日志，读到就按同一个
`_adaptive_video_watch_s` 扩一次，读不到仍按 base 判死（不给无限续命）。
"""
import os
import pathlib
import sys

import pytest

from scheduler.scheduler import _run_one_chapter, child_reported_duration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FAKE_APP = str(REPO_ROOT / "tests" / "fixtures" / "fake_app")


# ── 解析：纯函数 ────────────────────────────────────────────────────
def test_reads_the_real_engine_line():
    text = "[03:01:07.522 UTC] Video ready: duration=1130s currentTime=198.4 paused=False\n"
    assert child_reported_duration(text) == 1130.0


def test_takes_the_last_announcement():
    """引擎会重绑/重载多次，最后一次报的才是它真正要播的那段。"""
    text = ("Video ready: duration=1062s\n"
            "Video ready: duration=1130s\n")
    assert child_reported_duration(text) == 1130.0


def test_none_when_the_child_never_announces():
    assert child_reported_duration("") is None
    assert child_reported_duration("Step F: Wait video metadata\n") is None


def test_malformed_or_incomplete_duration_is_not_trusted():
    """截断/半行的日志不能被读成时长 —— 宁可回退 base。"""
    assert child_reported_duration("Video ready: duration=s\n") is None
    assert child_reported_duration("Video ready: duration=113") is None


# ── 行为：真子进程 + 真墙钟（与 P0 看门狗回归同一套路，不 mock Popen）──
def _use_fake_app(monkeypatch, tmp_path, behavior, extra_env=None):
    monkeypatch.chdir(tmp_path)
    prev = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{FAKE_APP}{os.pathsep + prev if prev else ''}")
    monkeypatch.setenv("FAKE_RUN_BEHAVIOR", behavior)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)


def test_watchdog_extends_once_from_the_childs_own_duration(monkeypatch, tmp_path, capsys):
    """子进程宣布 6s 视频后要跑 4s；base 预算故意只给 2s —— 必须靠自报时长续命跑完。

    没有这个扩展：2s 就被 killpg，exit 124 / timed_out=True（本文件下面那条反向用例钉住）。
    """
    _use_fake_app(monkeypatch, tmp_path, "slow_announced",
                  {"FAKE_RUN_DURATION_S": "6", "FAKE_RUN_SLEEP_S": "4"})

    res = _run_one_chapter("http://x?courseId=1&clazzId=2&cpi=3",
                           "1217304701", task_id="1217304701", max_s=2)

    out = capsys.readouterr().out
    assert res["timed_out"] is False, res
    assert res["exit_code"] == 0, res
    assert "watchdog extended" in out.lower(), out


def test_without_an_announcement_the_deadline_is_unchanged(monkeypatch, tmp_path):
    """扩展只认子进程自己报的数；没报就照原墙钟判死，不给无限续命。"""
    _use_fake_app(monkeypatch, tmp_path, "slow_announced",
                  {"FAKE_RUN_ANNOUNCE": "0", "FAKE_RUN_DURATION_S": "6",
                   "FAKE_RUN_SLEEP_S": "6"})

    res = _run_one_chapter("http://x?courseId=1&clazzId=2&cpi=3",
                           "1217304701", task_id="1217304701", max_s=2)

    assert res["timed_out"] is True, res
    assert res["exit_code"] == 124, res
