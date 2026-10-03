"""paths — 路径解析单点收敛（源码形态与 PyInstaller 冻结形态统一）。

全项目原先散落多处 `Path(__file__).resolve().parent.parent` 当仓库根用；
打包成 exe 后这些 `__file__` 全部落到 PyInstaller 解包目录（internal/）里，
状态文件会被写进运行时目录，升级即丢。本模块把两套形态收敛到两个语义：

  repo_root()     可写根：源码形态 = 仓库根（与历史行为一致）；
                  冻结形态 = exe 所在目录（state/ .cache/ evidence/ 都落这里）。
  resource_root() 只读资源根：源码形态 = 仓库根；
                  冻结形态 = sys._MEIPASS（随包资源，如 scripts/v3_optimized.user.js）。

冻结检测用 `getattr(sys, "frozen", False)` —— 源码形态（含 CI/GHA）永远走原
路径，保证 GitHub Actions 模式零行为变化。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结产物内。"""
    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path:
    """可写数据根：state/、.cache/、evidence/ 的挂载点。

    源码形态 = 本文件上两级（仓库根），与历史各散点的表达式等价；
    冻结形态 = exe 所在目录（onedir 产物可写、升级时随 exe 整体替换的数据
    与运行时 internal/ 分离）。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """只读随包资源根（scripts/*.user.js 等 data 文件）。

    冻结形态 = sys._MEIPASS（onedir 下即 internal/）；缺失时退回 exe 目录兜底。
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", "") or Path(sys.executable).parent)
    return repo_root()
