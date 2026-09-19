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

    # ── 2. 密码登录（含滑块处理，先经 v1/manage 建权）────────────
    # 冷会话/换账号：直接 go mooc2 stu 页会被「暂无权限使用该后台，点击这里进个人空间」
    # 挡在门外（不 redirect 到 login）。因此密码登录前先 goto
    # v1.chaoxing.com/manage?ws=1 —— 未登录会重定向到 passport 登录页
    # （再去填 #phone/#pwd），已登录则放行继续。

    def _fill_and_submit(pg):
        """在登录页填 #phone/#pwd 并点登录；找不到控件则返回 False。"""
        try:
            pg.wait_for_selector("#phone", timeout=12000)
            pg.locator("#phone").first.fill(user)
            pg.locator("#pwd").first.fill(pw)
        except Exception:
            return False
        for sel in ["button:has-text('登录')", "a.loginbtn", ".loginbtn", "#login"]:
            try:
                loc = pg.locator(sel)
                if loc.count() > 0:
                    loc.first.click(force=True, timeout=3000)
                    return True
            except Exception:
                continue
        return False

    def _wait_login_done():
        """等待登录完成；期间若出现滑块验证码则尝试解决。"""
        deadline = time.monotonic() + login_timeout_s
        while time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            if "passport2.chaoxing.com/login" not in page.url:
                return
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

    try:
        # ① 先 goto v1/manage 建权
        page.goto("https://v1.chaoxing.com/manage?ws=1",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        # ② 若被未登录/权限门拦着 → 去 passport 登录
        if _is_login_warning(page):
            if "passport2.chaoxing.com/login" not in page.url:
                page.goto("https://passport2.chaoxing.com/login",
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            _fill_and_submit(page)
            _wait_login_done()

        # ③ 建权后跳回真实目标页（mooc2 stu），确认能穿权限门
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception:
        pass

    login_ok = ("passport2.chaoxing.com/login" not in page.url) \
        and not _is_login_warning(page)

    # ── 3. 登录成功后保存 cookie ─────────────────────────────
    if login_ok:
        save_cookies(context)

    return login_ok


def _is_login_warning(page) -> bool:
    """判断课程页是否其实是「未登录/无权限」错误页（非已登录视图）。

    ensure_login 旧判据只看 URL 是否在 login 页，会把『失效会话渲染
    “用户未登录”却没重定向到 login 页』（以及冷会话下直接被
    「暂无权限使用该后台，点击这里进个人空间」挡在权限门外、而非 redirect
    到 login 页）误判为已登录。此处补 DOM 校验，两类「非已登录」都算 False。
    """
    try:
        if page.is_closed():
            return False
        html = page.content()
        for bad in ("用户未登录", "暂无权限", "没有权限"):
            if bad in html:
                return True
        return False
    except Exception:
        return False
