"""父进程 stdout 必须是 UTF-8，否则 Windows 重定向到文件时用 gbk 编码。

回归背景：local-1789828075/118/154 三次 scheduler 全部 SCHEDULER_CRASH，
error = "UnicodeEncodeError: 'gbk' codec can't encode character '\\u26a0'"，
崩在 scheduler.py 的看门狗降级日志行。子进程入口 app/run.py 早有同款加固，
父进程没有 —— 两个进程的日志编码不对称。
"""

import io

import pytest

from utils.stdio_utf8 import ensure_utf8_stdio


def _gbk_stream():
    buf = io.BytesIO()
    return buf, io.TextIOWrapper(buf, encoding="gbk", errors="strict", newline="")


def test_gbk_stream_raises_without_guard():
    """先证明故障真实存在：未加固的 gbk 流打 ⚠️ 会抛。"""
    _, stream = _gbk_stream()
    with pytest.raises(UnicodeEncodeError):
        print("⚠️ 看门狗降级", file=stream, flush=True)


def test_ensure_utf8_stdio_makes_stream_utf8():
    buf, stream = _gbk_stream()
    ensure_utf8_stdio([stream])
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_ensure_utf8_stdio_keeps_stream_identity_and_bytes_utf8():
    buf, stream = _gbk_stream()
    ensure_utf8_stdio([stream])
    print("⚠️ 看门狗降级 chapter=754", file=stream, flush=True)
    assert buf.getvalue().decode("utf-8") == "⚠️ 看门狗降级 chapter=754\n"


def test_ensure_utf8_stdio_survives_stream_without_reconfigure():
    class Bare:
        def write(self, data):
            return len(data)

        def flush(self):
            pass

    ensure_utf8_stdio([Bare()])  # 不抛即通过


def test_ensure_utf8_stdio_is_idempotent():
    _, stream = _gbk_stream()
    ensure_utf8_stdio([stream])
    ensure_utf8_stdio([stream])
    print("⚠️", file=stream, flush=True)


def test_watchdog_fallback_reason_prints_on_gbk_stream():
    """钉住真实崩溃点：看门狗降级那行日志在 gbk 控制台下也必须能打出来。"""
    from scheduler.scheduler import video_watch_budget

    _, reason = video_watch_budget(900, None, "探测未返回时长")
    assert reason.startswith("fallback")
    _, stream = _gbk_stream()
    ensure_utf8_stdio([stream])
    print(f"[scheduler] ⚠️ 看门狗降级 chapter=754 {reason}", file=stream, flush=True)
    stream.flush()
