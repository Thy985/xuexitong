"""Cookie 持久化：保存/加载登录凭证，跳过重复登录。

借鉴 Autovisor 的 cookie 管理模式：
  - 登录成功后自动保存 cookie 到 state/cookies.json
  - 下次启动先加载 cookie，验证是否有效
  - 无效则重新登录并更新 cookie
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

COOKIE_DIR = Path("state")
COOKIE_FILE = COOKIE_DIR / "cookies.json"


def load_cookies() -> Optional[list[dict]]:
    """从 state/cookies.json 加载已保存的 cookies。"""
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
                 login_timeout_s: int = 15) -> bool:
    """统一的登录入口：先尝试 cookie，无效则密码登录。

    Args:
        page: Playwright Page 对象
        context: Playwright BrowserContext 对象
        base_url: 登录页/课程页 URL
        user: 手机号
        pw: 密码
        login_timeout_s: 登录等待超时（秒）

    Returns:
        True 如果登录成功
    """
    # ── 1. 尝试 cookie 登录 ──────────────────────────────────
    cookies = load_cookies()
    if cookies:
        try:
            context.add_cookies(cookies)
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            if "passport2.chaoxing.com/login" not in page.url:
                # cookie 有效，跳过登录
                return True
            # cookie 过期，清除并重新登录
            clear_cookies()
        except Exception:
            pass

    # ── 2. 密码登录 ──────────────────────────────────────────
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
        for _ in range(login_timeout_s):
            page.wait_for_timeout(1000)
            if "passport2.chaoxing.com/login" not in page.url:
                break
    except Exception:
        pass

    login_ok = "passport2.chaoxing.com/login" not in page.url

    # ── 3. 登录成功后保存 cookie ─────────────────────────────
    if login_ok:
        save_cookies(context)

    return login_ok
