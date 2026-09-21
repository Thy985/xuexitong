# -*- coding: utf-8 -*-
"""只读探测：目标任务点媒体的「窗内激活」到底是时基预热还是视口触发（方案 A）。

背景：
  - `point_entry_probe.log`（Playwright，点击卡片 16s）与 `point_entry_probe2.log`
    （browser-harness，reload 后点击卡片 10s）都看到目标点 dur=None；
  - 但同一页面**长期驻留**后两卡都 rs=4 / dur>0（bh_play6b before 态）。
  ⇒ "无窗内入口" 与 "预热确实发生" 并存，说明观测窗可能短于站点预热周期。

本探测把两个混淆变量拆开，且都以**当前点为对照**：
  A 段 零交互等待 40s（记录 window.scrollY 证明没滚）→ 目标点是否自己出 metadata？
  B 段 把目标点模块 scrollIntoView 入视口后 30s → 是否因滚动才出 metadata？
  C 段 一旦 dur>0，对目标 <video> 发一次真实 play()（等价用户点播放键）→
       currentTime 是否增长？增长 ⇒ 窗内激活可用；否则站点只服务"当前点"。

判定写在前面的两次探测都可能是错的：它们只证明了"16s 内没加载"，
没证明"永远不会加载"。本脚本给出的是加载时刻，不是有/无。
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
from app.e2_headed_gha import build_base_url, get_video_state
from playwright.sync_api import sync_playwright

CID = "1217304738"                       # 4.10 IPV6 2 —— 章内两视频点
CUR_OID = "94382be4"                     # 第 1 点（对照，已 COMPLETED）
TARGET_OID = "e79a9a86eba1ccf65ceefb925d771fa4"   # 第 2 点（观测对象）
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304738"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

log = lambda *a: print(*a, flush=True)


def bound_state(page, oid8):
    """返回 (绑定帧, 该帧 <video> 状态)；绑定判据 = video.src 含 oid8。"""
    for fr in page.frames:
        try:
            st = fr.evaluate("""(oid) => {
                const v = document.querySelector('video');
                if (!v) return null;
                if ((v.currentSrc || v.src || '').indexOf(oid) === -1) return null;
                return {rs: v.readyState, net: v.networkState,
                        dur: (isFinite(v.duration) && v.duration > 0) ? v.duration : null,
                        ct: Math.round(v.currentTime * 10) / 10, paused: v.paused,
                        err: v.error ? v.error.code : null,
                        preload: v.preload, src: (v.currentSrc || v.src || '').slice(-26)};
            }""", oid8)
        except Exception:
            continue
        if st:
            return fr, st
    return None, {"rs": None, "dur": None, "ct": None, "paused": None, "missing": True}


def line(tag, page, cur, tgt):
    sy = page.evaluate("() => Math.round(window.scrollY)")
    log(f"[{tag}] scrollY={sy} | cur dur={cur.get('dur')} rs={cur.get('rs')} "
        f"ct={cur.get('ct')} paused={cur.get('paused')} "
        f"|| tgt dur={tgt.get('dur')} rs={tgt.get('rs')} ct={tgt.get('ct')} "
        f"paused={tgt.get('paused')} err={tgt.get('err')}")


def click_module(page, oid8):
    """真实坐标（CDP 输入级）点击该任务点视频模块中心 —— 等价用户点播放区。

    Playwright 的 bounding_box 已把 iframe 偏移换算到顶层视口，故可直接喂 mouse.click。
    """
    for fr in page.frames:
        if "knowledge/cards" not in (fr.url or ""):
            continue
        h = fr.query_selector(f'[objectid^="{oid8}"]')
        if not h:
            return {"err": "no el"}
        bb = h.bounding_box()
        if not bb or not bb.get("width"):
            return {"err": "no box", "bb": bb}
        x = bb["x"] + bb["width"] / 2
        y = bb["y"] + bb["height"] / 2
        page.mouse.click(x, y)
        return {"ok": True, "x": round(x), "y": round(y),
                "w": round(bb["width"]), "h": round(bb["height"])}
    return {"err": "no cards"}


def main():
    cp = _tdvp_course_params(_parse_url_params(COURSE_URL))
    base = build_base_url(CID, cp)

    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        b = p.chromium.launch(
            headless=False, **launch_kwargs(),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            ignore_https_errors=True,
                            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                        f"Chrome/{b.version} Safari/537.36"))
        page = ctx.new_page()
        from utils.cookie_store import ensure_login
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
        page.goto(base, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        _, cur = bound_state(page, CUR_OID)
        tf, tgt = bound_state(page, TARGET_OID)
        line("t0+4s", page, cur, tgt)

        # ── A 段：零交互等待，看目标点是否会自己预热 ──────────────
        log("=== A 段：零交互等待 15s ===")
        preheated = tgt.get("dur") or 0
        for k in range(3):
            time.sleep(5)
            _, cur = bound_state(page, CUR_OID)
            tf, tgt = bound_state(page, TARGET_OID)
            line(f"A{k+1} +{5*(k+1)}s", page, cur, tgt)
            if tgt.get("dur"):
                preheated = 5 * (k + 1)
                log(f">>> A 段命中：目标点零交互 {preheated}s 后自然出 metadata（时基预热）")
                break

        # ── B 段：把目标模块滚入视口，看是否视口触发 ────────────────
        if not preheated:
            log("=== B 段：scrollIntoView 目标模块（10s） ===")
            done = False
            for fr in page.frames:
                if "knowledge/cards" not in (fr.url or ""):
                    continue
                try:
                    done = fr.evaluate("""(oid) => {
                        const el = document.querySelector(
                            '[objectid="' + oid + '"]');
                        if (!el) return false;
                        el.scrollIntoView({block: 'center'});
                        return true;
                    }""", TARGET_OID)
                except Exception as e:
                    log("[B] scroll err:", str(e)[:80])
                break
            log(f"[B] scrollIntoView done={done}")
            for k in range(2):
                time.sleep(5)
                _, cur = bound_state(page, CUR_OID)
                tf, tgt = bound_state(page, TARGET_OID)
                line(f"B{k+1} +{5*(k+1)}s", page, cur, tgt)
                if tgt.get("dur"):
                    log(f">>> B 段命中：滚动后 {5*(k+1)}s 出 metadata（视口/滚动触发）")
                    break

        # ── D/E 段：真实坐标点击播放区 —— 区分"元素不可载"与"模块自有链路才取流"
        #    站点 <video> 的 src 可能是上一次会话留下的过期签名地址：play() 只让
        #    它停在 NETWORK_LOADING；真正的播放键处理器会重新换取流地址。
        def module_ui(fr):
            """转储模块内可见控制元素：preload=none 时只有站点自己的播放键会去换取流地址。"""
            try:
                return fr.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('button,a,[class],[id]').forEach((el) => {
                        const t = ((el.className || '') + ' ' + (el.id || '') + ' '
                                   + (el.getAttribute('title') || '')).toString();
                        if (!/play|pause|控制|播放|big|btn/i.test(t)) return;
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) return;
                        out.push({tag: el.tagName, cls: t.trim().slice(0, 48),
                                  txt: (el.innerText || '').trim().slice(0, 12),
                                  w: Math.round(r.width), h: Math.round(r.height),
                                  top: Math.round(r.top)});
                    });
                    const v = document.querySelector('video');
                    return {n: out.length, els: out.slice(0, 14),
                            preload: v ? v.preload : null,
                            srcHead: v ? (v.currentSrc || v.src || '').slice(0, 60) : null};
                }""")
            except Exception as e:
                return {"err": str(e)[:80]}

        def click_and_watch(label, oid8):
            """帧内元素级点击站点播放键（Playwright 自动滚入视口），观察是否换取新流地址。"""
            tfx, st0 = bound_state(page, oid8)
            log(f"=== {label}：pre rs={st0.get('rs')} dur={st0.get('dur')} "
                f"ct={st0.get('ct')} preload={st0.get('preload')} src=...{st0.get('src')}")
            if tfx is None:
                log(f"[{label}] 无绑定帧，跳过")
                return st0, None
            log(f"[{label} UI] {json.dumps(module_ui(tfx), ensure_ascii=False)}")
            hit_btn = None
            for sel in ["button[class*='play']", "[class*='bigplay']", "[class*='playBtn']",
                        "[class*='play-btn']", ".vjs-big-play-button", "[class*='start']",
                        "[class*='video'] [class*='btn']"]:
                try:
                    h = tfx.query_selector(sel)
                except Exception:
                    h = None
                if h:
                    try:
                        h.click(timeout=4000)
                        hit_btn = sel
                        break
                    except Exception as e:
                        log(f"[{label} click {sel}] err {str(e)[:60]}")
            log(f"[{label} click] button={hit_btn}")
            if hit_btn is None:                      # 没有可点控件 → 兜底中心点击
                log(f"[{label} fallback] {click_module(page, oid8[:8])}")
            src0 = st0.get("src")
            hit = None
            for k in range(6):
                time.sleep(3)
                tfx, st = bound_state(page, oid8)
                log(f"[{label} +{3*(k+1)}s] rs={st.get('rs')} net={st.get('net')} "
                    f"dur={st.get('dur')} ct={st.get('ct')} paused={st.get('paused')} "
                    f"preload={st.get('preload')} err={st.get('err')} src=...{st.get('src')}")
                if st.get("src") != src0:
                    log(f">>> {label}：src 被换成新签名地址（{src0} -> {st.get('src')}）")
                if st.get("dur"):
                    hit = st
                    break
            return st0, hit

        _, cur0 = bound_state(page, CUR_OID)
        if tgt.get("dur") and tf is not None:
            log("=== C 段：目标点已有 metadata，直接真实 play() 看起播 ===")
            r = tf.evaluate("""() => { const v = document.querySelector('video');
                                       if (!v) return {ok:false};
                                       try { v.play(); return {ok:true, paused:v.paused}; }
                                       catch (e) { return {ok:false, err:String(e).slice(0,80)}; } }""")
            log(f"[C] play() -> {json.dumps(r)}")
            for k in range(4):
                time.sleep(3)
                _, tgt = bound_state(page, TARGET_OID)
                log(f"[C{k+1} +{3*(k+1)}s] dur={tgt.get('dur')} ct={tgt.get('ct')} "
                    f"paused={tgt.get('paused')} rs={tgt.get('rs')}")
            try:
                tf.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
            except Exception:
                pass
        else:
            # D：先打目标点（真问题），E：再打当前点（正对照，证明点击手法本身有效）
            _, hit_t = click_and_watch("D 目标点", TARGET_OID)
            if hit_t:
                log(">>> D 命中：目标点被点击激活（窗内入口存在，形态=模块自有播放链路）")
            else:
                log(">>> D 未命中：目标点点击后仍无 metadata")
            _, hit_c = click_and_watch("E 当前点(正对照)", CUR_OID)
            log(f"[总判定] 目标点可点击激活={bool(hit_t)} | 当前点可点击激活={bool(hit_c)} "
                f"→ {'窗内入口存在' if hit_t else ('仅当前点可激活 ⇒ 无窗内入口' if hit_c else '手法/环境无效，需查点击是否落在播放器')}")

        # ── F 段：src 直连性 —— 解释"play() 停在 NETWORK_LOADING 却永不出 metadata"
        #    若 CDN 回 403/410/长度异常 ⇒ 页面里挂着的是上次会话的陈旧签名地址，
        #    引擎/用户都必须经站点换取新地址，窗内 DOM 层激活根本不可能成。
        log("=== F 段：带 cookie 直连 <video>.src ===")
        for lbl, oid8 in (("目标点", TARGET_OID), ("当前点", CUR_OID)):
            tfx, _ = bound_state(page, oid8)
            if tfx is None:
                log(f"[F {lbl}] 无绑定帧")
                continue
            u = tfx.evaluate("() => { const v = document.querySelector('video');"
                             "return v ? (v.currentSrc || v.src || '') : ''; }")
            if not u:
                log(f"[F {lbl}] src 为空")
                continue
            try:
                r = page.context.request.get(u, headers={
                    "Referer": "https://mooc1.chaoxing.com/",
                    "Range": "bytes=0-1023"})
                log(f"[F {lbl}] status={r.status} ct={r.headers.get('content-type')} "
                    f"len={r.headers.get('content-length')} "
                    f"cr={r.headers.get('content-range')} url={u[:78]}")
            except Exception as e:
                log(f"[F {lbl}] req err {str(e)[:90]} url={u[:78]}")
        b.close()


if __name__ == "__main__":
    main()
