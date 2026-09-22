# -*- coding: utf-8 -*-
"""READ-ONLY batch probe: fresh point-level truth for the remaining ``:videoN`` siblings.

Why: run 35733572959 (sha 367c1cd, i.e. WITH the refine-side prune of #26) still spent
its single nightly dispatch (``max_chapters=1``) on the phantom ``1217304733:video2``:
18.7s -> FAIL -> PHANTOM-CORRECTED, ``done`` stayed 27.  The prune could not fire because
its evidence is per-chapter and the chapter it read (1217304719) is not the chapter it
dispatched -- and even that read came back ``video_total=0`` ("untrusted").

Six same-shape records are still queued (4734/4737/4741:video2, 4741:video3,
4750/4751:video2), all DISCOVERED/NONE/cf=0, i.e. one burned night each.

This probe answers, per chapter, with FRESH evidence:
  * what the server-side point reader sees (``read_chapter_job_points``), and
  * what the playback engine itself sees (``enumerate_video_objectids``).
The two agree -> we may act.  They disagree -> the sibling may be real and deletion is off.

Controls are mandatory, not decoration: chapters whose ``:video2`` was REALLY played
(4738, 4708: COMPLETED/SERVER_VERIFIED) must read >= 2.  If they read 1, this measurement
cannot distinguish "phantom" from "multi-video chapter" and every candidate verdict below
would be meaningless -- so the script reports that case explicitly instead of concluding.

Touching nothing: no clicks, no play(), no seek, no registry/snapshot/state writes, no
v3 injection, no ``multimedia/log``.  Navigation only, which is what every live verify
already does.
"""
import json
import os
import pathlib
import sys
import time

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from utils.env_file import load_env_file            # noqa: E402

env = dict(os.environ)
load_env_file(root, env)

from tvdp.tdvp import chapter_video_summary, read_chapter_job_points   # noqa: E402

COURSE_ID = "265997861"
CLAZZ_ID = "151695658"
CPI = "506830460"

# (chapter, role).  Roles: control_true_multi / control_corrected / read_empty_case / candidate
CHAPTERS = [
    ("1217304738", "control_true_multi: 4738:video2 is COMPLETED/SERVER_VERIFIED (really "
                   "played+bound) -> MUST read >= 2 or the probe proves nothing"),
    ("1217304708", "control_true_multi: 4708:video2 COMPLETED/SERVER_VERIFIED cf=4 -> "
                   "MUST read >= 2"),
    ("1217304733", "control_corrected: engine enumerated 1 point here 40 min ago -> expect 1"),
    ("1217304719", "read_empty_case: refine head of run 35733572959 read video_total=0 "
                   "for a chapter discovery calls video/pending -> is the reader broken?"),
    ("1217304734", "candidate: 4734:video2 DISCOVERED/NONE (parent COMPLETED/SERVER_VERIFIED)"),
    ("1217304737", "candidate: 4737:video2 DISCOVERED/NONE (parent UNKNOWN/SERVER_VERIFIED)"),
    ("1217304741", "candidate: 4741:video2 AND 4741:video3 DISCOVERED/NONE"),
    ("1217304750", "candidate: 4750:video2 DISCOVERED/NONE"),
    ("1217304751", "candidate: 4751:video2 DISCOVERED/NONE"),
]

log = print


def chapter_url(cid: str) -> str:
    return (f"https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId={cid}"
            f"&courseId={COURSE_ID}&clazzid={CLAZZ_ID}&cpi={CPI}"
            "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")


def engine_view(page):
    """What the playback engine would see on the currently loaded chapter."""
    from app.e2_headed_gha import enumerate_video_objectids
    try:
        return list(enumerate_video_objectids(page) or [])
    except Exception as e:
        log(f"    engine raised {type(e).__name__}: {e}")
        return []


