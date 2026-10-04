"""version — 构建版本戳（排障第一问：对方跑的是哪个 build）。

之前 evidence 里 `meta.github_sha` 在本地恒为 `"local"`，冻结 exe 又没有
任何自报身份的手段 —— 提 issue 的人说"播放失败"、我这边复现不了，最常见的
原因就是两边根本不是同一份代码。本模块给运行期提供一个可读回写的版本标识，
写进 evidence.meta 与诊断摘要。

两个来源，都随形态自适应：
  - `VERSION`（仓库根，随包 resource_root）：语义版本，源码形态与冻结形态同源
  - git sha：仅当 `resource_root()` 确实是 git 工作树时取（冻结形态下 _MEIPASS
    里没有 .git，直接跳过，不做任何 subprocess）

对外只有 `app_version()` / `git_sha()` / `build_info()` 三个函数，全部
不抛异常 —— 拿不到版本不该让刷课失败。
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

VERSION_FILE = "VERSION"
_FALLBACK = "0.0.0-dev"


def version_file() -> Path:
    return resource_version_root() / VERSION_FILE


def resource_version_root() -> Path:
    # resource_root()：冻结形态 = _MEIPASS（VERSION 随 datas 打进包），
    # 源码形态 = 仓库根。局部 import 避免与本模块同层的循环依赖。
    from utils.paths import resource_root
    return resource_root()


def _read_version() -> str:
    try:
        raw = version_file().read_text(encoding="utf-8").strip()
        return raw.splitlines()[0].strip() if raw else _FALLBACK
    except Exception:
        return _FALLBACK


def app_version() -> str:
    """语义版本字符串；读不到时返回 `0.0.0-dev`（不抛异常）。"""
    return _read_version()


def git_sha() -> str:
    """当前构建的 git 短 sha；非 git 工作树（如 dist/ 产物目录）返回空串。"""
    root = resource_version_root()
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = (out.stdout or "").strip()
        if out.returncode == 0 and sha:
            return sha
    except Exception:
        pass
    return ""


def build_info() -> dict:
    """诊断摘要里用的版本块。字段缺失时留空串，不猜。"""
    return {
        "app_version": app_version(),
        "git_sha": git_sha(),
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "executable": _executable_path(),
        "frozen": bool(getattr(__import__("sys"), "frozen", False)),
    }


def _executable_path() -> str:
    import sys
    try:
        return sys.executable if getattr(sys, "frozen", False) else ""
    except Exception:
        return ""