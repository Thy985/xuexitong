"""Debug click_probe: check if JS can find the node and click it."""
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

    # Step 1: Check DOM availability
    debug = page.evaluate("""
        () => {
            const tree = document.querySelector('#coursetree');
            if (!tree) return { error: 'no tree' };
            const cells = tree.querySelectorAll('.posCatalog_select:not(.firstLayer)');
            return {
                tree_found: true,
                cell_count: cells.length,
                sample: Array.from(cells).slice(0, 3).map((c, i) => ({
                    i: i,
                    tag: c.tagName,
                    cls: c.className,
                    has_name: !!(c.querySelector('.posCatalog_name')),
                    name_text: (c.querySelector('.posCatalog_name') || {}).textContent.trim().substring(0, 20)
                }))
            };
        }
    """)
    print(f"DOM debug: {debug}")

    # Step 2: Try click with same logic as click_probe
    def try_click(si):
        result = page.evaluate("""
            (si) => {
                const tree = document.querySelector('#coursetree');
                if (!tree) return { ok: false, reason: 'no tree' };
                const cells = tree.querySelectorAll('.posCatalog_select:not(.firstLayer)');
                const list = Array.from(cells);
                if (si >= list.length) return { ok: false, reason: 'index out of bounds: ' + si + ' >= ' + list.length };
                const target = list[si];
                if (!target) return { ok: false, reason: 'null target at index ' + si };
                const name = target.querySelector('.posCatalog_name');
                if (!name) return { ok: false, reason: 'no .posCatalog_name in cell ' + si };
                name.click();
                return { ok: true, cell_tag: target.tagName, cell_cls: target.className, name_text: name.textContent.trim().substring(0,30) };
            }
        """, si)
        return result

    for si in [0, 1, 2, 3, 4, 5]:
        r = try_click(si)
        print(f"  try_click(si={si}): {r}")
        if r.get("ok"):
            page.wait_for_timeout(3000)
            m = re.search(r'chapterId[=:](\d+)', page.url)
            print(f"    -> URL chapterId: {m.group(1) if m else 'NOT_FOUND'}")
            # Navigate back to course page to continue testing
            page.goto(COURSE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            break
    b.close()
