"""stdio 编码加固：把输出流统一成 UTF-8。

Why: Windows 上 stdout 被重定向到文件时，Python 用系统 ANSI 代码页（本机 gbk/cp936）
建立文本流，任何非 GBK 字符（⚠️ 这类 emoji）都会让 print 抛 UnicodeEncodeError；
同一份代码在 GitHub Actions（Linux, UTF-8）永远不出事。这正是"本地行、云就行"
命题下最容易漏的一层不对称。
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional


def ensure_utf8_stdio(streams: Optional[Iterable] = None) -> None:
    """将给定流（默认 sys.stdout/sys.stderr）切到 UTF-8，容错且幂等。

    没有 reconfigure 的流（pytest capture、已包装的流）原样放过：日志加固
    永远不该成为新的崩溃源。
    """
    targets = [sys.stdout, sys.stderr] if streams is None else list(streams)
    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
