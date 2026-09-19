"""一次性诊断: v1/manage 建权 -> mooc2 真实登录（验证 ensure_login 改后能否穿权限门）。只读，不播视频、不写 state。"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_envf = ROOT / ".env"
if _envf.exists():
    for line in _envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from utils.cookie_store import ensure_login, load_cookies  # noqa: E402

# 用户实测提供的有效 mooc2 URL（含当次 enc/t）
MOOC2_URL = ("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"
             "?courseid=265997861&clazzid=151695658&cpi=506830460"
             "&enc=3a1cd72e9300b0b8d3ed5fb4d5adb7ca"
             "&t=1789477495747&pageHeader=0&v=2&hideHead=0")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def main() -> int:
    user = os.environ.get("CX_USER", "")
    pw = os.environ.get("CX_PASS", "")
    print(f"creds_present={bool(user) and bool(pw)}  cookie_present={bool(load_cookies())}")
    ok = False
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        try:
            ok = ensure_login(pg, ctx, MOOC2_URL, user, pw,
                              login_timeout_s=30, captcha_mode="auto")
            pg.wait_for_timeout(3000)
        except Exception as e:
            print("ensure_login raised:", type(e).__name__, e)
            b.close()
            return 2
        print("login_ok:", ok)
        print("final_url:", pg.url[:120])
        html = pg.content()
        body = ""
        try:
            body = pg.inner_text("body")
        except Exception:
            pass
        print("not_logged_in:", "用户未登录" in html)
        print("permission_gate:", ("暂无权限" in html) or ("没有权限" in html))
        print("personal_space:", "个人空间" in body or "空间" in html)
        print("catalog_html_len:", len(html))
        try:
            pg.screenshot(path=str(ROOT / "docs/evidence" / "diag_v1_login.png"), full_page=True)
            print("screenshot saved")
        except Exception as e:
            print("screenshot err:", e)
        b.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
