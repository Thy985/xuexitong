# -*- coding: utf-8 -*-
"""只读判定：章 4738 的"当前位/完成态"三方对齐（A 方案，无点击、无播放）。

要回答两件事：
  1) 站点 DOM 认为哪些任务点已 finished、哪个是页面肯放行的"当前位" —— 决定
     引擎该投哪一 oid（`_probe_serialization.py` 只测出"非当前位点了也被收回"）；
  2) 引擎枚举 `read_chapter_job_points` 给出的 task_id 序列 vs 账本 key vs DOM 实际
     视频模块数 —— 直接判 D13 幻影点（账本有 :video3，DOM 只见 2 个视频模块）。

三方各出一份，逐条对照打印。
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

from playwright.sync_api import sync_playwright
from tvdp.tdvp import read_chapter_job_points

CID = "1217304738"
COURSE = "265997861"
CLAZZ = "151695658"
CPI = "506830460"

log = lambda *a: print(*a, flush=True)

DUMP = """() => {
  const out = [];
  document.querySelectorAll('.ans-job-icon').forEach((icon, i) => {
    const item = icon.closest('.ans-job-item, .ans-item') || icon.parentElement;
    const host = item ? (item.closest('[class*="ans-attach"]') || item) : null;
    const vid = item ? item.querySelector('[objectid]') : null;
    out.push({
      i,
      iconCls: (icon.className || '').toString().trim().slice(0, 60),
      itemCls: item ? (item.className || '').toString().trim().slice(0, 90) : null,
      hostCls: host ? (host.className || '').toString().trim().slice(0, 90) : null,
      oid: vid ? (vid.getAttribute('objectid') || '').slice(0, 8) : null,
      txt: ((item && item.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 40)
    });
  });
  // 声明式"当前位"候选信号：带 active/current 的元素
  const marks = [];
  document.querySelectorAll('[class*="active"],[class*="current"]').forEach((n) => {
    const c = (n.className || '').toString().trim();
    if (/job|point|item|video|catalog/i.test(c) && marks.length < 12) {
      marks.push({tag: n.tagName, cls: c.slice(0, 80),
                  oid: (n.getAttribute('objectid') || '').slice(0, 8) || null,
                  txt: (n.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 30)});
    }
  });
  return {rows: out, marks: marks};
}"""


def main():
    reg = json.loads((root / "state/registry" / f"{COURSE}_{CLAZZ}" / "tasks.json"
                     ).read_text(encoding="utf-8"))
    tasks = reg.get("tasks", reg)
    ledger = {k: (v.get("status"), v.get("consecutive_failures"))
              for k, v in tasks.items() if CID in k}

    with sync_playwright() as p:
        from utils.browser_factory import launch_kwargs
        b = p.chromium.launch(headless=False, **launch_kwargs(),
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                                    "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = ctx.new_page()
        from utils.cookie_store import ensure_login
        from app.e2_headed_gha import build_base_url
        base = build_base_url(CID, None)
        ensure_login(page, ctx, base, env["CX_USER"], env["CX_PASS"])

        pts = read_chapter_job_points(page, CID, COURSE, CLAZZ, CPI)
        log("=== (1) 引擎枚举 read_chapter_job_points ===")
        for i, x in enumerate(pts):
            log(f"  [{i}] " + json.dumps({k: x.get(k) for k in
                ("task_id", "type", "finished", "isFinished", "objectid", "title")},
                ensure_ascii=False))

        log("=== (2) DOM 任务点原始类名/oid + 当前位候选信号 ===")
        rows, marks = [], []
        for fr in page.frames:
            if "knowledge/cards" not in (fr.url or ""):
                continue
            got = fr.evaluate(DUMP)
            rows, marks = got.get("rows", []), got.get("marks", [])
            for row in rows:
                log("  row " + json.dumps(row, ensure_ascii=False))
            for m in marks:
                log("  mark " + json.dumps(m, ensure_ascii=False))
            break

        log("=== (3) 账本该章 key → (status, cf) ===")
        for k in sorted(ledger):
            log(f"  {k} → {ledger[k]}")

        log(f"=== 对照 === DOM .ans-job-icon 数={len(rows)} "
            f"其中带 oid={sum(1 for r in rows if r.get('oid'))} | "
            f"引擎枚举任务点数={len(pts)} | 账本该章 key 数={len(ledger)}")
        b.close()


if __name__ == "__main__":
    main()
