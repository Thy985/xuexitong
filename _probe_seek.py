# -*- coding: utf-8 -*-
"""只读探测：已完成的视频点，站点允许到什么程度的 seek（方案A前提验证）。

对象=4738 的当前点（第 1 点，服务端 finished）。probe v2 里播放器 90s 都没活
（dur=None/rs=0），v3 静默 —— 先要和引擎 Step B 逐字对齐启动条件并抓 v3 控制台，
再用大播放按钮可信点击区分「自动播放策略挡住程序化 play()」与「v3 没启动」。
播放器活了以后测两种手段：
  a) 脚本置 currentTime = duration*0.9（等效拖条，未走输入管线）
  b) 真实鼠标事件点进度条 90% 处（需先 hover 出 control bar）
观察：跳转是否生效、5~8s 内页面是否回钳（反作弊）、是否触发暂停/推进。
"""
import json
import os
import pathlib
import re as _re
import sys
import time

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

from resolvers.course_resolver import _parse_url_params
from tvdp.tdvp import _tdvp_course_params
from app.e2_headed_gha import build_base_url, get_video_state, V3_SCRIPT_PATH
from playwright.sync_api import sync_playwright

CID = "1217304738"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0"
              "&openc=9b5661be6351e4d46bc29bfa2d69236a")

FIN_JS = """(oid) => {
    const att = document.querySelector(
        '.ans-insertvideo-online[objectid="' + oid + '"]');
    if (!att) return null;
    const item = att.closest('.ans-attach-ct, .ans-job-item, .ans-item')
        || att.parentElement;
    return item ? item.classList.contains('ans-job-finished') : null;
}"""

CUR_JS = """(frac) => {
    const v = document.querySelector('video');
    if (!v || !isFinite(v.duration) || v.duration <= 0) return null;
    const before = v.currentTime;
    v.currentTime = v.duration * frac;
    return {duration: v.duration, before: before,
            after: v.currentTime, frac: frac};
}"""

params = _parse_url_params(COURSE_URL)
cp = _tdvp_course_params(params)
base = build_base_url(CID, cp)

log = lambda *a: print(*a, flush=True)
console_lines = []

with sync_playwright() as p:
    from utils.browser_factory import launch_kwargs
    # —— 与引擎 Step B 逐字对齐（--display 是 GHA/Xvfb 专属，Windows 省略）——
    b = p.chromium.launch(
        headless=False,
        **launch_kwargs(),
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-web-security",          # 允许跨域 iframe contentDocument
            "--disable-site-isolation-trials",
        ],
    )
    ctx = b.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{b.version} Safari/537.36"
        ),
    )
    page = ctx.new_page()
    page.on("console", lambda m: console_lines.append(f"[{m.type}] {m.text}"))
    from utils.cookie_store import ensure_login
    ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
    page.goto(base, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    boot = page.evaluate("() => ({coursetree: !!document.querySelector('#coursetree')})")
    log(f"[boot] {json.dumps(boot)}")
    page.add_script_tag(content=V3_SCRIPT_PATH.read_text(encoding="utf-8"))
    log("[v3] injected (engine-identical)")

    # 等当前播放器活（dur 出现，最多 90s —— 与引擎 Step F 同预算）
    st = {}
    for i in range(45):
        page.wait_for_timeout(2000)
        st = get_video_state(page)
        if (i + 1) % 5 == 0 or (st.get("found") and st.get("duration")):
            log(f"[wait {2*(i+1)}s] found={st.get('found')} dur={st.get('duration')} "
                f"ct={st.get('currentTime')} paused={st.get('paused')}")
        if st.get("found") and st.get("duration"):
            break

    # 播放器仍没活 → 用 6e0d371 证实过的激活手段：可信点 .vjs-big-play-button
    if not st.get("duration"):
        log("[act] dur 仍无 —— 尝试可信点击大播放按钮激活")
        acted = None
        for fr in page.frames:
            try:
                loc = fr.locator(".vjs-big-play-button")
                for k in range(loc.count()):
                    one = loc.nth(k)
                    if one.is_visible():
                        one.click(timeout=3000)
                        acted = (fr.url or "")[:60]
                        break
            except Exception as e:
                acted = f"err:{str(e)[:60]}"
            if acted and not acted.startswith("err"):
                break
        log(f"[act] clicked={acted}")
        for i in range(15):
            page.wait_for_timeout(2000)
            st = get_video_state(page)
            if st.get("duration"):
                log(f"[act +{2*(i+1)}s] dur={st.get('duration')} "
                    f"ct={st.get('currentTime')} paused={st.get('paused')}")
                break
    src = st.get("src") or ""
    m = _re.search(r"[0-9a-f]{32}", src)
    oid = m.group(0) if m else ""
    log(f"[cur] oid={oid[:8]} dur={st.get('duration')} "
        f"ct={st.get('currentTime')} paused={st.get('paused')}")

    fin = None
    for fr in page.frames:
        if "knowledge/cards" in (fr.url or "") and oid:
            try:
                fin = fr.evaluate(FIN_JS, oid)
            except Exception as e:
                fin = f"err:{e}"
            break
    log(f"[truth] current point finished = {fin}")

    vfr = None
    for fr in page.frames:
        try:
            r = fr.evaluate("""(oid) => {
                const v = document.querySelector('video');
                return !!(v && oid && (v.currentSrc || v.src || '').includes(oid));
            }""", oid)
        except Exception:
            r = False
        if r:
            vfr = fr
            break
    if vfr is None or not st.get("duration") or not fin:
        log("ABORT: 播放器未激活 / 找不到视频帧 / 当前点未确认 finished —— 打印 v3 控制台")
        for line in console_lines:
            log("  console:", line[:160])
        b.close(); sys.exit(0)

    # (a) 脚本置 currentTime 到 90%
    r1 = vfr.evaluate(CUR_JS, 0.9)
    log(f"[seek-a] {json.dumps(r1)}")
    page.wait_for_timeout(3000)
    st2 = get_video_state(page)
    log(f"[seek-a +3s] ct={st2.get('currentTime')} paused={st2.get('paused')} "
        f"rs={st2.get('readyState')}")
    page.wait_for_timeout(5000)
    st3 = get_video_state(page)
    log(f"[seek-a +8s] ct={st3.get('currentTime')} paused={st3.get('paused')}")

    # (b) 真实鼠标：hover 播放器 → 点进度条 90%
    try:
        vfr.locator("video").first.hover(timeout=3000)
        page.wait_for_timeout(800)
        holder = vfr.locator(".vjs-progress-holder, .vjs-progress-control")
        log(f"[seek-b] progress holder count={holder.count()}")
        if holder.count():
            bb = holder.first.bounding_box(timeout=3000)
            if bb:
                holder.first.click(position={"x": bb["width"] * 0.9,
                                             "y": bb["height"] / 2},
                                    timeout=3000)
                page.wait_for_timeout(2000)
                st4 = get_video_state(page)
                log(f"[seek-b +2s] ct={st4.get('currentTime')} "
                    f"paused={st4.get('paused')}")
    except Exception as e:
        log(f"[seek-b] failed: {str(e)[:120]}")

    page.wait_for_timeout(6000)
    st5 = get_video_state(page)
    log(f"[final] ct={st5.get('currentTime')} dur={st5.get('duration')} "
        f"paused={st5.get('paused')} url_chap="
        f"{(page.url.split('chapterId=')[1][:10] if 'chapterId=' in page.url else '?')}")
    log("---- v3 控制台（尾部 40 条）----")
    for line in console_lines[-40:]:
        log("  console:", line[:160])
    b.close()
