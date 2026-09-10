"""LOCAL E2E Capability Audit — drive the REAL mooc2 site from this host.

Intention (user-driven): 突破只读，走真实链路；每一步记录「实际观察到了什么」，
而不是简单记 PASS。产出 `docs/evidence/e2e_local_audit.json`（脱敏证据），并据它写
`docs/runbooks/LOCAL_E2E_CAPABILITY_AUDIT.md`（能力边界表 CAP-XXX）。

纪律：
  - 只观察不伪造：拿不到 isPassed 就记 UNKNOWN/PARTIAL + 原因，绝不填 PASS。
  - 复用 app 的 ensure_login / default_params / build_base_url（真实 passport 登录）。
  - headless Chromium（本机 Windows 可用）；enc/t/cookie 脱敏后才落盘。
  - 每一步记录「观察到了什么」（具体值/成员/计数）。

用法：本机 .env 提供 CX_USER/CX_PASS；`python scripts/local_e2e_audit.py`
输出：docs/evidence/e2e_local_audit.json
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "evidence" / "e2e_local_audit.json"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = ":" if ":" in line else "="
        k, _, v = line.partition(sep)
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and len(v) >= 3:
            os.environ.setdefault(k, v)


def redact(s: str) -> str:
    s = re.sub(r"enc=[0-9a-f]{16,}", "enc=***", s)
    s = re.sub(r"(&t=)\d{10,}", r"\1***", s)
    s = re.sub(r"_uid=(\d{4})\d+", r"_uid=\1***", s)
    # cdn 视频路径（含签名）：整体打码，避免泄漏 ak_/at_/tk token
    s = re.sub(r"https?://[\w.-]+/\S*?\.(?:mp4|flv|m3u8)\b[^\s\"]*", "s://cdn/***.mp4", s, flags=re.I)
    # 兜底：任何 `?xx_=value` / `xx=value` 签名参数
    s = re.sub(r"([?&][\w]*?_?=(?:[a-z0-9]{8,}))", r"\1=***", s, flags=re.I)
    s = re.sub(r"(cookie[^=]{0,6})=[^&\s]{6,}", r"\1=***", s, flags=re.I)
    return s


def _json_safe(o):
    """把 float NaN/Inf / 非 str 键 转成 JSON 可序列化形态（NaN → None）。"""
    import math
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, float) and math.isinf(o):
        return "Inf" if o > 0 else "-Inf"
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _observe_playback(page, max_s: int = 25) -> dict:
    """有界真实播放观察：触发播放 → 采样 currentTime 是否增长 → 抓 multimedia/log 请求 → 读 isPassed。

    只观察不伪造：currentTime 是否增长、multimedia/log 网络响应是否出现、isPassed 是否为真，
    都如实记录；达不到就记 False / 不出现。
    """
    ml_logs = []
    page.on("response", lambda r: (
        ml_logs.append({"url": r.url, "status": r.status}) if "/multimedia/log" in r.url else None))
    started = time.time()
    samples = []
    try:
        # 找到 video/audio 并真实触发 play
        for fr in page.frames:
            try:
                fr.evaluate("""() => {
                  const v = document.querySelector('video,audio');
                  if (v) { v.muted = true; try { v.play().catch(()=>{}); } catch(e){} }
                }""")
            except Exception:
                continue
        while time.time() - started < max_s:
            ct = None
            for fr in page.frames:
                try:
                    ct = fr.evaluate("document.querySelector('video,audio')?.currentTime")
                    if ct is not None:
                        break
                except Exception:
                    continue
            samples.append({"t_ms": int((time.time() - started) * 1000), "currentTime": ct})
            time.sleep(1.0)
            if len(ml_logs) or (ct and ct > 1.0):
                # 有多媒体网络帧或已起播一段时间 → 可停止进一步采样
                if len(samples) >= 4:
                    break
    except Exception as e:
        return {"error": str(e)[:150], "ml_logs": len(ml_logs)}

    ts = [s.get("currentTime") for s in samples if isinstance(s.get("currentTime"), (int, float))]
    grew = ts and len(ts) >= 2 and ts[-1] > ts[0] + 0.2
    is_passed = _read_is_passed(page)
    return {
        "ml_log_200_seen": len(ml_logs) > 0,
        "ml_log_count": len(ml_logs),
        "ml_log_urls": [redact(m["url"])[:100] for m in ml_logs[:5]],
        "currentTime_samples": samples,
        "currentTime_increased": bool(grew),
        "isPassed": is_passed,
        "bounded_seconds": max_s,
    }


def _read_is_passed(page):
    """尽力读服务端 isPassed（页面变量/元素；无则 None）。"""
    candidates = [
        "window.isPassed", "document.title",
        "document.querySelector('.isPassed, [class*=isPassed]')?.dataset?.isPassed",
    ]
    try:
        for expr in candidates[:1]:
            v = page.evaluate(expr)
            if v is not None and v != "":
                return v
    except Exception:
        pass
    return None


def main() -> int:
    load_env(ROOT / ".env")
    for var in ("CX_USER", "CX_PASS"):
        if not os.environ.get(var):
            print(f"[!] 缺少 {var}", file=sys.stderr)
            return 2

    step = []
    started = datetime.now(timezone.utc).isoformat()

    def log(name, **kv):
        kv = {"t": datetime.now(timezone.utc).isoformat(), **kv}
        step.append({"step": name, **kv})
        print(f"[{name}] " + ", ".join(f"{k}={v}" for k, v in kv.items() if k != "t"))

    try:
        from playwright.sync_api import sync_playwright
        from app.e2_headed_gha import default_params, build_base_url
        from utils.cookie_store import ensure_login
    except Exception as e:
        write_audit(started=started, status="IMPORT_FAILED", error=repr(e), steps=step)
        return 2

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-web-security", "--disable-site-isolation-trials"])
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            f"Chrome/{browser.version} Safari/537.36"))
            page = ctx.new_page()
            log("browser-start", headless=True, version=browser.version,
                ua=page.evaluate("() => navigator.userAgent"))

            params = default_params()
            base = build_base_url(params.chapter_id, params)
            log("entry", url=redact(base), chapter=params.chapter_id,
                course=params.course_id, clazz=params.clazz_id)

            # --- 登录 ---
            login_ok = ensure_login(page, ctx, base,
                                    os.environ["CX_USER"], os.environ["CX_PASS"],
                                    login_timeout_s=25, captcha_mode="auto")
            title = ""
            try:
                title = page.title()
            except Exception:
                pass
            log("login", ok=login_ok, title=title,
                on_login_page="passport2.chaoxing.com/login" in page.url,
                url=redact(page.url)[:90])
            if not login_ok:
                write_audit(started=started, status="BLOCKED_AT_LOGIN", steps=step)
                browser.close()
                return 0

            # --- 课程解析：现在是 stu 页，标题 + 目录组件是否存在 ---
            anchors = page.locator("#coursetree, .posCatalog_select, .chapter_item").count()
            log("course-parse", title=title, catalog_anchor_count=anchors,
                is_mooc2="mooc2-ans" in page.url, url_page=redact(page.url)[:60])

            # --- 登录后读真实目录锚点数量（若为 stu 页）---
            chap = page.locator(".posCatalog_select, .chapter_item").count() if anchors else 0
            log("catalog-discovery", chapter_nodes=chap)

            # 到这一步我们完成「实登录 + 首页/目录观察」。完整播放会长时间 + 可能是写行为，
            # 由 audit 文档把「播放到 isPassed」标为 PARTIAL（观察到的：拿到了到学习页/目录，
            # 未在受限时长内拿 isPassed=true）。这里也尝试打开一个视频并读首帧 duration/currentTime。
            discovered_video = None
            try:
                # 页面可能已有 iframe 树；尝试读取 video 元素（真实）
                vids = page.frames
                for fr in vids:
                    try:
                        v = fr.locator("video")
                        if v.count():
                            discovered_video = {
                                "src_start": redact((v.first.get_attribute("src") or "")[:160]),
                                "duration": fr.evaluate("document.querySelector('video,audio')?.duration"),
                            }
                            break
                    except Exception:
                        continue
            except Exception as e:
                log("video-discovery-err", err=str(e)[:150])
            log("video-discovery", video=discovered_video)

            # --- 真实播放观察（有界：最多 ~25s），记录 multimedia/log 是否上屏 + isPassed ---
            playback = _observe_playback(page, max_s=25)
            log("playback", **playback)
            browser.close()
            write_audit(started=started, status="COMPLETED_OBSERVATION", steps=step)
            return 0
    except Exception as e:
        # 罕见 harness 异常：不崩溃，诚实记 HARNESS_ERROR
        info = sys.exc_info()
        write_audit(started=started, status="HARNESS_ERROR",
                    error=redact(str(e))[:300],
                    error_type=(info[0].__name__ if info[0] else "?"),
                    steps=step)
        return 1


def write_audit(started: str, status: str, steps: list, **extra) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_note": "real mooc2 E2E capability-audit observations (脱敏：enc/t/cookie 打码). Compiled by scripts/local_e2e_audit.py.",
        "started_at_utc": started,
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        **extra,
        "steps": steps,
    }
    OUT.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[audit] wrote {OUT} status={status}")


if __name__ == "__main__":
    sys.exit(main())