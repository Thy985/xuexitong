"""Real mooc2 capture of course catalog DOM + network, for tests/fixtures.

Read-only scope: login (cookie-first/password) -> course page -> the 章节
(zj) catalog page /-> mooc1 studentstudy learning page (real #coursetree).

Captures (raw, NOT yet sanitized) into <fixtures>/_raw_capture/:
  - course_page_stu.html   : /mycourse/stu shell (top tabs)
  - dom_catalog_list.html  : /mycourse/studentcourse (the .catalog_* list)
  - dom_learning_tree.html : the mooc1 studentstudy learning page (has #coursetree),
                             i.e. the DOM the tvdp extractor actually consumes
plus the network XHR bodies for catalog page.
NO progress/point write, NO production state write.
"""
from __future__ import annotations

import os
import pathlib
import re
import json
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlparse

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.cookie_store import ensure_login  # noqa: E402

COURSE_ID = "265997861"
CLAZZ_ID = "151695658"
CPI = "506830460"
STU_URL = ("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"
           "?courseid=" + COURSE_ID + "&clazzid=" + CLAZZ_ID +
           "&cpi=" + CPI + "&pageHeader=0&v=2&hideHead=0")
STUDENT_COURSE = ("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
                  "?courseid=" + COURSE_ID + "&clazzid=" + CLAZZ_ID + "&cpi=" + CPI)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def _load_env(root: pathlib.Path):
    envf = root / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ("=" in line or ":" in line):
            sep = "=" if "=" in line else ":"
            k, _, v = line.partition(sep)
            os.environ.setdefault(k.strip(), v.strip())


def _slug(u: str) -> str:
    from urllib.parse import parse_qsl, urlparse
    p = urlparse(u)
    q = dict(parse_qsl(p.query))
    keep = {}
    for s in ("chapterId", "courseid", "courseId", "clazzid", "cpi", "enc"):
        if s in q:
            keep[s] = q[s][:24]
    name = p.path.split("/")[-1] or p.path.rstrip("/").split("/")[-1]
    return name + "?" + "&".join(f"{k}={v}" for k, v in keep.items())


def main() -> int:
    _load_env(ROOT)
    out = ROOT / "tests/fixtures/_raw_capture"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log: dict = {"ts": stamp, "creds": bool(os.getenv("CX_USER")), "urls": [], "notes": []}

    captured = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()

        def _on_resp(resp):
            if not any(k in resp.url for k in ("studentcourse", "studentstudy")):
                return
            ct = resp.headers.get("content-type", "")
            if not any(s in ct for s in ("html",)):
                return
            try:
                body = resp.text()
            except Exception:
                return
            if body.strip():
                captured.append({"url": resp.url, "status": resp.status,
                                 "content_type": ct, "body_len": len(body), "body": body})

        pg.on("response", _on_resp)

        ok = ensure_login(pg, ctx, STUDENT_COURSE, os.environ["CX_USER"], os.environ["CX_PASS"],
                          login_timeout_s=30, captcha_mode="auto")
        log["login_ok"] = bool(ok)
        pg.wait_for_timeout(3500)
        # catalog list page (real .catalog_* grammar)
        pg.goto(STUDENT_COURSE)
        pg.wait_for_timeout(3500)
        catalog_html = pg.content()
        (out / "dom_catalog_list.html").write_text(catalog_html, encoding="utf-8")
        log["urls"].append(_slug(pg.url))

        # find first real chapter knowledgeId to enter the mooc1 learning page
        m = re.search(r"toOld\('([^']+)',\s*'(\d+)',\s*'([^']+)'", catalog_html)
        if not m:
            log["notes"].append("no toOld() first chapter found in catalog")
        else:
            courseid, krowid, clazzid = m.group(1), m.group(2), m.group(3)
            # grab real enc from the hidden input / ServerHost in the page
            em = re.search(r'enc"\s*=\s*"([0-9a-f]{16,})', catalog_html) \
                or re.search(r'id="enc"\s+value="([0-9a-f]{16,})', catalog_html)
            enc = em.group(1) if em else ""
            mooc1 = "https://mooc1.chaoxing.com"
            learn_url = (mooc1 + "/mycourse/studentstudy?chapterId=" + krowid +
                         "&courseId=" + courseid + "&clazzid=" + clazzid +
                         "&cpi=" + CPI + ("&enc=" + enc if enc else "") + "&mooc2=1")
            log["notes"].append(f"enter chapter {krowid}")
            try:
                pg.goto(learn_url)
                pg.wait_for_timeout(3500)
                learn_html = pg.content()
                (out / "dom_learning_tree.html").write_text(learn_html, encoding="utf-8")
                log["urls"].append(_slug(pg.url))
            except Exception as e:
                log["learn_err"] = str(e)
        pg.wait_for_timeout(500)
        b.close()

    (out / "manifest.json").write_text(json.dumps(log, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps({"ts": log["ts"], "login_ok": log.get("login_ok"),
                      "urls": log["urls"], "notes": log["notes"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())