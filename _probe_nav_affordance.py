# -*- coding: utf-8 -*-
"""C：headless 只读探测 —— 页面有没有自带的"定位/激活任务点"入口。

只查询 DOM，不 click、不 play、不滚动真页面以外的东西（scrollIntoView 仅用于
hit-test，不产生学习行为）。
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
from app.e2_headed_gha import build_base_url
from playwright.sync_api import sync_playwright

CID = "1217304738"
COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
              "&courseId=265997861&clazzid=151695658&cpi=506830460"
              "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0"
              "&openc=9b5661be6351e4d46bc29bfa2d69236a")

CARDS_JS = r"""() => {
    const out = {points: [], onclickSamples: [], jobCssHints: []};
    const items = [];
    document.querySelectorAll('.ans-job-item, .ans-item').forEach(n => {
        if (n.querySelector('.ans-job-icon') || n.classList.contains('ans-job-icon'))
            items.push(n);
    });
    const atts = Array.from(
        document.querySelectorAll('.ans-insertvideo-online[objectid]'));
    atts.forEach((att, i) => {
        // 与生产 read_chapter_job_points 同构：icon → item（closest 失败退 parentElement）
        let item = att.closest('.ans-job-item, .ans-item');
        let icon = item ? item.querySelector('.ans-job-icon') : null;
        if (!icon) {
            document.querySelectorAll('.ans-job-icon').forEach(ic => {
                if (icon) return;
                const it = ic.closest('.ans-job-item, .ans-item') || ic.parentElement;
                if (it && it.contains(att)) { icon = ic; item = it; }
            });
        }
        if (!item) item = att.parentElement;
        const probe = el => el ? {
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 80),
            cursor: getComputedStyle(el).cursor,
            onclick: (el.getAttribute('onclick') || '').slice(0, 80),
            href: (el.getAttribute('href') || '').slice(0, 60),
            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
            rect: (r => ({w: Math.round(r.width), h: Math.round(r.height)}))(
                el.getBoundingClientRect()),
        } : null;
        // hit-test：滚到视口后，中心点的实际接收元素是谁
        let hit = null;
        if (icon) {
            icon.scrollIntoView({block: 'center'});
            const r = icon.getBoundingClientRect();
            const el = document.elementFromPoint(r.left + r.width / 2,
                                                 r.top + r.height / 2);
            hit = el ? el.tagName.toLowerCase() + '.' +
                (el.className || '').toString().split(/\s+/).slice(0, 2).join('.')
                : null;
        }
        // 附件区里有没有像"播放/进入"的按钮
        const btns = item ? Array.from(item.querySelectorAll(
            'a,button,[onclick],[class*=play],[class*=btn]'))
            .slice(0, 6).map(b => (b.tagName.toLowerCase() + '.' +
                (b.className || '').toString().slice(0, 50))) : [];
        out.points.push({
            idx: i, oid: (att.getAttribute('objectid') || '').slice(0, 8),
            finished: item ? item.classList.contains('ans-job-finished') : null,
            item: probe(item), icon: probe(icon), hitTest: hit,
            itemButtons: btns,
        });
    });
    document.querySelectorAll('[onclick]').forEach(n => {
        const o = (n.getAttribute('onclick') || '');
        if (/job|attach|video|scroll|anchor|focus/i.test(o)) {
            if (out.onclickSamples.length < 10)
                out.onclickSamples.push(o.slice(0, 100));
        }
    });
    return out;
}"""

VIDEO_JS = r"""() => {
    const v = document.querySelector('video');
    if (!v) return null;
    const pick = sel => Array.from(document.querySelectorAll(sel))
        .map(e => {
            const r = e.getBoundingClientRect();
            return (e.className || '').toString().slice(0, 60) +
                ':vis=' + (e.offsetWidth > 0 && e.offsetHeight > 0) +
                ':rect=' + Math.round(r.width) + 'x' + Math.round(r.height);
        }).slice(0, 10);
    let hit = null;
    const bp = document.querySelector('.vjs-big-play-button, [class*=big-play], [class*=play-btn]');
    if (bp) {
        const r = bp.getBoundingClientRect();
        const el = document.elementFromPoint(r.left + r.width / 2,
                                             r.top + r.height / 2);
        hit = el ? el.tagName.toLowerCase() + '.' +
            (el.className || '').toString().slice(0, 50) : 'none';
    }
    return {
        srcOid: ((v.currentSrc || v.src || '').match(/[0-9a-f]{32}/) || [''])[0].slice(0, 8),
        readyState: v.readyState, paused: v.paused,
        videoVisible: v.offsetWidth > 0 && v.offsetHeight > 0,
        playerCtrl: pick('.vjs-big-play-button, .vjs-play-control, .vjs-control-bar, .vjs-poster, [class*=play]'),
        bigPlayHit: hit,
    };
}"""

params = _parse_url_params(COURSE_URL)
cp = _tdvp_course_params(params)
base = build_base_url(CID, cp)

with sync_playwright() as p:
    from utils.browser_factory import launch_kwargs
    b = p.chromium.launch(headless=True, **launch_kwargs(),
                          args=["--no-sandbox", "--disable-dev-shm-usage",
                                "--disable-gpu"])
    ctx = b.new_context()
    page = ctx.new_page()
    from utils.cookie_store import ensure_login
    ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])
    page.goto(base, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(9000)
    result = {"cards": None, "videoFrames": []}
    for fr in page.frames:
        u = fr.url or ""
        try:
            if "knowledge/cards" in u and result["cards"] is None:
                result["cards"] = fr.evaluate(CARDS_JS)
            elif "ananas/modules/video" in u:
                vf = fr.evaluate(VIDEO_JS)
                if vf:
                    result["videoFrames"].append(vf)
        except Exception as e:
            result.setdefault("errs", []).append(str(e)[:80])
    b.close()

print("PROBE=" + json.dumps(result, ensure_ascii=False))
