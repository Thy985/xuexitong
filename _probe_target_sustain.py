# -*- coding: utf-8 -*-
"""Can the in-chapter target point actually SUSTAIN playback once we click its own
video.js play button, under engine-isomorphic conditions (v3 injected)?

What is already settled:
  - finishing the current point does NOT advance in-page: the whole chapter navigates
    away (evidence/seek_play_probe.log). The fast-forward route is dead.
  - two short probes saw the target play ~24-30s after clicking its play button,
    but nobody has measured a *long* window, and completion needs watched time >= 90%
    of the point's duration (~17 min for this 1130s point).
  - engine note app/e2_headed_gha.py:258 (run 4) saw the page pause the out-of-turn
    player every ~2s and switch the chapter away.

So the open question is a duration question. This probe clicks the target's play button
ONCE (no synthesized requests, no play() loop from us -- v3 is the engine's own script)
and then only observes for 180s:
  * does target ct keep advancing, and how often does `paused` flip?
  * does the page navigate the chapter away?
  * does the site's OWN progress call (multimedia/log) name the target oid?
    (that is the site deciding this point is being watched -- observed, never forged)
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
from tvdp.tdvp import _tdvp_course_params
from app.e2_headed_gha import build_base_url, V3_SCRIPT_PATH
from playwright.sync_api import sync_playwright

CID = "1217304738"
CUR_OID = "94382be4"
TGT_OID = "e79a9a86eba1ccf65ceefb925d771fa4"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=" + CID +
              "&courseId=265997861&clazzid=151695658&cpi=506830460" +
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

log = lambda *a: print(*a, flush=True)
JS = """(oid) => {
    const v = document.querySelector('video');
    if (!v) return null;
    if ((v.currentSrc || v.src || '').indexOf(oid) === -1) return null;
    return {rs: v.readyState, dur: (isFinite(v.duration) && v.duration > 0) ? v.duration : null,
            ct: Math.round(v.currentTime * 10) / 10, paused: v.paused, ended: v.ended};
}"""


def bound(page, oid):
    for fr in page.frames:
        try:
            st = fr.evaluate(JS, oid)
        except Exception:
            continue
        if st:
            return fr, st
    return None, None


def click_play_button(page, oid, label):
    """One real user-gesture click on *that point's own* video.js play button.

    Identity = the frame whose <video> src carries this point's objectid (the same
    binding the engine observes on), and the button must come out of that same frame.
    When no button is found, dump what that frame actually offers -- guessing the
    selector twice is what this line of work keeps costing us.
    """
    for fr in page.frames:
        try:
            mine = fr.evaluate("""(o) => {
                const v = document.querySelector('video');
                if (!v) return false;
                return (v.currentSrc || v.src || '').indexOf(o) >= 0;
            }""", oid)
        except Exception:
            continue
        if not mine:
            continue
        h = fr.query_selector("button[class*='play']")
        if not h:
            try:
                shape = fr.evaluate("""() => ({
                    buttons: [...document.querySelectorAll('button')]
                        .slice(0, 12).map(b => b.className).join('|'),
                    playables: [...document.querySelectorAll(
                        '[class*="play"], [class*="vjs-big"], img[onclick]')]
                        .slice(0, 12).map(e => e.tagName + '.' + e.className).join('|'),
                })""")
            except Exception as e:
                shape = {"err": f"{type(e).__name__}: {e}"}
            log(f"[{label}] target frame found but no play button; frame says {shape}")
            return False
        # the card lives inside a scrolled container; ask for it explicitly before
        # clicking, or Playwright clicks a coordinate that is off-view.
        try:
            h.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        try:
            h.click(timeout=5000)
        except Exception:
            try:
                h.evaluate("el => el.click()")
            except Exception as e:
                log(f"[{label}] click failed: {type(e).__name__}: {e}")
                return False
        log(f"[{label}] clicked its own play button "
            f"({h.get_attribute('class')}, frame={fr.url[:60]})")
        return True
    log(f"[{label}] no frame bound to {oid[:8]}")
    return False


def main():
    cp = _tdvp_course_params(_parse_url_params(COURSE_URL))
    base = build_base_url(CID, cp)
    progress_calls = []

    def on_req(req):
        if "multimedia/log" not in req.url:
            return
        try:
            body = (req.post_data or "")[:400]
        except Exception:
            body = ""
        progress_calls.append({"url": req.url, "body": body})

    def counted(oid):
        return sum(1 for c in progress_calls
                   if oid in c["url"] or oid in c["body"])

    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        from utils.cookie_store import ensure_login
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            ignore_https_errors=True)
        page = ctx.new_page()
        page.on("request", on_req)
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        no_v3 = os.environ.get("PROBE_NO_V3") == "1"
        if no_v3:
            log("[boot] v3 NOT injected (isolating our own resume loop as the variable)")
        else:
            page.add_script_tag(content=V3_SCRIPT_PATH.read_text(encoding="utf-8"))
            log("[boot] engine-identical (v3 injected)")

        fr_c, cur = bound(page, CUR_OID)
        fr_t, tgt = bound(page, TGT_OID)
        log(f"[pre] cur={cur} | tgt={tgt}")

        if not click_play_button(page, TGT_OID, "target"):
            b.close()
            return

        url0 = page.url
        flips = 0
        advanced = 0
        samples = 0
        prev_paused = None
        prev_ct = None
        for k in range(90):        # 180s
            time.sleep(2)
            _, t = bound(page, TGT_OID)
            _, c = bound(page, CUR_OID)
            if page.url != url0:
                log(f"[+{2*(k+1)}s] chapter navigated away -> {page.url[:80]}")
                break
            if t is None:
                log(f"[+{2*(k+1)}s] target frame gone")
                break
            samples += 1
            if prev_paused is not None and t["paused"] != prev_paused:
                flips += 1
            if prev_ct is not None and t["ct"] > prev_ct:
                advanced += 1
            prev_paused, prev_ct = t["paused"], t["ct"]
            if k % 5 == 0 or t["ended"]:
                log(f"[+{2*(k+1)}s] tgt rs={t['rs']} dur={t['dur']} ct={t['ct']} "
                    f"paused={t['paused']} | cur ct={c and c.get('ct')} "
                    f"paused={c and c.get('paused')} | site progress calls "
                    f"tgt={counted(TGT_OID)} cur={counted(CUR_OID)} total={len(progress_calls)}")
            if t["ended"]:
                log(f"[VERDICT] target played to END within {2*(k+1)}s")
                break
        moved = advanced / max(1, samples - 1)
        log(f"[result] samples={samples} ct-advanced={advanced} ({moved:.0%}) "
            f"pause_flips={flips} site_progress_calls(target)={counted(TGT_OID)}")
        if progress_calls:
            s = progress_calls[-1]
            log(f"[shape] one site progress call: query={s['url'].split('?', 1)[-1][:220]}"
                f" body={s['body'][:220]}")
        else:
            log("[shape] the site made no multimedia/log call inside the window")
        if moved >= 0.8 and flips <= 3:
            log("[VERDICT] target sustains playback on its own after one click "
                "-> the engine's Step F should activate by clicking, not fast-forward")
        elif moved > 0:
            log("[VERDICT] target plays intermittently (page fights it) -> "
                "engine would have to keep rescuing it; 90% watched-time is not reachable")
        else:
            log("[VERDICT] target never advanced -> in-chapter activation is not available")
        b.close()


if __name__ == "__main__":
    main()
