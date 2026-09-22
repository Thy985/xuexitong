# -*- coding: utf-8 -*-
"""Discriminating probe: does the engine's fast-forward actually PLAY the current point?

Background (run 35669129208, chapter 1217304738, target :video2):
  - binding correct (target oid e79a9a86), red-line gate passed,
    log line "[bind] fastforward current point 94382be4 -> 955.8s" (ok=True),
  - yet the bound target frame kept dur=None for 90s x 2 attempts -> FAIL.

Hypothesis H1: setting currentTime on the *current* point does not resume playback
  (the element stays paused), so `ended` never fires and the page's serial state
  machine never advances to the next task point.
Competing H2: the point does play to the end but the page advances elsewhere
  (next chapter / other module) rather than to the target point.

Engine-identical boot (v3 injected), then three phases:
  A  observe the current player untouched for 12s  (does it play on its own?)
  B  seek exactly like the engine (min(0.9*dur, dur-1)), observe 40s  -> H1?
  C  only if B shows paused/frozen ct: call play() on that same frame,
     observe until `ended` or 180s  -> does the page then reach the target?

Read-mostly: no forged requests, no ledger writes. Progress POSTs the site makes
itself are only *observed* (logged), never synthesized.
"""
import json
import os
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

from resolvers.course_resolver import _parse_url_params
from tvdp.tdvp import _tdvp_course_params
from app.e2_headed_gha import build_base_url, V3_SCRIPT_PATH

CID = "1217304738"
CUR_OID = "94382be4"        # point 1 (server: finished)
TARGET_OID = "e79a9a86"     # point 2 (:video2, the point we want the page to activate)
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

params = _parse_url_params(COURSE_URL)
base = build_base_url(CID, _tdvp_course_params(params))

log = lambda *a: print(*a, flush=True)

SNAPSHOT = """() => {
    const v = document.querySelector('video');
    if (!v) return null;
    const src = v.currentSrc || v.src || '';
    const m = src.match(/[0-9a-f]{32}/);
    return {oid: m ? m[0].slice(0, 8) : null,
            dur: (isFinite(v.duration) && v.duration > 0) ? v.duration : null,
            ct: v.currentTime, paused: v.paused, rs: v.readyState,
            ended: v.ended, nn: v.networkState};
}"""


