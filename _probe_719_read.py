# -*- coding: utf-8 -*-
"""只读探测：719（点对点协议PPP）的 L2 点级深读为什么在 CI 上返回空。

背景：run 36993138621 NOOP，`_combined_points` 为空（artifact 无 chapter_points.json），
深读目标恰是 raw_url 锚点章 719。`read_chapter_job_points` 对「cards 帧没找到 /
选择器超时 / 行全被 D13 过滤」都静默返回 []，日志无法归因。本探测用**生产同款**
函数（直接 import）读 719，并用已知可读的 741 做对照，输出：
  (1) 每步之后的全帧 URL 清单；
  (2) knowledge/cards 帧里 .ans-job-icon/.ans-job-item 的原始行（marker/type/
      isFinished/titleText/objectid）——D13 过滤前的样子；
  (3) 生产转换 `job_rows_to_points` 的结果；
  (4) cards 帧 HTML 落盘（tempdir）供离线复查。
无 v3 注入、无点击、无播放、不写 state/ —— 纯 DOM 读。
"""
import json
import pathlib
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

import os
from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

from resolvers.course_resolver import _parse_url_params
from tvdp.tdvp import (_tdvp_course_params, read_chapter_job_points,
                       job_rows_to_points)
from app.e2_headed_gha import build_base_url
from playwright.sync_api import sync_playwright

COURSE_ID, CLAZZ_ID, CPI = "265997861", "151695658", "506830660".replace("0660", "0460")
ENC = "1bc1bd778f9e00d924fe97b3c63f76f4"

log = lambda *a: print(*a, flush=True)

ROWS_JS = """
() => {
    const out = [];
    document.querySelectorAll('.ans-job-item, .ans-item, .ans-job-icon').forEach(n => {
        const icon = n.classList.contains('ans-job-icon')
            ? n : n.querySelector('.ans-job-icon');
        if (!icon) return;
        const item = icon.closest('.ans-job-item, .ans-item') || icon.parentElement;
        const finished = item ? item.classList.contains('ans-job-finished') : false;
        const marker = (icon.className||'').toString();
        const vid = item ? item.querySelector('.ans-insertvideo-online[objectid]') : null;
        out.push({ marker, isFinished: finished,
                   titleText: (item? (item.innerText||'') : '').trim().replace(/\\s+/g,' ').slice(0,60),
                   objectid: vid ? (vid.getAttribute('objectid') || '') : '' });
    });
    return out;
}
"""


def frame_inventory(page, tag):
    frames = [(f.name[:20], f.url[:110]) for f in page.frames]
    log(f"--- frames {tag} ({len(frames)}) ---")
    for name, url in frames:
        log(f"    [{name}] {url}")


def cards_frames(page):
    return [f for f in page.frames if "knowledge/cards" in f.url]


def raw_rows(page):
    rows_all = []
    for fr in cards_frames(page):
        try:
            fr.wait_for_selector(".ans-job-item, .ans-job-icon", timeout=8000)
        except Exception as e:
            log(f"    [cards] selector wait failed: {type(e).__name__}")
            continue
        page.wait_for_timeout(1200)
        rows = fr.evaluate(ROWS_JS)
        n_icon = fr.evaluate(
            "document.querySelectorAll('.ans-job-icon').length")
        log(f"    [cards] {fr.url[:90]} icons={n_icon} rows={len(rows)}")
        for r in rows:
            log(f"      row: finished={r['isFinished']} oid={r['objectid'][:12] or '-'} "
                f"marker={r['marker'][:70]} text={r['titleText'][:30]!r}")
        rows_all.append((fr.url, rows))
    return rows_all


def probe_chapter(page, cid, tag):
    log(f"===== {tag}: chapter {cid} =====")
    url = (f"https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId={cid}"
           f"&courseId={COURSE_ID}&clazzid={CLAZZ_ID}&cpi={CPI}"
           f"&enc={ENC}&mooc2=1&hidetype=0")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(7000)
    frame_inventory(page, f"after goto {cid}")

    pts = read_chapter_job_points(page, cid, COURSE_ID, CLAZZ_ID, CPI)
    log(f"[production] read_chapter_job_points -> {json.dumps(pts, ensure_ascii=False)}")

    rows_all = raw_rows(page)
    for fr_url, rows in rows_all:
        merged = job_rows_to_points(rows, cid)
        log(f"[transform] job_rows_to_points({len(rows)} rows) -> "
            f"{json.dumps(merged, ensure_ascii=False)[:400]}")

    if rows_all:
        out = pathlib.Path(tempfile.gettempdir()) / f"_probe_cards_{cid}.html"
        out.write_text(cards_frames(page)[0].content(), encoding="utf-8")
        log(f"[dump] cards html -> {out}")


def main():
    cp = _tdvp_course_params({"course_id": COURSE_ID, "clazz_id": CLAZZ_ID,
                              "cpi": CPI, "enc": ENC, "chapter_id": "1217304719"})
    base = build_base_url("1217304741", cp)
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
        log("login ok")

        probe_chapter(page, "1217304741", "CONTROL")
        probe_chapter(page, "1217304719", "TARGET")
        b.close()


if __name__ == "__main__":
    main()
