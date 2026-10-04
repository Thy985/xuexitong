"""runlog — 把 loop 层的控制台输出**同时**留在磁盘上。

## 为什么需要

每章子进程的 stdout 一直有落盘（`evidence/chapter_<id>.scheduler.stdout.log`，
scheduler 用 Popen 重定向），但**父进程自己**的输出没有：loop 的轮次决策、
scheduler 的调度结论、TDVP 探针的推进过程、单轮异常的 traceback，全都是
`print()` 到 stdout。exe 双击跑起来就是一个控制台窗口，用户关掉即消失。

而故障恰恰高发在这一层——选错章、熔断 NOOP、探针空转、URL 失效，都是调度层
的现象，引擎层日志一片 PASS。issue 报过来时，能拿到的证据粒度停在"这一章的
播放"，缺的正是"这一轮为什么选了它 / 为什么没选"。

装上 tee 之后，窗口里看到的和事后能查的是同一份东西。

## 边界

- 失败绝不致命：文件打不开（只读介质/权限）时静默退回纯控制台输出。
- 只在 loop 模式安装：GHA 已有自己的 job log，不需要也不该改。
- 行缓冲 + 及时 flush：崩溃时已写出的行不丢（这正是最需要它的场景）。
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

DEFAULT_LOG_NAME = "loop.log"


class _Tee(io.TextIOBase):
    """同时写控制台与文件的文本流。"""

    def __init__(self, primary, sink: "io.TextIOBase | None"):
        self._primary = primary
        self._sink = sink

    # ── io.TextIOBase 接口 ──
    def write(self, data: str) -> int:
        try:
            self._primary.write(data)
        except Exception:
            pass
        if self._sink is not None:
            try:
                self._sink.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for s in (self._primary, self._sink):
            if s is None:
                continue
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8") or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._primary, "errors", "replace") or "replace"

    def writable(self) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover - 进程退出时由 atexit 处理
        # 不关 primary：sys.stdout 由解释器持有，关掉会破坏其它使用者
        if self._sink is not None:
            try:
                self._sink.close()
            except Exception:
                pass
            self._sink = None

    def __getattr__(self, name):
        # 未显式实现的属性（buffer/encoding 之外的）透传给控制台流
        return getattr(self._primary, name)


def install_run_log(log_path: "Path | None" = None, *, name: str = DEFAULT_LOG_NAME):
    """把 stdout/stderr 接到 `<log_path>`，返回打开的文件句柄（或 None）。

    log_path 缺省时取 `repo_root()/evidence/<name>`。返回句柄交给调用方持有：
    `io.TextIOBase.__del__` 在 GC 时才析构，局部变量被回收会提前关掉日志。
    """
    if log_path is None:
        from utils.paths import repo_root
        log_path = repo_root() / "evidence" / name

    sink = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "a", encoding="utf-8", errors="replace")
    except Exception as e:  # 磁盘只读 / 权限 / 路径占用 —— 退回纯控制台
        print(f"[!] 无法写入运行日志 {log_path}（继续，仅不落盘）：{e}",
              file=sys.stderr, flush=True)
        return None

    sys.stdout = _Tee(sys.stdout, sink)
    sys.stderr = _Tee(sys.stderr, sink)
    return sink


def restore(stream) -> None:
    """还原被 tee 包裹的流（测试用；生产路径靠进程退出自然结束）。"""
    inner = getattr(stream, "_primary", None)
    if inner is not None:
        stream._sink = None
        return inner
    return stream


def read_tail(path: "Path | str", lines: int = 200) -> "list[str]":
    """读日志尾部若干行（诊断摘要用；文件不存在返回空列表）。"""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return content.splitlines()[-lines:]


def env_opt_out() -> bool:
    """XUE_LOG_FILE=0 时不落盘（调试时嫌日志碍事可关）。"""
    return (os.environ.get("XUE_LOG_FILE") or "").strip() in ("0", "false", "no")