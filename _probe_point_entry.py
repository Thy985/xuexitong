# -*- coding: utf-8 -*-
"""只读探测：窗内"下一任务点"入口是否存在（方案 3，用户定）。

背景：完成点尾播完 → 页面跳下一章（advance_probe 实证），窗内目标点
（e79a9a86）从未被激活。本探测审计站点是否有合法窗内入口，把目标点变成
页面"当前"播放器：
  (1) 静态：课程树当前节的子节点结构；所有 [objectid] 附件的 a/onclick/cursor/
      可点祖先；页面内 "任务点" 进度条及其可点元素；iframe 帧 URL 参数。
  (2) 动态：对候选入口做一次真实点击，观察 e79a… 帧是否取得 metadata
      （dur>0）或进度条状态翻转；点击后 15s 采样目标帧。
无 v3 注入（避免自动播放污染观测）。
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

CID = "1217304738"
TARGET_OID = "e79a9a86eba1ccf65ceefb925d771fa4"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0")

log = lambda *a: print(*a, flush=True)


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

        # ── (a) 静态审计 ─────────────────────────────────────────────
        audit = page.evaluate("""() => {
            const out = {};
            const tree = document.querySelector(
                '#coursetree, #Coursetree, .coursetree');
            if (tree) {
                const cur = tree.querySelector('.current')
                    || tree.querySelector('[class*=active]') || tree;
                const near = cur.closest('li,div') || cur;
                out.treeCurrent = (near.innerText || '')
                    .replace(/\\s+/g, ' ').trim().slice(0, 60);
                const around = [];
                for (let el = cur; el && el !== tree && around.length < 10;
                     el = el.parentElement) {
                    const a = el.querySelector(':scope > a');
                    around.push({tag: el.tagName,
                        txt: (el.innerText || '').trim().slice(0, 40),
                        href: a ? (a.getAttribute('href') || '').slice(0, 90) : '',
                        cls: (el.className || '').toString().slice(0, 50)});
                }
                out.treeAround = around;
                const links = Array.from(tree.querySelectorAll('a')).slice(0, 30)
                    .map(a => ({txt: (a.innerText || '').trim().slice(0, 30),
                                href: (a.href || '').slice(0, 110)}));
                out.treeAllLinks = links.filter(x => /job|chapterId/.test(x.href));
            } else {
                out.treeCurrent = 'NO #coursetree';
            }
            const objs = [];
            document.querySelectorAll('[objectid]').forEach((el) => {
                const oid = el.getAttribute('objectid');
                if (!oid) return;
                const a = el.closest('a');
                objs.push({oid: oid.slice(0, 8),
                    cls: (el.className || '').toString().slice(0, 60),
                    aHref: a ? a.href.slice(0, 110) : null,
                    onclick: (el.getAttribute('onclick') || '').slice(0, 60),
                    cursor: getComputedStyle(el).cursor});
            });
            out.attachObjects = objs.slice(0, 10);
            const clickables = [];
            document.querySelectorAll('a[href], [onclick]').forEach((el) => {
                const t = ((el.getAttribute('href') || '') + ' '
                    + (el.getAttribute('onclick') || '')
                    + ' ' + (el.innerText || '')).toLowerCase();
                if (/e79a9a86|94382be4|jobid|任务点/.test(t)
                    && clickables.length < 15) {
                    clickables.push({tag: el.tagName,
                        txt: (el.innerText || '').trim()
                            .replace(/\\s+/g, ' ').slice(0, 40),
                        href: (el.getAttribute('href') || '').slice(0, 90),
                        onclick: (el.getAttribute('onclick') || '').slice(0, 60)});
                }
            });
            out.clickables = clickables;
            return out;
        }""")
        log("=== (a) 静态审计 ===")
        log(json.dumps(audit, ensure_ascii=False, indent=1))

        for fr in page.frames:
            if "knowledge/cards" in (fr.url or ""):
                try:
                    cards = fr.evaluate("""() => {
                        const out = [];
                        document.querySelectorAll(
                            '.ans-insertvideo-online[objectid]')
                            .forEach((el, i) => {
                                const item = el.closest(
                                    '.ans-inserting-ct,.ans-job-item');
                                const a = el.closest('a');
                                out.push({i, oid: (el.getAttribute('objectid')
                                    || '').slice(0, 8),
                                    itemCls: item ? (item.className || '') : '',
                                    aHref: a ? a.href.slice(0, 90) : '',
                                    cursor: getComputedStyle(el).cursor});
                            });
                        return out;
                    }""")
                    log("== cards 帧 attach 行 ==")
                    log(json.dumps(cards, ensure_ascii=False))
                except Exception as e:
                    log("cards eval err:", e)
                break
        log(f"[top url] {page.url}")
        for i, fr in enumerate(page.frames):
            u = fr.url or ""
            if "video" in u or "ananas" in u:
                log(f"[ifr {i}] {u[:130]}")

        # ── (b) 动态：点击目标卡片，看目标播放器是否活 ──────────────
        def target_state():
            try:
                return get_video_state(page, TARGET_OID)
            except Exception as e:
                return {"err": str(e)[:80]}

        last = target_state()
        log(f"[pre-click] target found={last.get('found')} "
            f"dur={last.get('duration')} ct={last.get('currentTime')}")

        done = False
        for fr in page.frames:
            if "knowledge/cards" not in (fr.url or ""):
                continue
            try:
                fr.evaluate("""(oid) => {
                    const el = document.querySelector(
                        '.ans-insertvideo-online[objectid="' + oid + '"]');
                    if (!el) return false;
                    el.scrollIntoView({block: 'center'});
                    window.dispatchEvent(new Event('scroll'));
                    return true;
                }""", TARGET_OID)
            except Exception:
                pass
            try:
                fr.locator(f'.ans-insertvideo-online[objectid="{TARGET_OID}"]') \
                    .first.click(timeout=4000)
                done = True
            except Exception as e:
                log(f"[click card] err: {str(e)[:80]}")
            break
        log(f"[click card] done={done}")
        for t in range(8):
            time.sleep(2)
            st = target_state()
            log(f"[{2*(t+1)}s] again found={st.get('found')} "
                f"dur={st.get('duration')} ct={st.get('currentTime')} "
                f"paused={st.get('paused')}")
            if st.get("duration") and st.get("duration") > 0:
                log(">>> 目标点被点成轮内活跃播放器（有 metadata）<<<")
                break
        log("[结论] 见上：目标点 16s 内出现 dur>0 ⇒ 窗内入口存在；否则不存在")
        b.close()


if __name__ == "__main__":
    main()