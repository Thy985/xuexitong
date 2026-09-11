"""列出当前超星账号课程列表（只读，不播放）。

登录后访问学习空间课程列表页，收集所有课程名与该课的学习/目录链接，
输出 courseid/clazzid/cpi/enc 与 studentstudy URL。
作用：给目标课「只给个课程名就能拿到它全部身份」，无需逐门贴 URL。
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

LIST_URLS = [
    "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse",   # mooc2 课程列表
    "https://mooc1.chaoxing.com/mycourse/",                              # mooc1 我的课程
]


def _load_env(root: Path):
    envf = root / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ("=" in line or ":" in line):
            sep = "=" if "=" in line else ":"
            k, _, v = line.partition(sep)
            os.environ.setdefault(k.strip(), v.strip())


def _qc_of(url: str) -> dict:
    try:
        q = parse_qs(urlparse(url).query)
    except Exception:
        return {}
    return {k: v[0] if v else "" for k, v in q.items()}


def _redact(u: str) -> str:
    return re.sub(r"enc=[0-9a-f]{16,}", "enc=***", u)


def main() -> int:
    _load_env(ROOT)
    if not os.environ.get("CX_USER"):
        print("缺少 CX_USER", file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright
    from utils.cookie_store import ensure_login

    found = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900},
                            user_agent=UA, ignore_https_errors=True)
        pg = ctx.new_page()
        ok = ensure_login(pg, ctx, LIST_URLS[1],
                          os.environ["CX_USER"], os.environ["CX_PASS"],
                          login_timeout_s=25, captcha_mode="auto")
        print("[login] ok=", ok, " title=", pg.title(), " onlogin=",
              "passport2.chaoxing.com/login" in pg.url)
        for lu in LIST_URLS:
            try:
                pg.goto(lu, timeout=30000)
                pg.wait_for_timeout(2500)
                html = pg.content()
            except Exception as e:
                print("[list][warn]", lu, "->", str(e)[:80])
                continue
            anchors = pg.locator(
                "a[href*='studentstudy'], a[href*='mycourse/stu'], "
                "a[href*='studentcourse']")
            n = anchors.count()
            print(f"[list] url={lu} anchors={n} html_len={len(html)}", flush=True)
            if n == 0:
                continue
            for i in range(n):
                try:
                    a = anchors.nth(i)
                    href = (a.get_attribute("href") or "").strip()
                    txt = (a.inner_text() or "").strip().replace("\n", " ")
                except Exception:
                    continue
                m = _qc_of(href)
                if m.get("courseid") or m.get("courseId") or m.get("clazzid"):
                    full = href if href.startswith("http") else ("https://mooc1.chaoxing.com" + href)
                    found.append({"title": txt[:60], "url": _redact(full)})
        seen = set()
        uniq = []
        for c in found:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            uniq.append(c)
        b.close()

    print("=== courses ===")
    print(json.dumps(uniq, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())