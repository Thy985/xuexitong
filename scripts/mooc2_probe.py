"""Read-only E2E probe against the REAL mooc2 course entry URL.

Uses the mooc2-ans /mycourse/stu URL (the one the user confirmed works).
Scope (read-only): attempt login, verify authenticated home/course catalog,
capture DOM evidence. NO video play, NO point registration, NO state/ write.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import json
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root (scripts/ -> root)
sys.path.insert(0, str(ROOT))

from utils.cookie_store import ensure_login  # noqa: E402

MOOC2_URL = ("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"
             "?courseid=265997861&clazzid=151695658"
             "&cpi=506830460&enc=<当次从登录/发现取得>&t=<当次>&pageHeader=0&v=2&hideHead=0")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def _load_env(root: pathlib.Path):
    envf = root / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ("=" in line or ":" in line):
                sep = "=" if "=" in line else ":"
                k, _, v = line.partition(sep)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    _load_env(ROOT)
    outdir = ROOT / "docs/evidence/mooc2_evidence"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rep = {
        "url": MOOC2_URL,
        "created_at_utc": stamp,
        "creds_present": bool(os.environ.get("CX_USER")) and bool(os.environ.get("CX_PASS")),
        "login": {}, "render": {},
    }
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        # cookie-first then password
        ok = ensure_login(pg, ctx, MOOC2_URL, os.environ["CX_USER"],
                          os.environ["CX_PASS"], login_timeout_s=30,
                          captcha_mode="auto")
        pg.wait_for_timeout(4000)
        rep["login"]["ok"] = ok
        rep["login"]["url"] = pg.url
        html = pg.content()
        (outdir / "page.html").write_text(html, encoding="utf-8")
        rep["render"]["html_len"] = len(html)
        title = pg.title()
        rep["render"]["title"] = title
        # markers for real course/catalog vs not-logged-in
        body = ""
        try:
            body = pg.inner_text("body")
        except Exception:
            pass
        rep["markers"] = {
            "not_logged_in": "用户未登录" in html,
            "permission_gate": "暂无权限" in html or "没有权限" in html,
            "personal_space": ("个人空间" in body) or ("空间" in html),
            "catalog_chapter": ("章节" in body) or ("目录" in body),
            "course_title_any": bool(re.search(r"课程|agora", body)),
        }
        try:
            pg.screenshot(path=str(outdir / "page.png"), full_page=True)
            rep["render"]["screenshot"] = True
        except Exception as e:
            rep["render"]["screenshot"] = f"ERR {e}"
        b.close()
    (outdir / "probe.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())