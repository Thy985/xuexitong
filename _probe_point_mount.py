# -*- coding: utf-8 -*-
"""READ-ONLY probe: how many video job points does chapter 1217304730 actually mount?

Why: run 35681460999 dispatched `1217304730:video2` off the natural queue and died in
15.9s with `FAIL(target video point 2 not on page; points=1)` -- the engine's own
`enumerate_video_objectids` (app/e2_headed_gha.py:358) saw a single point, and the code
at app/e2_headed_gha.py:824-833 treats that as a terminal verdict without reloading.

Two explanations are still alive and they need DIFFERENT fixes:
  (a) lazy mount: point 2 attaches to the DOM only later (or only after point 1 is
      activated) -> Step F needs a reload/re-enumerate recovery like metadata already has;
  (b) phantom record: the server really exposes 1 video point and the ledger's `:video2`
      is an enumeration artifact (the D13 family) -> the record should be cleaned.

This probe touches nothing: no clicks, no seek, no play(), no state writes, no v3.
It samples the DOM the way the engine reads it, over a 90s window, then asks the
server-side point reader for the same chapter and compares.
"""
import os
import pathlib
import sys
import time

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

from resolvers.course_resolver import _parse_url_params
from tvdp.tdvp import _tdvp_course_params, read_chapter_job_points
from app.e2_headed_gha import build_base_url, enumerate_video_objectids
from playwright.sync_api import sync_playwright

CID = os.environ.get("PROBE_CID", "1217304730")
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=" + CID +
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

log = print

JS_CARDS = """() => {
    const sel = '.ans-insertvideo-online[objectid]';
    const cards = [...document.querySelectorAll(sel)];
    return {
        markers: cards.map(e => (e.getAttribute('objectid') || '').slice(0, 8)),
        videos: [...document.querySelectorAll('video')].length,
        job_icons: [...document.querySelectorAll('[class*="ans-job"]')]
            .map(e => e.className).slice(0, 12),
    };
}"""


def sample(page, label):
    """(engine view, DOM view) for every frame -- the engine reads the former."""
    oids = []
    dom = None
    for fr in page.frames:
        try:
            got = fr.evaluate(JS_CARDS)
        except Exception:
            continue
        if not got:
            continue
        if got.get("markers"):
            dom = got
        oids.extend(got.get("markers") or [])
    engine = []
    try:
        engine = enumerate_video_objectids(page)
    except Exception as e:
        log(f"[{label}] enumerate_video_objectids raised {type(e).__name__}: {e}")
    log(f"[{label}] engine={len(engine)} {[o[:8] for o in engine]} "
        f"| dom_markers={len(oids)} {[o[:8] for o in oids]} "
        f"| videos={None if dom is None else dom.get('videos')} "
        f"| frames={len(page.frames)}")
    return engine


def main():
    cp = _tdvp_course_params(_parse_url_params(COURSE_URL))
    base = build_base_url(CID, cp)
    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        from utils.cookie_store import ensure_login
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            ignore_https_errors=True)
        page = ctx.new_page()
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(base, wait_until="domcontentloaded", timeout=30000)

        for wait_ms, label in ((0, "t=0s"), (3000, "t=3s"), (5000, "t=8s"),
                               (7000, "t=15s"), (15000, "t=30s"), (15000, "t=45s"),
                               (15000, "t=60s"), (30000, "t=90s")):
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            sample(page, label)

        log(f"[scroll] scrolling to bottom to give lazy mount every chance")
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(5000)
        except Exception as e:
            log(f"[scroll] failed {type(e).__name__}: {e}")
        sample(page, "after-scroll")

        log("[server] asking the live point reader for the same chapter")
        try:
            pts = read_chapter_job_points(page, CID, cp.course_id, cp.clazz_id, cp.cpi)
            vids = [p for p in pts if (p or {}).get("type") == "video"]
            for pt in pts:
                log(f"[server] {pt.get('task_id')} type={pt.get('type')} "
                    f"finished={pt.get('finished')} title={(pt.get('title') or '')[:24]!r}")
            log(f"[server] points={len(pts)} video_points={len(vids)}")
            log(f"[verdict] engine_last={len(sample(page, 'final') or [])} "
                f"server_video_points={len(vids)}")
        except Exception as e:
            log(f"[server] read_chapter_job_points failed {type(e).__name__}: {e}")
        b.close()


if __name__ == "__main__":
    main()
