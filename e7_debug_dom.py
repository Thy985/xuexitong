"""Debug: 检查课程目录树实际 DOM 结构，找出 click_probe 失败原因。"""
import os, sys
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
    browser = pwc.chromium.launch(
        headless=False, channel="chromium",
        args=[f"--display={display}", "--no-sandbox",
              "--disable-dev-shm-usage", "--disable-gpu"],
    )
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    # 登录
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

    # 检查 DOM
    has_tree = page.evaluate("!!document.querySelector('#coursetree')")
    print(f"#coursetree exists: {has_tree}")
    if not has_tree:
        print("WARNING: #coursetree not found on page")
        # 截图查看
        page.screenshot(path="state/debug_nomenu.png")
    else:
        cells_all = page.evaluate("document.querySelectorAll('#coursetree .posCatalog_select').length")
        cells_non_first = page.evaluate("document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer)').length")
        print(f".posCatalog_select count: {cells_all}")
        print(f".posCatalog_select:not(.firstLayer) count: {cells_non_first}")

        # 第一个非 firstLayer 节点的内部结构
        first_info = page.evaluate("""
            () => {
                const tree = document.querySelector('#coursetree');
                const cells = tree.querySelectorAll('.posCatalog_select:not(.firstLayer)');
                if (!cells[0]) return 'NO FIRST CELL';
                const cell = cells[0];
                const name = cell.querySelector('.posCatalog_name');
                const a = cell.querySelector('a');
                const href = a ? a.href : 'NO HREF';
                return {
                    cell_tag: cell.tagName,
                    cell_classes: cell.className,
                    cell_text: cell.textContent.trim().substring(0, 50),
                    name_exists: !!name,
                    name_tag: name ? name.tagName : 'N/A',
                    name_classes: name ? name.className : 'N/A',
                    name_text: name ? name.textContent.trim().substring(0, 50) : 'N/A',
                    has_a: !!a,
                    a_href: href,
                    child_tags: Array.from(cell.children).map(c => c.tagName + ':' + (c.className||'')),
                    innerHTML: cell.innerHTML.substring(0, 500)
                };
            }
        """)
        print(f"\nFirst cell structure:")
        import json
        print(json.dumps(first_info, ensure_ascii=False, indent=2))

        # 检查前几个节点
        print("\nFirst 6 cells:")
        for i in range(min(6, cells_non_first)):
            info = page.evaluate(f"""
                () => {{
                    const cells = document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer)');
                    const cell = cells[{i}];
                    const name = cell.querySelector('.posCatalog_name');
                    const a = cell.querySelector('a');
                    return {{
                        idx: {i},
                        tag: cell.tagName,
                        classes: cell.className,
                        name_text: name ? name.textContent.trim() : 'NO NAME',
                        a_href: a ? a.href : 'NO HREF',
                        has_name: !!name,
                        has_a: !!a,
                    }};
                }}
            """)
            print(f"  [{i}] {info}")

        # 测试点击第一个节点
        print("\n--- Testing click ---")
        clicked = page.evaluate("""
            () => {
                const cells = document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer)');
                const cell = cells[0];
                if (!cell) return 'NO CELL';
                const name = cell.querySelector('.posCatalog_name');
                if (!name) return 'NO NAME';
                console.log('About to click:', name.textContent.trim());
                name.click();
                return 'CLICKED';
            }
        """)
        print(f"Click result: {clicked}")
        page.wait_for_timeout(4000)
        print(f"URL after click: {page.url}")
        import re
        m = re.search(r'chapterId[=:](\d+)', page.url)
        print(f"chapterId from URL: {m.group(1) if m else 'NOT FOUND'}")
        browser.close()
