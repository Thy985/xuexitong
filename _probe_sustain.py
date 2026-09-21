# -*- coding: utf-8 -*-
"""只读探测：窗内点击目标点播放键后，播放能否**持续**（probe3 只证到 metadata）。

假设（来自旧记录）：页面按"当前位"归属每 ~2s 暂停非当前位播放器（R-04），
并把整章切走。本探测用 2s 粒度采样 paused / currentTime / 顶层 URL，量出：
  · 起播后能连续爬多少秒才被暂停；
  · 暂停后是否还会自己恢复（v3 驱动器缺席时站点态度如何）；
  · 顶层 URL 是否变化（整章被切走）。
不注入任何脚本、不改服务端字段 —— 纯观测。
"""
import json
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
from app.e2_headed_gha import build_base_url
from playwright.sync_api import sync_playwright

CID = "1217304738"
TARGET_OID = "e79a9a86eba1ccf65ceefb925d771fa4"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=" + CID +
              "&courseId=265997861&clazzid=151695658&cpi=506830460" +
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

log = lambda *a: print(*a, flush=True)


def bound(page, oid8):
    for fr in page.frames:
        try:
            st = fr.evaluate("""(oid) => {
                const v = document.querySelector('video');
                if (!v) return null;
                if ((v.currentSrc || v.src || '').indexOf(oid) === -1) return null;
                return {rs: v.readyState, dur: (isFinite(v.duration) && v.duration > 0)
                        ? v.duration : null, ct: Math.round(v.currentTime * 10) / 10,
                        paused: v.paused, ts: Date.now()};
            }""", oid8)
        except Exception:
            continue
        if st:
            return fr, st
    return None, None


def main():
    cp = _tdvp_course_params(_parse_url_params(COURSE_URL))
    base = build_base_url(CID, cp)
    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                                    "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = ctx.new_page()
        from utils.cookie_store import ensure_login
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        tf, st = bound(page, TARGET_OID)
        log(f"[init] {json.dumps(st)}")
        if tf is None:
            log("目标绑定帧缺失，退出")
            b.close()
            return
        h = tf.query_selector("button[class*='play']")
        if not h:
            log("无 video.js 播放键，退出")
            b.close()
            return
        h.click(timeout=5000)
        log("[click] vjs-big-play-button done")

        url0 = page.url
        flips = 0
        prev_paused = None
        max_ct = 0.0
        t0 = time.time()
        for k in range(15):
            time.sleep(2)
            tf, st = bound(page, TARGET_OID)
            if st is None:
                log(f"[+{2*(k+1)}s] 目标帧消失（页面已切走）url={page.url[:70]}")
                break
            if prev_paused is not None and st["paused"] != prev_paused:
                flips += 1
            prev_paused = st["paused"]
            max_ct = max(max_ct, st["ct"] or 0)
            log(f"[+{2*(k+1)}s] paused={st['paused']} ct={st['ct']} dur={st['dur']} "
                f"rs={st['rs']} urlChanged={page.url != url0}")
        log(f"[判定] 观测 {time.time()-t0:.0f}s：paused 翻转={flips} 次，"
            f"currentTime 峰值={max_ct}（起播点见 init），URL 是否离开本章={page.url != url0}")
        b.close()


if __name__ == "__main__":
    main()
