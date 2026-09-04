"""Map cell_index to chapterId via onclick attribute in DOM."""
import os, sys, re
os.environ["CX_USER"] = "18605440838"
os.environ["CX_PASS"] = "147258369Thy"

sys.path.insert(0, "e2")
import e2_headed_gha as E
from resolvers.course_resolver import _parse_url_params
from playwright.sync_api import sync_playwright

COURSE_URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?"
              "chapterId=1217304706&courseId=265997861&clazzid=151695658"
              "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
              "&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a")
params = _parse_url_params(COURSE_URL)
E.COURSE_ID = params.get("course_id", "")
E.CLAZZ_ID = params.get("clazz_id", "")
E.CPI = params.get("cpi", "")
E.ENC = params.get("enc", "")
E.OPENR = params.get("openc")
E.HIDETYPE = params.get("hidetype") or "0"

display = os.environ.get("DISPLAY", ":99")
with sync_playwright() as pwc:
    b = pwc.chromium.launch(headless=False, channel="chromium",
        args=[f"--display={display}", "--no-sandbox",
              "--disable-dev-shm-usage", "--disable-gpu"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    base = E.build_base_url(params.get("chapter_id", ""))
    page.goto(base, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    try:
        page.wait_for_selector("#phone", timeout=12000)
        page.locator("#phone").first.fill("18605440838")
        page.locator("#pwd").first.fill("147258369Thy")
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

    page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # 打印每个 cell 的 onclick 里的数字（chapterId）
    infos = page.evaluate("""
        () => {
            const cells = document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer)');
            return Array.from(cells).slice(0, 15).map((cell, i) => {
                const name = cell.querySelector('.posCatalog_name');
                const text = name ? name.textContent.trim().replace(/^\\d+\\.\\d+\\s*/, '') : '?';
                const onclick = name ? (name.getAttribute('onclick') || '') : '';
                // Extract all numbers from onclick (like getTeacherAjax('courseId','clazzId','chapterId'))
                const nums = onclick.match(/\\d{7,}/g) || [];
                return { idx: i, title: text, chapterId_from_onclick: nums[0] || 'NONE' };
            });
        }
    """)
    print("cell_index | title                    | chapterId (from onclick)")
    print("-" * 65)
    for info in infos:
        print(f"  {info['idx']:2d}     | {info['title'][:30]:30s} | {info['chapterId_from_onclick']}")

    # 测试：点击 si=4 看看得到什么 chapterId
    print("\n--- Click probe si=4 ---")
    clicked = page.evaluate("""
        () => {
            const cells = document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer)');
            const cell = cells[4];
            if (!cell) return 'NO CELL';
            const name = cell.querySelector('.posCatalog_name');
            if (!name) return 'NO NAME';
            name.click();
            return 'CLICKED';
        }
    """)
    print(f"Click result: {clicked}")
    page.wait_for_timeout(4000)
    m = re.search(r'chapterId[=:](\d+)', page.url)
    print(f"URL chapterId: {m.group(1) if m else 'NOT_FOUND'}")
    b.close()
