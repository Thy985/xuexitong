# -*- coding: utf-8 -*-
"""区分条件：窗内点击目标点播放键能否续播，取决于"当前点是否在播"。

engine `app/e2_headed_gha.py:258` 记录 run 4：点目标帧 .vjs-big-play-button
起播成立，但轮外播放器每 ~2s 被暂停、整章被切走。
_probe_sustain.py 却观测到 30s 零暂停 —— 当时两卡都 idle（当前点没在播）。
本探针造出引擎同构条件：**先让当前点真播**，再点目标点播放键，2s 粒度同时采样
两个播放器与顶层 URL，量出 paused 翻转与切章是否复现。纯观测，不注入脚本。
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
            ct: Math.round(v.currentTime * 10) / 10, paused: v.paused};
}"""


def bound(page, oid8):
    for fr in page.frames:
        try:
            st = fr.evaluate(JS, oid8)
        except Exception:
            continue
        if st:
            return fr, st
    return None, None


def start(page, oid8, label):
    fr, st = bound(page, oid8)
    if fr is None:
        log(f"[{label}] 无绑定帧")
        return False
    h = fr.query_selector("button[class*='play']")
    if not h:
        log(f"[{label}] 无播放键")
        return False
    h.click(timeout=5000)
    log(f"[{label}] 已点播放键（pre ct={st.get('ct')} dur={st.get('dur')}）")
    return True


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

        if not start(page, CUR_OID, "1 当前点(轮内)"):
            b.close()
            return
        time.sleep(6)
        _, cur = bound(page, CUR_OID)
        log(f"[1 6s后] cur paused={cur and cur.get('paused')} ct={cur and cur.get('ct')} "
            f"dur={cur and cur.get('dur')}  ← 轮内确在播={bool(cur and not cur.get('paused'))}")

        if not start(page, TGT_OID, "2 目标点(轮外)"):
            b.close()
            return

        url0, tgt_flips, tgt_adv, last_ct = page.url, 0, 0, None
        prev = None
        for k in range(12):
            time.sleep(2)
            _, tgt = bound(page, TGT_OID)
            _, cr = bound(page, CUR_OID)
            if tgt is None:
                log(f"[+{2*(k+1)}s] 目标帧消失（疑似整章被切走）urlChanged={page.url != url0}")
                break
            if prev is not None and tgt["paused"] != prev:
                tgt_flips += 1
            prev = tgt["paused"]
            if last_ct is not None and tgt["ct"] > last_ct:
                tgt_adv += 1
            last_ct = tgt["ct"]
            log(f"[+{2*(k+1)}s] tgt paused={tgt['paused']} ct={tgt['ct']} dur={tgt['dur']} "
                f"| cur paused={cr and cr.get('paused')} ct={cr and cr.get('ct')} "
                f"| urlChanged={page.url != url0}")
        log(f"[判定] 轮内点1 在播的条件下：目标点 paused 翻转={tgt_flips} 次，"
            f"ct 前进采样数={tgt_adv}/11，顶层 URL 变化={page.url != url0}")
        b.close()


if __name__ == "__main__":
    main()
