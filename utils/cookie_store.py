"""Cookie 持久化：保存/加载登录凭证，跳过重复登录。

借鉴 Autovisor 的 cookie 管理模式：
  - 登录成功后自动保存 cookie 到 .cache/cookies.json
  - 下次启动先加载 cookie，验证是否有效
  - 无效则重新登录并更新 cookie

安全：cookie 是登录会话凭证，绝不写入 git 追踪的 state/ 目录，
仅保存在本地不可追踪的 .cache/（见 .gitignore），避免随仓库/artifact 泄露。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

# 保存在 git 不追踪的本地缓存目录，绝不入库、绝不上传 artifact
COOKIE_DIR = Path(__file__).resolve().parent.parent / ".cache"
COOKIE_FILE = COOKIE_DIR / "cookies.json"


def load_cookies() -> Optional[list[dict]]:
    """从 .cache/cookies.json 加载已保存的 cookies。"""
    if not COOKIE_FILE.exists():
        return None
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) > 0:
            return data
    except Exception:
        pass
    return None


def save_cookies(context) -> None:
    """从 Playwright BrowserContext 提取 cookies 并保存。"""
    try:
        cookies = context.cookies()
        if cookies:
            COOKIE_DIR.mkdir(parents=True, exist_ok=True)
            COOKIE_FILE.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception:
        pass


def clear_cookies() -> None:
    """删除已保存的 cookies（强制下次重新登录）。"""
    try:
        COOKIE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def ensure_login(page, context, base_url: str, user: str, pw: str,
                 login_timeout_s: int = 15,
                 captcha_mode: str = "auto",
                 captcha_attempts: int = 3) -> bool:
    """统一的登录入口：先尝试 cookie，无效则密码登录。

    Args:
        page: Playwright Page 对象
        context: Playwright BrowserContext 对象
        base_url: 登录页/课程页 URL
        user: 手机号
        pw: 密码
        login_timeout_s: 登录等待超时（秒）
        captcha_mode: "auto"(默认) 自动滑块；"manual" 等人工；"auto_then_manual" 先自动后人工；
                    "skip" 完全跳过滑块处理（维持旧行为）。
        captcha_attempts: captcha_mode 为 auto 时的自动拖拽次数。

    Returns:
        True 如果登录成功
    """
    from utils.captcha_slider import detect_slider, solve_slider, wait_manual

    # 读取可选环境变量覆盖（便于 CI/脚本控制）
    if captcha_mode == "auto":
        captcha_mode = os.environ.get("XUE_CAPTCHA_MODE", "auto")

    # ── 1. 尝试 cookie 登录 ──────────────────────────────────
    cookies = load_cookies()
    if cookies:
        try:
            context.add_cookies(cookies)
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            if "passport2.chaoxing.com/login" not in page.url \
                    and not _is_login_warning(page):
                # cookie 有效，跳过登录（额外校验非"用户未登录")
                return True
            # cookie 过期，清除并重新登录
            clear_cookies()
        except Exception:
            pass

    # ── 2. 密码登录（含滑块处理）──────────────────────────────
    page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    try:
        page.wait_for_selector("#phone", timeout=12000)
        page.locator("#phone").first.fill(user)
        page.locator("#pwd").first.fill(pw)
        for sel in ["button:has-text('登录')", "a.loginbtn", ".loginbtn", "#login"]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.click(force=True, timeout=3000)
                    break
            except Exception:
                pass

        # 循环等待登录结果；期间若出现滑块验证码则尝试解决
        deadline = time.monotonic() + login_timeout_s
        while time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            if "passport2.chaoxing.com/login" not in page.url:
                break
            if captcha_mode != "skip" and detect_slider(page):
                if captcha_mode in ("auto", "auto_then_manual"):
                    res = solve_slider(page, attempts=captcha_attempts,
                                       mode="auto")
                elif captcha_mode == "manual":
                    res = "manual_needed"
                else:  # e.g. "auto_then_manual" 兜底
                    res = solve_slider(page, attempts=captcha_attempts,
                                       mode="auto")
                if res == "manual_needed":
                    wait_manual(page, timeout_s=60.0)
                    page.wait_for_timeout(800)
    except Exception:
        pass

    login_ok = ("passport2.chaoxing.com/login" not in page.url) \
        and not _is_login_warning(page)

    # ── 3. 登录成功后保存 cookie ─────────────────────────────
    if login_ok:
        save_cookies(context)

    return login_ok


def _is_login_warning(page) -> bool:
    """判断课程页是否其实是「用户未登录」错误页（非已登录视图）。

    ensure_login 旧判据只看 URL 是否在 login 页，这会把『失效会话渲染
    “用户未登录”却没重定向到 login 页』误判为已登录。此处补 DOM 校验。
    """
    try:
        if page.is_closed():
            return False
        html = page.content()
        return "用户未登录" in html
    except Exception:
        return False
