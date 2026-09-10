"""READ-ONLY local E2E probe — login + catalog (no video, no state/ write).

Scope (per user approval):
  - Launch real local Chromium.
  - REAL login to chaoxing using .env CX_USER/CX_PASS or reuse cached session.
  - Read the REAL course catalog / chapter directory DOM (read-only discovery).
  - Do NOT play video, do NOT register course points, do NOT write state/.

Evidence written only under --out dir (docs/engineering-review/e2e_evidence).
Disposable; not part of app code path.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
import re

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root (scripts/ -> root)
sys.path.insert(0, str(ROOT))

from utils.cookie_store import ensure_login  # noqa: E402

COURSE_KEY = "265997861_151695658"
COURSE_ID = "265997861"
CLAZZ_ID = "151695658"
BASE_URL = "https://mooc1.chaoxing.com"
COURSE_URL = (f"{BASE_URL}/mooc-ans/mycourse/studentstudy"
              f"?courseId={COURSE_ID}&clazzId={CLAZZ_ID}")


def _load_env(root: pathlib.Path):
    envf = root / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "=" if "=" in line else ":"
            k, _, v = line.partition(sep)
            if k:
                os.environ.setdefault(k.strip(), v.strip())


def _is_auth_page(html: str) -> bool:
    """Detect whether the page is NOT the '用户未登录' or login warning stub."""
    return ("用户未登录" not in html and "passport2.chaoxing.com/login" not in html)


def _extract_catalog(html: str) -> dict:
    """Read-only structured extraction of the catalog (no BD writes)."""
    items = []
    # chapter/card id markers often carry chapterId in onclick/attrs
    for m in re.finditer(r"onclick=[\"']?[^\"']*(?:chapterId|dataId|coursedata"
                         r")[\"']?=([\"']?)(\d+)\1", html):
        cid = m.group(2)
        if cid not in items:
            items.append(cid)
    # titles (text between tags under catalog blocks) — crude
    titles = []
    for m in re.finditer(
        r'<!--#chapterList-->|<div[^>]*class="[^"]*(chapterName|posCatalog_name|'
        r'fl|num_tune)[^"]*"[^>]*>(.*?)</div>', html, flags=re.S | re.I
    ):
        t = re.sub(r"<[^>]+>", "", m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0))
        t = t.strip()
        if t and t not in titles:
            titles.append(t[:70])
    return {"chapter_ids": items[:30], "titles": titles[:30]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--headless", action="store_true", default=True)
    args = ap.parse_args()

    _load_env(ROOT)
    outdir = ROOT / args.out
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    cx_user = os.environ.get("CX_USER", "")
    cx_pass = os.environ.get("CX_PASS", "")
    report = {
        "scope": "read-only login + catalog (no learning/state write)",
        "created_at_utc": stamp,
        "env_runner": "local-playwright",
        "course_url": COURSE_URL,
        "creds_present": bool(cx_user) and bool(cx_pass),
        "login": {},
        "catalog": {},
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=args.headless)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/151.0.0.0 Safari/537.36"),
            )
            page = ctx.new_page()
            # attempt cookie-first login; then validate the DOM actually authed
            login_ok = ensure_login(page, ctx, COURSE_URL, cx_user, cx_pass,
                                   login_timeout_s=25)
            page.wait_for_timeout(2500)
            html0 = page.content()
            post_login_actually_authed = login_ok and _is_auth_page(html0)
            report["login"]["ok"] = login_ok
            report["login"]["actually_authed"] = post_login_actually_authed
            report["login"]["final_url"] = page.url

            if not post_login_actually_authed:
                # cached session is dead: force a fresh password login
                from utils.cookie_store import clear_cookies
                clear_cookies()
                page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                login_ok2 = ensure_login(page, ctx, COURSE_URL, cx_user, cx_pass,
                                         login_timeout_s=30)
                page.wait_for_timeout(3000)
                html0 = page.content()
                post_login_actually_authed = login_ok2 and _is_auth_page(html0)
                report["login"]["used_password_login"] = True
                report["login"]["ok"] = login_ok2
                report["login"]["actually_authed"] = post_login_actually_authed
                report["login"]["final_url"] = page.url

            (outdir / "login_result.json").write_text(
                json.dumps({"ok": report["login"]["ok"],
                            "actually_authed": post_login_actually_authed,
                            "url": page.url, "ts": stamp},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            if post_login_actually_authed:
                page.wait_for_timeout(2500)
                html = page.content()
                (outdir / "catalog.html").write_text(html, encoding="utf-8")
                report["catalog"]["html_len"] = len(html)
                report["catalog"]["parsed"] = _extract_catalog(html)
                (outdir / "catalog.json").write_text(
                    json.dumps(report["catalog"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
                try:
                    page.screenshot(path=str(outdir / "catalog.png"),
                                    full_page=True)
                    report["catalog"]["screenshot"] = True
                except Exception as e:  # noqa: BLE001
                    report["catalog"]["screenshot"] = f"ERR {e}"
            else:
                # capture the stub as evidence of a dead session
                (outdir / "not_authed.html").write_text(html0, encoding="utf-8")
                report["login"]["dead_session_evidence"] = "user not logged in"
                report["catalog"]["html_len"] = len(html0)
            browser.close()
    except Exception as e:  # noqa: BLE001
        report["error"] = repr(e)

    (outdir / "probe_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())