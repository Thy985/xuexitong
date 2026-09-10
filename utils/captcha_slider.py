"""滑块验证码（geetest / 学习通新版）处理。

目标：在 `ensure_login` 密码登录被滑块验证码拦截时，提供处理能力：
  1. 检测当前页面是否出现滑块验证码（登录提交后动态出现）。
  2. 自动拖拽求解（auto）：尽力而为；无图像缺口识别时用面板宽比率估算距离，
     配合多次尝试。不 100% 保证命中（轨迹/时限风控）。
  3. 人工模式（manual）：headless/headed 均适用 —— 等在自动失败后切换到人工，
     或在 headed 浏览器里让用户拖一下。
  4. 始终诚实：不做无上限重试（避免触发风控），建议把有效 cookie 作为稳定路径。

不依赖 PIL；如需精确缺口识别，由调用方截图后自行算 offset，经 solve_slider 的距离参数传入。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("xuexitong.captcha")

# 常见 geetest 类名（v3/v4 的典型命名）
SLIDER_BUTTON_SELECTORS = [
    ".geetest_slider_button",
    ".geetest_holder .geetest_slider",
    ".geetest_holder .geetest_wind .geetest_slider_button",
]
TRACK_SELECTORS = [
    ".geetest_slider_track",
    ".geetest_holder .geetest_slider_wrap",
    ".geetest_slider",
    ".geetest_walk_bar",
]
PANEL_SELECTORS = [
    ".geetest_panel",
    ".geetest_wind",
    ".geetest_holder",
    "#captcha-container",
]
TEXT_HINTS = ["滑块验证", "拖动滑块", "请完成验证", "向右滑动", "geetest", "captcha"]


def detect_slider(page) -> bool:
    """检测当前页面出现滑块验证码 widget。"""
    try:
        for sel in SLIDER_BUTTON_SELECTORS + PANEL_SELECTORS:
            try:
                if page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        # 文本级探测
        text = ""
        try:
            if not page.is_closed():
                text = page.inner_text("body") or ""
        except Exception:
            pass
        low = text.lower()
        for hint in TEXT_HINTS:
            if hint.lower() in low:
                return True
    except Exception as exc:  # noqa: BLE001
        log.debug("detect_slider error: %r", exc)
    return False


def _slider_button(page):
    for sel in SLIDER_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _track_width(page) -> Optional[int]:
    """取得滑块可拖拽轨道的宽，用于估算缺口位移。

    优先取轨道本身（track），避免取到全宽的外层容器导致位移估算过大。
    """
    for sel in TRACK_SELECTORS + PANEL_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                box = loc.bounding_box()
                if box and box.get("width"):
                    w = int(box["width"])
                    # 太宽（比如~视口全宽）当作外层容器，跳过以免位移过大
                    if w < 700:
                        return w
        except Exception:
            continue
    return None


def _slide_distance(page) -> int:
    """估算滑块拖拽距离。

    无精确缺口感知时，粗略为轨道宽 * 0.75；调用方可经 solve_slider
    的 drag_override 传入精确的缺口像素偏置。
    """
    w = _track_width(page)
    return int((w or 300) * 0.75)


def save_captcha(pg, path: str) -> None:
    """保存 captcha 面板截图（可选，用于证据/人工参照）。"""
    try:
        loc = pg.locator(".geetest_panel, .geetest_wind").first
        if loc.count():
            loc.screenshot(path=path)
        else:
            pg.screenshot(path=path)
    except Exception as exc:  # noqa: BLE001
        log.debug("save_captcha err: %r", exc)


def _drag_once(page, distance: int) -> bool:
    """执行一次拖拽，返回滑块是否被接受（widget 消失）。"""
    btn = _slider_button(page)
    if btn is None:
        return False
    box = btn.bounding_box()
    if not box:
        return False
    x0 = box["x"] + box["width"] / 2
    y0 = box["y"] + box["height"] / 2
    page.mouse.move(x0, y0, steps=1)
    page.mouse.down()
    # 分段移动，弱化机械感
    steps = 12
    chunk = max(3, distance / steps)
    cur = x0
    for _ in range(steps):
        cur -= chunk
        cur = max(x0 - distance, cur)
        page.mouse.move(cur, y0, steps=2)
        page.wait_for_timeout(18)
    page.mouse.up()
    page.wait_for_timeout(700)
    return not detect_slider(page)


def solve_slider(
    page,
    attempts: int = 3,
    mode: str = "auto",
    save_path: Optional[str] = None,
    drag_override: Optional[int] = None,
) -> str:
    """主入口。

    Returns:
      "no_captcha"  当前页没有滑块
      "solved"      已解决（滑动成功，widget 消失）
      "failed"      自动尝试后仍未过
      "manual_needed"  需要人工（mode 含 manual）
    """
    if not detect_slider(page):
        return "no_captcha"
    if save_path:
        save_captcha(page, save_path)

    solved = False
    for i in range(1, attempts + 1):
        log.info("滑块尝试 %d/%d", i, attempts)
        dist = drag_override if drag_override is not None else _slide_distance(page)
        if _drag_once(page, dist):
            solved = True
            break
        page.wait_for_timeout(600)

    if solved:
        return "solved"
    if mode in ("manual", "auto_then_manual"):
        return "manual_needed"
    return "failed"


def wait_manual(page, timeout_s: float = 60.0) -> str:
    """等待人工完成滑块（headed）。人工完成后滑块 widget 应消失。"""
    t0 = time.time()
    while True:
        try:
            gone = not detect_slider(page)
        except Exception:
            gone = False
        if gone:
            return "solved"
        if time.time() - t0 > timeout_s:
            return "timeout"
        page.wait_for_timeout(700)