def main() -> int:
    user, pw = env.get("CX_USER"), env.get("CX_PASS")
    if not user or not pw:
        log("[abort] CX_USER/CX_PASS not both present -- refusing to start a browser "
            "that would sit on the login wall")
        return 2

    out_dir = root / "evidence" / "probe_phantom_batch"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report = {"started_utc": stamp, "course": {"course_id": COURSE_ID,
                                               "clazz_id": CLAZZ_ID, "cpi": CPI},
              "chapters": []}

    from playwright.sync_api import sync_playwright
    from utils.browser_factory import launch_kwargs
    from utils.cookie_store import ensure_login

    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = ctx.new_page()
        t0 = time.time()
        ok = ensure_login(page, ctx, chapter_url(CHAPTERS[0][0]), user, pw)
        log(f"[login] ensure_login -> {ok} ({time.time() - t0:.1f}s)")
        if not ok:
            log("[abort] login failed -- stopping, nothing else touched")
            b.close()
            return 3

        for cid, role in CHAPTERS:
            t = time.time()
            try:
                pts = read_chapter_job_points(page, cid, COURSE_ID, CLAZZ_ID, CPI) or []
            except Exception as e:
                log(f"[{cid}] reader raised {type(e).__name__}: {e}")
                pts = []
                report["chapters"].append({"chapter_id": cid, "role": role,
                                           "reader_error": type(e).__name__})
                continue
            vids = [q for q in pts if (q or {}).get("type") == "video"]
            total, finished = chapter_video_summary(pts)
            eng = engine_view(page)
            row = {
                "chapter_id": cid,
                "role": role,
                "reader_points": len(pts),
                "reader_video_total": total,
                "reader_video_finished": finished,
                "reader_videos": [{"task_id": q.get("task_id"),
                                   "isFinished": q.get("isFinished"),
                                   "objectid": (q.get("objectid") or "")[:10],
                                   "title": q.get("title") or ""} for q in vids],
                "engine_video_count": len(eng),
                "engine_objectids": [o[:10] for o in eng],
                "agree": bool(eng) and len(eng) == total,
                "secs": round(time.time() - t, 1),
            }
            report["chapters"].append(row)
            log(f"[{cid}] role={role.split(':')[0]} reader_total={total} "
                f"reader_finished={finished} engine={len(eng)} agree={row['agree']} "
                f"({row['secs']}s)")

        b.close()

    # ── verdict block: only conclude if the controls showed the probe CAN see 2 points ──
    controls = [c for c in report["chapters"] if c.get("role", "").startswith("control_true_multi")]
    decisive = [c for c in controls if (c.get("reader_video_total") or 0) >= 2
                and (c.get("engine_video_count") or 0) >= 2]
    report["controls_passed"] = len(decisive)
    report["controls_total"] = len(controls)
    log("")
    log(f"[controls] {len(decisive)}/{len(controls)} true-multi chapters were seen as >= 2 "
        f"by BOTH readers")
    if len(decisive) < len(controls):
        log("[inconclusive] a control failed -> this measurement cannot separate phantom "
            "from real multi-video point; DO NOT delete anything on this evidence")
    else:
        for c in report["chapters"]:
            if not c.get("role", "").startswith("candidate"):
                continue
            cid = c["chapter_id"]
            n = max(c.get("reader_video_total") or 0, c.get("engine_video_count") or 0)
            log(f"[verdict] {cid}: fresh evidence says {n} video point(s) -> "
                f"every ledger :videoN with N > {n} is a phantom (reader={c.get('reader_video_total')} "
                f"engine={c.get('engine_video_count')} agree={c.get('agree')})")
        for c in report["chapters"]:
            if c.get("role", "").startswith("read_empty_case"):
                log(f"[verdict] {c['chapter_id']}: reader={c.get('reader_video_total')} "
                    f"engine={c.get('engine_video_count')} -- this is the chapter whose "
                    f"E6.2 refine read came back 0 last run")

    dst = out_dir / f"batch_{stamp}.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[write] report -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
