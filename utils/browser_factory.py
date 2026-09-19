"""browser_factory — xuexitong 可配浏览器启动（参照 Autovi 的 EXE_PATH/driver 思路）

痛点：代码里 12+ 处 `chromium.launch(channel="chromium")` 把浏览器锁死在 Playwright
内置 build（如 chromium-1208）。当某环境的 ms-playwright 缓存缺对应 build，
或想借用系统已装浏览器（避免 `playwright install`），就无路可走。

本模块提供**单点可配层**：通过环境变量指定浏览器，各处 launch 改用
`browser_launch(...)`，不再写死 channel。

环境变量（都可省略，默认 `channel="chromium"` = 现行为，零侵入）：
  - `XUE_BROWSER_EXE`      → 精确到 chrome.exe / msedge.exe 的路径（优先级最高）
  - `XUE_BROWSER_CHANNEL`  → Playwright channel：chrome | msedge | chromium | ...（次优先）
两者都不设 → 默认 chromium（GHA/CI 行为不变）。

用法（同步/异步 Playwright 通用）：
    from utils.browser_factory import launch_kwargs
    browser = p.chromium.launch(**launch_kwargs(headless=False, args=[...]))
或直接：
    from utils.browser_factory import launch_browser
    browser = launch_browser(browser_type=p.chromium, headless=False, args=[...])

注意：executable_path / channel 由启动器解析，sync 与 async 的 chromium.launch 签名一致，
因此同一 kwargs 两者可用。
"""

from __future__ import annotations

import os
from typing import Any

# Playwright 合法 channel 可接受 chromium（内置）/ chrome / msedge 等，按平台而定
_VALID_CHANNELS = {"chromium", "chrome", "msedge", "edge"}


def _resolve_browser() -> dict[str, str]:
    """解析可配浏览器（本模块单一来源）。返回可直接进 launch 的 key。"""
    exe = (os.environ.get("XUE_BROWSER_EXE") or "").strip()
    channel = (os.environ.get("XUE_BROWSER_CHANNEL") or "").strip().lower()
    if exe:
        return {"executable_path": exe}
    if channel:
        if channel not in _VALID_CHANNELS:
            # 不静默——未知 channel 直接报错，避免"看起来换了浏览器其实没换"
            raise ValueError(
                f"XUE_BROWSER_CHANNEL 值 '{channel}' 不被支持; "
                f"可用: {', '.join(sorted(_VALID_CHANNELS))}"
            )
        return {"channel": channel}
    return {"channel": "chromium"}


def launch_kwargs(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回给 chromium.launch(**kwargs) 的参数。

    - 首先看环境变量解析浏览器；用户显式传的同名 key（变 attitude `overrides`）优先。
    - 默认 `{"channel": "chromium"}`。
    """
    kwargs: dict[str, Any] = _resolve_browser()
    if overrides:
        kwargs.update(overrides)
        # overrides 里若又给了 executable_path/channel，且我们仍依赖内置 build，覆盖即可
    return kwargs


def launch_browser(browser_type, **overrides):
    """便捷封装：browser = launch_browser(browser_type=p.chromium, headless=False, ...)"""
    return browser_type.launch(**launch_kwargs(overrides))