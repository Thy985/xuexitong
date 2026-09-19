"""时长探测不得在无凭据时开浏览器；测试会话不得带真凭据。

回归背景（用户可见故障）：本地跑 `pytest tests/unit tests/regression` 时桌面反复
弹出 passport2 登录页，停在登录页不动、过一会儿消失、再弹。链路是
    测试 → run_scheduler → _probe_video_duration_s → 真起 headed Chromium
    + ensure_login(无 CX_USER/CX_PASS) → 卡在登录页直到内部超时
即：一个"尽力而为"的探测在没有凭据时照样开浏览器，而且跑在**父进程**里，
不受 per-chapter 看门狗约束。测试因此既慢（一次弹窗几十秒）又偷偷打真站。
"""

import os

import pytest

from scheduler.scheduler import _probe_video_duration_s

CREDS = ("CX_USER", "CX_PASS")


@pytest.fixture
def no_creds(monkeypatch):
    for key in CREDS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("XUE_VIDEO_DURATION_S", raising=False)


def _forbid_playwright(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("测试内不得启动浏览器")

    monkeypatch.setattr("playwright.sync_api.sync_playwright", boom)


def test_probe_skips_browser_when_credentials_missing(no_creds, monkeypatch):
    _forbid_playwright(monkeypatch)
    dur, err = _probe_video_duration_s(
        "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304702",
        "1217304702")
    assert dur is None
    assert err and "凭据" in err, f"要给出可诊断的原因，实际 {err!r}"


def test_probe_skips_browser_without_chapter_id(monkeypatch):
    _forbid_playwright(monkeypatch)
    dur, err = _probe_video_duration_s("http://x", "")
    assert dur is None and err


def test_probe_still_short_circuits_on_env_duration(no_creds, monkeypatch):
    """显式给定时长时依旧零成本 —— 加固不能把快路径堵掉。"""
    _forbid_playwright(monkeypatch)
    monkeypatch.setenv("XUE_VIDEO_DURATION_S", "846")
    assert _probe_video_duration_s("http://x?chapterId=1", "1") == (846.0, None)


def test_test_session_carries_no_real_credentials():
    """conftest 必须剥掉真凭据：否则单测会拿真实账号打真站。"""
    assert not os.environ.get("CX_USER"), "测试会话不应带 CX_USER"
    assert not os.environ.get("CX_PASS"), "测试会话不应带 CX_PASS"
