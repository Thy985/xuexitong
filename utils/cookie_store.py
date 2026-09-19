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
            html = _page_html(page)
            if "passport2.chaoxing.com/login" not in page.url \
                    and not is_unauthenticated(html):
                # 会话有效。注意「暂无权限使用该后台」也算已登录 —— 旧判据把它
                # 一并当作失效，于是清掉好 cookie 再重登一次，仍撞同一道门。
                if not is_permission_gate(html):
                    return True
                if _enter_personal_space(page):
                    page.goto(base_url, wait_until="domcontentloaded",
                              timeout=30000)
                    page.wait_for_timeout(2500)
                    if not _is_login_warning(page):
                        save_cookies(context)
                        return True
            # cookie 过期，清除并重新登录
            clear_cookies()
        except Exception:
            pass

    # ── 2. 密码登录（含滑块处理）──────────────────────────────
    # 冷会话下直接 goto mooc2 stu 页可能不 redirect 到 login，而是渲染
    # 「暂无权限使用该后台，点击这里进个人空间」——那是**已登录**的权限门，
    # 要的是点那个链接（见 is_permission_gate / _enter_personal_space），
    # 不是再登一次。旧实现误把它当未登录并 goto 裸 login URL（丢 refer），
    # 结果登录后无返回上下文、卡在登录页。

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
        # ① goto 目标页：未登录时它会带 refer 重定向到 passport 登录页，登录成功后
        #    自动回到目标页。实测反例：裸 goto passport2/login（不带 refer）→ 登录后
        #    没有返回上下文，卡在登录页，login_ok 恒 False。
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        # ② 停在登录页才填表提交（权限门不是"未登录"，不再触发重登）
        if "passport2.chaoxing.com/login" in page.url:
            _fill_and_submit(page)
            _wait_login_done()

        # ③ 已登录但落在 manage 权限门 → 点「点击这里进个人空间」建立空间上下文
        if is_permission_gate(_page_html(page)):
            _enter_personal_space(page)

        # ④ 跳回真实目标页（mooc2 stu），确认能穿权限门
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


def is_permission_gate(html: "str|None") -> bool:
    """「已登录但被 manage 权限门挡住」——需点『进个人空间』，**不是**未登录。

    实测：登录后 v1.chaoxing.com/manage 渲染
    「<姓名> 您暂无权限使用该后台，点击这里进个人空间」，链接 href=i.chaoxing.com。
    """
    if not html:
        return False
    return "暂无权限使用该后台" in html or "点击这里进个人空间" in html


def is_unauthenticated(html: "str|None") -> bool:
    """真·未登录（页面显式说"用户未登录"）。与 is_permission_gate 互斥。"""
    return bool(html) and "用户未登录" in html


# 权限门页上那个链接的实测特征（href 优先，文案兜底）。
PERSONAL_SPACE_SELECTORS = [
    "a[href='https://i.chaoxing.com']",
    "a:has-text('点击这里进个人空间')",
    "a:has-text('个人空间')",
]


def _page_html(page) -> str:
    try:
        return page.content() if not page.is_closed() else ""
    except Exception:
        return ""


def _enter_personal_space(page) -> bool:
    """点权限门上的「点击这里进个人空间」，为会话建立个人空间上下文。"""
    for sel in PERSONAL_SPACE_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False


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
