"""Throwaway diagnostic: what actually blocks local password login.
Reads DOM + detects slider; does NOT auto-retry. Evidence to stdout only.
"""
import os
import pathlib
import sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root (scripts/ -> root)
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and ("=" in line or ":" in line):
        sep = "=" if "=" in line else ":"
        k, _, v = line.partition(sep)
        os.environ.setdefault(k.strip(), v.strip())

from utils.cookie_store import clear_cookies  # noqa: E402
from utils.captcha_slider import detect_slider  # noqa: E402

clear_cookie_data = clear_cookies  # alias

COURSE_URL = ("https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudy"
          "?courseId=265997861&clazzId=151695658")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def main():
    clear_cookie_data()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA)
        pg = ctx.new_page()
        pg.goto(COURSE_URL, wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(3500)
        print("PRE url:", pg.url)
        pg.wait_for_selector("#phone", timeout=12000)
        pg.locator("#phone").first.fill(os.environ.get("CX_USER", ""))
        pg.locator("#pwd").first.fill(os.environ.get("CX_PASS", ""))
        clicked = None
        for sel in ["button:has-text('登录')", "a.loginbtn", ".loginbtn", "#login"]:
            loc = pg.locator(sel)
            if loc.count() > 0:
                loc.first.click(force=True, timeout=3000)
                clicked = sel
                break
        print("clicked:", clicked)
        slider_seen = False
        for i in range(20):
            pg.wait_for_timeout(1000)
            s = detect_slider(pg)
            if s:
                slider_seen = True
            u = pg.url
            if "passport2.chaoxing.com/login" not in u:
                print(f"POST_LOGIN at step {i} url={u}")
                break
            if i in (2, 5, 8):
                try:
                    t = pg.inner_text("body")[:160].replace("\n", " ")
                except Exception:
                    t = ""
                print(f" step{i} url={u[:80]} slider={s} text={t!r}")
        print("final_url:", pg.url)
        print("slider_seen_any:", slider_seen)
        try:
            print("body_tail:", pg.inner_text("body")[:260].replace("\n", " "))
        except Exception as e:
            print("body_err", e)
        b.close()


if __name__ == "__main__":
    main()