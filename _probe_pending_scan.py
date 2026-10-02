# -*- coding: utf-8 -*-
"""只读扫描：对全部 DOM 未完成章做 L2 点级深读，画出「剩余可做工作」地图。

背景：run 36993138621 NOOP 的根因是队列里没有任何 video 任务——29 条 `:other`
不计入队列，而唯一被深读的 719 实为 PPT 章（无视频点）。本探测回答：
  1. 目录里还有哪些 DOM 未完成章？job_remaining 多少？
  2. 每个未完成章的点级构成：几个视频点（已完成/未完成）、几个其它点？
  3. 结论：引擎（video-only）还有没有活可干？
纯 DOM 读：无 v3 注入、无点击、无播放、不写 state/。
"""
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

import os
from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

from resolvers.course_resolver import _parse_url_params
from tvdp.tdvp import _tdvp_course_params, read_chapter_job_points, chapter_video_summary
from app.e2_headed_gha import build_base_url
from playwright.sync_api import sync_playwright

COURSE_ID, CLAZZ_ID, CPI = "265997861", "151695658", "506830460"
ENC = "1bc1bd778f9e00d924fe97b3c63f76f4"
RAW_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304719"
           f"&courseId={COURSE_ID}&clazzid={CLAZZ_ID}&cpi={CPI}&enc={ENC}"
           "&mooc2=1&hidetype=0")

log = lambda *a: print(*a, flush=True)


def main():
    cp = _tdvp_course_params(_parse_url_params(RAW_URL))
    base = build_base_url("1217304719", cp)
    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-gpu"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                        f"Chrome/{b.version} Safari/537.36"))
        page = ctx.new_page()
        from utils.cookie_store import ensure_login
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(RAW_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector("#coursetree, a[href*='chapterId']",
                               timeout=30000, state="attached")

        from tvdp.tdvp import extract_catalog_from_page
        chapters = extract_catalog_from_page(page, RAW_URL)
        pending = [c for c in chapters if c.get("status") != "completed"
                   and c.get("chapter_id")]
        log(f"catalog: {len(chapters)} chapters, pending={len(pending)}")
        for c in pending:
            log(f"  pending {c.get('chapter_id')} job_remaining="
                f"{c.get('job_remaining')} {c.get('title','')[:24]!r}")

        report = []
        for c in pending:
            cid = c["chapter_id"]
            try:
                pts = read_chapter_job_points(page, cid, COURSE_ID, CLAZZ_ID, CPI)
            except Exception as e:
                log(f"  {cid}: read error {type(e).__name__}: {e}")
                pts = None
            if pts is None:
                pts = []
            tv, tf = chapter_video_summary(pts)
            n_other = sum(1 for x in pts if x.get("type") != "video")
            rec = {"chapter_id": cid, "title": c.get("title", ""),
                   "job_remaining": c.get("job_remaining"),
                   "points_read": len(pts), "video_total": tv,
                   "video_finished": tf, "video_todo": tv - tf,
                   "other_points": n_other}
            report.append(rec)
            log(f"  {cid} {rec['title'][:20]!r}: video {tf}/{tv} todo={tv-tf} "
                f"other={n_other} (read {len(pts)} pts)")

        b.close()

    out = pathlib.Path(tempfile_dir()) / "xue_pending_scan.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"saved -> {out}")
    todo = sum(r["video_todo"] for r in report)
    log(f"=== SUMMARY: 剩余视频工作量 video_todo={todo} "
        f"(涉及 {sum(1 for r in report if r['video_todo'] > 0)} 章) ===")


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()


if __name__ == "__main__":
    main()
