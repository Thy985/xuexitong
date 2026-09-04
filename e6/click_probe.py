"""E6: TDVP Click Probe — 通过 DOM 点击获取无 chapterId 的节点链接。

借鉴 xuexitongScript/v3_optimized.user.js 的 #coursetree DOM 结构：
  #coursetree > ul > li (章节) → .posCatalog_select (小节) → .posCatalog_name (标题)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional


def click_probe_chapter_id(course_url: str, chapter_index: int, cell_index: int) -> Optional[str]:
    """点击目录树中指定位置的节点，从 URL 提取 chapterId。

    Args:
        course_url: studentstudy URL
        chapter_index: DOM #coursetree > ul > li 的索引
        cell_index: 该 li 内 .posCatalog_select:not(.firstLayer) 的索引

    Returns:
        chapterId 字符串，或 None（点击失败/无 chapterId）
    """
    user = os.environ.get("CX_USER")
    pw = os.environ.get("CX_PASS")
    if not user or not pw:
        return None

    try:
        from resolvers.course_resolver import _parse_url_params
        params = _parse_url_params(course_url)
        chapter_id = params.get("chapter_id") or ""

        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        import e2_headed_gha as E
        E.COURSE_ID = params.get("course_id", "")
        E.CLAZZ_ID = params.get("clazz_id", "")
        E.CPI = params.get("cpi", "")
        E.ENC = params.get("enc", "")
        E.OPENR = params.get("openc")
        E.HIDETYPE = params.get("hidetype") or "0"

        from playwright.sync_api import sync_playwright
        display = os.environ.get("DISPLAY", ":99")

        with sync_playwright() as pwc:
            browser = pwc.chromium.launch(
                headless=False, channel="chromium",
                args=[f"--display={display}", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()

            # ── 登录 ──────────────────────────────────────────────
            base = E.build_base_url(chapter_id)
            page.goto(base, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            try:
                page.wait_for_selector("#phone", timeout=12000)
                page.locator("#phone").first.fill(user)
                page.locator("#pwd").first.fill(pw)
                for sel in ["button:has-text('登录')", "a.loginbtn", ".loginbtn"]:
                    try:
                        if page.locator(sel).count() > 0:
                            page.locator(sel).first.click(force=True, timeout=3000)
                            break
                    except Exception:
                        pass
                for _ in range(15):
                    page.wait_for_timeout(1000)
                    if "passport2.chaoxing.com/login" not in page.url:
                        break
            except Exception:
                pass

            # ── 导航到课程页面 ────────────────────────────────────
            page.goto(course_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # ── 点击目标节点 ──────────────────────────────────────
            clicked = page.evaluate("""
                (si) => {
                    const tree = document.querySelector('#coursetree');
                    if (!tree) return false;
                    const cells = tree.querySelectorAll('.posCatalog_select:not(.firstLayer)');
                    const list = Array.from(cells);
                    const target = list[si];
                    if (!target) return false;
                    const name = target.querySelector('.posCatalog_name');
                    if (!name) return false;
                    name.click();
                    return true;
                }
            """, cell_index)

            if not clicked:
                print(f"[e6] click-probe: click failed ci={chapter_index} si={cell_index}",
                      file=sys.stderr)
                browser.close()
                return None

            page.wait_for_timeout(4000)
            new_url = page.url
            m = re.search(r'chapterId[=:](\d+)', new_url)
            cid = m.group(1) if m else ""
            browser.close()
            print(f"[e6] click-probe: ci={chapter_index} si={cell_index} → chapterId={cid or 'NOT_FOUND'}",
                  flush=True)
            return cid or None

    except Exception as e:
        print(f"[e6] click_probe error: {e}", file=sys.stderr)
        return None
