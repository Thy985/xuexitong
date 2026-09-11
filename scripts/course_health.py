"""只读列出课程的所有章节 + 各章待完成任务点状态（Windows headless）。

复用 tvdp.tdvp 的#coursetree DOM 提取语法（同一份 JS），但本机 Windows 用
headless chromium（不是 GHA 的 Xvfb）。输出每章 chapter_id/title/status/
job_remaining，用于「给一门课自动找到未完成的视频点」。

用法：
  python scripts/course_health.py --url "https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=...&clazzid=...&cpi=...&enc=...&chapterId=..."
  python scripts/course_health.py --name 数据        # 只显示标题含关键词的章
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

JS_EXTRACT = """
() => {
    const results = [];
    const seenTitles = new Set();
    const tree = document.querySelector('#coursetree');
    if (tree) {
        const allCells = tree.querySelectorAll(':scope > ul > li .posCatalog_select:not(.firstLayer)');
        allCells.forEach((cell, gi) => {
            const nameEl = cell.querySelector('.posCatalog_name');
            const title = nameEl
                ? (nameEl.title || nameEl.textContent || '').trim()
                : (cell.textContent || '').trim();
            if (!title) return;
            if (seenTitles.has(title)) return;
            seenTitles.add(title);
            const text = (cell.textContent || '').replace(/\\s+/g, ' ').trim();
            let status = 'unknown';
            if (cell.classList.contains('posCatalog_finish') ||
                cell.classList.contains('flip') ||
                cell.querySelector('.icon_Completed') ||
                /已完成|Completed/i.test(text)) {
                status = 'completed';
            } else if (/待完成|未完成|Pending/i.test(text)) {
                status = 'pending';
            }
            let cid = '';
            const nodeHtml = cell.outerHTML || '';
            const m1 = nodeHtml.match(/chapterId[=:'"](\\d+)/);
            const m2 = nodeHtml.match(/data-?chapter[-_]?id[=:'"](\\d+)/);
            const m3 = nodeHtml.match(/getTeacherAjax\\([^)]*,\\s*'([^']+)'/);
            const m4 = nodeHtml.match(/getTeacherAjax\\([^)]*,\\s*"([^"]+)"/);
            if (m1) cid = m1[1]; else if (m2) cid = m2[1];
            else if (m3) cid = m3[1]; else if (m4) cid = m4[1];
            const isActive = cell.classList.contains('posCatalog_active');
            let jobRemaining = 0;
            const unf = cell.querySelector('input[type="hidden"][class*="UnfinishCount"], input[type="hidden"][class*="unfinish"], input[name*="job"]');
            if (unf && unf.value) jobRemaining = parseInt(unf.value, 10) || 0;
            results.push({chapter_id: cid, title, status, is_active: isActive,
                          chapter_index: gi, job_remaining: jobRemaining,
                          text: text.slice(0, 150)});
        });
    }
    if (results.length === 0) {
        document.querySelectorAll('a[href*="chapterId"]').forEach(a => {
            const href = a.href || '';
            const m = href.match(/chapterId=(\\d+)/);
            if (!m) return;
            let container = a.closest('li, .catalog_list, tr, [class*="item"], [class*="node"]') || a.parentElement;
            const text = container ? (container.innerText || '') : '';
            results.push({chapter_id: m[1],
                          title: (a.textContent || '').trim(),
                          status: /已完成/.test(text) ? 'completed' : (/待完成/.test(text) ? 'pending' : 'unknown'),
                          is_active: false, chapter_index: 0, job_remaining: 0,
                          text: text.slice(0, 150)});
        });
    }
    return results;
}
"""  # noqa: E501


def _load_env(root: Path):
    envf = root / ".env"
    if not envf.exists():
        return
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ("=" in line or ":" in line):
            sep = "=" if "=" in line else ":"
            k, _, v = line.partition(sep)
            os.environ.setdefault(k.strip(), v.strip())


def _redact(u: str) -> str:
    return re.sub(r"enc=[0-9a-f]{16,}", "enc=***", u)


def main(argv) -> int:
    _load_env(ROOT)
    url = ""
    name = ""
    for i, a in enumerate(argv):
        if a == "--url" and i + 1 < len(argv):
            url = argv[i + 1]
        elif a == "--name" and i + 1 < len(argv):
            name = argv[i + 1]
    if not url:
        # 默认：引擎配置的 canonical 课程（计算机网络-2025级），真实可用的 URL 参数
        try:
            from app.e2_headed_gha import default_params, build_base_url
            cp = default_params()
            url = build_base_url(cp.chapter_id, cp)
            print("[default-params] ", end="")
        except Exception as e:
            print("要 --url <studentstudy>（或用 default_params 失败：%r）" % e, file=sys.stderr)
            return 2
    if not os.environ.get("CX_USER"):
        print("缺 CX_USER", file=sys.stderr)
        return 2

    from playwright.sync_api import sync_playwright
    from utils.cookie_store import ensure_login

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-web-security", "--disable-site-isolation-trials"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900},
                            user_agent=UA, ignore_https_errors=True)
        pg = ctx.new_page()
        ok = ensure_login(pg, ctx, url,
                          os.environ["CX_USER"], os.environ["CX_PASS"],
                          login_timeout_s=25, captcha_mode="auto")
        print("[login] ok=", ok, end="  ")
        pg.goto(url, timeout=45000)
        try:
            pg.wait_for_selector("#coursetree, a[href*='chapterId']",
                                 timeout=25000, state="attached")
        except Exception:
            print("[warn] tree selector not found")
        pg.wait_for_timeout(2000)
        rows = pg.evaluate(JS_EXTRACT)
        b.close()

    if not rows:
        print("no chapters parsed; url=", _redact(url))
        return 1
    print(f"<<course health>> url={_redact(url)[:90]}")
    print(f"{'status':11}{'cid':>12}  {'jobRemain':>8}  {'title'}")
    shown = 0
    for r in rows:
        if name and name not in r["title"]:
            continue
        print(f"{r['status']:11}{r['chapter_id']:>12}  {r['job_remaining']:>8}  "
              f"{r['title'][:46]}")
        shown += 1
    if name:
        print(f"(--name filter) matched {shown}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))