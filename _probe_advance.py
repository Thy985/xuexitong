# -*- coding: utf-8 -*-
"""只读探测：完成点尾部播完（ended）后，页面是否自推进到页内下一视频点。

动机：e2e 第 2 次尝试里，未做任何 seek 的页面 ~30s 内 target 帧消失、出现
陌生 oid（b2b…）—— 需确认"在当前点自然 ended 后页面去哪"：是页内下一任务点
（目标 e79a…成为活动播放器），还是整节跳走。
方法：引擎同构启动 → v3 注入 → 等当前点 dur → seek 到 0.95×dur（尾部自然播完）
→ 每 2s 采样所有帧的 {oid,dur,ct,paused,rs} + 顶层 chapterId，最多 120s。
判定：目标 oid 首次成为"有 dur 的活动播放器"的时刻 / URL chapterId 变化。
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
from playwright.sync_api import sync_playwright

CID = "1217304738"
TARGET_OID = "e79a9a86eba1ccf65ceefb925d771fa4"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

params = _parse_url_params(COURSE_URL)
cp = _tdvp_course_params(params)
base = build_base_url(CID, cp)

log = lambda *a: print(*a, flush=True)


def main():
    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        b = p.chromium.launch(
            headless=False, **launch_kwargs(),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{b.version} Safari/537.36"))
        page = ctx.new_page()
        from utils.cookie_store import ensure_login
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        page.add_script_tag(content=V3_SCRIPT_PATH.read_text(encoding="utf-8"))
        log(f"[boot] (engine-identical), target={TARGET_OID[:8]}")

        def snapshot():
            out = []
            if not page.frames:
                return out, '?'
            for f in page.frames:
                try:
                    r = f.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (!v) return null;
                        const src = v.currentSrc || v.src || '';
                        const m = src.match(/[0-9a-f]{32}/);
                        return {oid: m ? m[0].slice(0, 8) : null,
                                dur: (isFinite(v.duration) && v.duration > 0)
                                    ? v.duration : null,
                                ct: v.currentTime, paused: v.paused,
                                rs: v.readyState};
                    }""")
                except Exception:
                    r = None
                if r:
                    out.append({**r, "fr": (f.url or "")[:50]})
            chap = '?'
            try:
                if 'chapterId=' in page.url:
                    chap = page.url.split('chapterId=')[1][:10]
            except Exception:
                pass
            return out, chap

        active = None
        for i in range(45):
            page.wait_for_timeout(2000)
            st, chap = snapshot()
            live = [s for s in st if s.get("dur")]
            if live:
                active = live[0]
                log(f"[wait {2*(i+1)}s] live0={active['oid']} dur={active['dur']:.0f} "
                    f"ct={active['ct']:.1f} rs={active['rs']} chap={chap}")
                break
        if not active:
            log("ABORT: 90s 内无活跃播放器")
            b.close()
            return

        # seek 活动播放器到 95%，留尾部自然播完
        for f in page.frames:
            try:
                r = f.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (!v || !isFinite(v.duration) || v.duration <= 0) return null;
                    const dur = v.duration;
                    v.currentTime = dur * 0.95;
                    return {dur: dur, after: v.currentTime};
                }""")
            except Exception:
                r = None
            if r:
                log(f"[seek] dur={r['dur']:.0f} after={r['after']:.1f}")
                break
        target_seen = False
        chap_before = None
        last_state = None
        for i in range(75):
            page.wait_for_timeout(2000)
            st, chap = snapshot()
            live = [s for s in st if s.get("dur")]
            if chap != chap_before:
                log(f"[{2*(i+1)}s] chapterId 变化: {chap_before} -> {chap}")
                chap_before = chap
            if chap_before is None:
                chap_before = chap
            state = json.dumps(
                [{"oid": s["oid"], "ct": round(s["ct"], 1),
                  "paused": s["paused"]} for s in live], ensure_ascii=False)
            if state != last_state:
                log(f"[{2*(i+1)}s] live={state}（状态变化）")
                last_state = state
            for s in live:
                if s["oid"] == TARGET_OID[:8]:
                    target_seen = True
                    log(f"  >>> 目标 {TARGET_OID[:8]} 成为活跃播放器")
                    break
            if target_seen:
                break
        log(f"[VERDICT] target_active={target_seen} （150s 窗口内页面自推进到目标点与否）")
        b.close()


if __name__ == "__main__":
    main()