def main():
    progress_posts = []
    with sync_playwright_guard() as (b, page):
        page.on("request", lambda r: progress_posts.append(r.url)
                if "multimedia/log" in r.url else None)

        def snap():
            frames = []
            for f in page.frames:
                try:
                    r = f.evaluate(SNAPSHOT)
                except Exception:
                    r = None
                if r:
                    frames.append(r)
            chap = "?"
            try:
                if "chapterId=" in page.url:
                    chap = page.url.split("chapterId=")[1][:10]
            except Exception:
                pass
            return frames, chap

        def show(tag, frames, chap):
            log(f"  {tag} chap={chap} " + json.dumps(
                [{"oid": f["oid"], "dur": f["dur"], "ct": round(f["ct"], 1),
                  "paused": f["paused"], "rs": f["rs"], "ended": f["ended"]}
                 for f in frames], ensure_ascii=False))

        # ---- boot, engine-identical
        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        page.add_script_tag(content=V3_SCRIPT_PATH.read_text(encoding="utf-8"))
        log(f"[boot] engine-identical, url={page.url[:70]}")

        cur = None
        for i in range(45):
            page.wait_for_timeout(2000)
            fr, chap = snap()
            live = [f for f in fr if f.get("dur")]
            if live:
                cur = live[0]
                show(f"[ready {2*(i+1)}s]", fr, chap)
                break
        if not cur:
            log("ABORT: 90s inside no player with duration (page gave us nothing)")
            return
        log(f"[cur] oid={cur['oid']} dur={cur['dur']:.0f} expected_cur={CUR_OID} "
            f"expected_target={TARGET_OID}")

        # ---- Phase A: untouched
        log("Phase A: observe untouched 12s (does the page play it on its own?)")
        a_ct = []
        for i in range(6):
            page.wait_for_timeout(2000)
            fr, chap = snap()
            me = next((f for f in fr if f["oid"] == cur["oid"]), None)
            if me:
                a_ct.append(round(me["ct"], 1))
                log(f"  A{2*(i+1)}s ct={me['ct']:.1f} paused={me['paused']} rs={me['rs']}")
        grew = len(set(a_ct)) > 1
        log(f"[A] currentTime moved on its own = {grew} (values={a_ct})")

        # ---- Phase B: seek exactly like the engine
        pos = round(cur["dur"] * 0.9, 1)
        seen = []
        frozen = True
        target_seen = False
        for f in page.frames:
            try:
                r = f.evaluate("""(pos) => {
                    const v = document.querySelector('video');
                    if (!v) return null;
                    if ((v.currentSrc || v.src || '').indexOf(pos.oid) < 0
                        && !isFinite(v.duration)) return null;
                    if (!isFinite(v.duration) || v.duration <= 0) return null;
                    v.currentTime = Math.min(pos.p, v.duration - 1);
                    return {after: v.currentTime, paused: v.paused, dur: v.duration};
                }""", {"p": pos, "oid": cur["oid"]})
            except Exception:
                r = None
            if r:
                log(f"[B] seek -> {r['after']:.1f}s, paused right after seek = "
                    f"{r['paused']}  <-- H1 predicts True")
                break
        else:
            log("[B] seek did not land")
            return
        target_seen = False
        for i in range(75):
            page.wait_for_timeout(2000)
            fr, chap = snap()
            me = next((f for f in fr if f["oid"] == cur["oid"]), None)
            tgt = next((f for f in fr if f["oid"] == TARGET_OID), None)
            if me:
                seen.append(round(me["ct"], 1))
                if abs(me["ct"] - r["after"]) > 1.0:
                    frozen = False
                if i % 3 == 0 or me["ended"]:
                    tgt_txt = (f"dur={tgt['dur']} ct={tgt['ct']:.1f}"
                               if tgt else "absent")
                    log(f"  B{2*(i+1)}s cur ct={me['ct']:.1f} paused={me['paused']} "
                        f"ended={me['ended']} | target {tgt_txt}")
                if me["ended"]:
                    log(f"[B] current point reached ENDED at {2*(i+1)}s "
                        f"without any play() call; watch what the page does next")
                    break
            if tgt and tgt["dur"]:
                log("[B] target became active before the current point even ended")
                target_seen = True
                break
        log(f"[B] ct frozen after seek (no play) = {frozen} (last={seen[-3:]})")

        # ---- Phase C: what happens after the real `ended`
        if not target_seen:
            log("Phase C: observe 90s after the current point's tail")
            chap_before = CID
            for i in range(45):
                page.wait_for_timeout(2000)
                fr, chap = snap()
                tgt = next((f for f in fr if f["oid"] == TARGET_OID), None)
                if i % 3 == 0 or (tgt and tgt["dur"]) or chap != chap_before:
                    show(f"C{2*(i+1)}s", fr, chap)
                if tgt and tgt["dur"]:
                    log(f"[VERDICT] page activated the TARGET point after the current "
                        f"one ended (target dur={tgt['dur']:.0f}) -> fast-forward does "
                        f"work; the GHA failure is a budget/cadence problem")
                    return
                if chap != chap_before:
                    log(f"[VERDICT] page left the chapter instead (chapterId "
                        f"{chap_before} -> {chap}) -> the serial state machine does NOT "
                        f"advance in-page to this target")
                    return
                chap_before = chap
            log("[VERDICT] 90s after the tail: target stayed inactive, page stayed put")
        log(f"[observed] site-made progress POSTs: {len(progress_posts)}")
        for u in progress_posts[-5:]:
            log(f"  {u[:120]}")


def sync_playwright_guard():
    import contextlib

    @contextlib.contextmanager
    def g():
        from playwright.sync_api import sync_playwright
        from utils.browser_factory import launch_kwargs
        from utils.cookie_store import ensure_login
        p = sync_playwright().start()
        b = p.chromium.launch(
            headless=False, **launch_kwargs(),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{b.version} Safari/537.36"))
        page = ctx.new_page()
        try:
            ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        except Exception as e:
            log(f"[login] ensure_login raised {type(e).__name__}: {e}")
        try:
            yield b, page
        finally:
            b.close()
            p.stop()
    return g()


if __name__ == "__main__":
    main()
