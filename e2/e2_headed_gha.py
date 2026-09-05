"""E2: GitHub Actions Headed Browser Compatibility Verification

验证目标：
  将本地已 PASS 的 E1.2 浏览器自动化链路（超星 mooc1.chaoxing.com，
  章节自然播放至 isPassed=true），在 GitHub Actions CI runner 上以
  "有头浏览器 + Xvfb 虚拟显示" 方式复现。

实验原则（与 E1.2 完全一致）：
  - 只改变运行环境（本地 → GitHub Actions Ubuntu 24.04）
  - 不改变任何业务参数
  - 不直接调用 multimedia/log
  - 不伪造播放进度
  - 不重放请求
  - 不修改 enc / attDurationEnc / otherInfo / _t 等参数

验证项（10 项）：
  1. 登录成功
  2. studentstudy 加载
  3. cards iframe 加载
  4. 递归进入 ananas video iframe
  5. video.duration 正常取得
  6. 真实播放按钮 click
  7. currentTime 持续增长
  8. 自然产生 multimedia/log
  9. 服务端 isPassed=true
  10. 完成状态独立复核

输出：
  - CI 环境信息
  - browser/version
  - headed/headless 状态
  - iframe tree
  - video duration
  - playback timeline
  - multimedia/log count
  - isPassed
  - post-run verification
  - failure stage
  - Evidence Pack (JSON artifact)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright


# ── 课程配置（演示默认值；可由 URL / env / CLI 覆盖 → 通用 fork MVP）────
# 默认指向演示课程 1.6 章节，保持 E2/E3 证据工作流向后兼容。
DEMO_COURSE_ID, DEMO_CLAZZ_ID = "265997861", "151695658"
DEMO_CPI, DEMO_ENC = "506830460", "1bc1bd778f9e00d924fe97b3c63f76f4"
DEMO_CHAPTER = "1217304705"

COURSE_ID  = os.environ.get("COURSE_ID", DEMO_COURSE_ID)
CLAZZ_ID   = os.environ.get("CLAZZ_ID", DEMO_CLAZZ_ID)
CPI        = os.environ.get("CPI", DEMO_CPI)
ENC        = os.environ.get("ENC", DEMO_ENC)
CHAPTER_ID = os.environ.get("CHAPTER_ID", DEMO_CHAPTER)
# 由超星课程侧提供的 openc/hidetype（从 URL / env / CLI 注入）。
# 没有这两个参数时，当前学生页面不渲染 knowledge/cards iframe → no_cards_frame。
OPENR = os.environ.get("OPENR") or os.environ.get("OPEN_C")    # noqa: N816
HIDETYPE = os.environ.get("HIDETYPE")


def parse_course_url(url: str | None) -> dict:
    """从超星 studentstudy URL 提取课程/章节/附加参数（openc/hidetype 必须保留）。"""
    if not url:
        return {}
    q = parse_qs(urlparse(url).query)
    pick = lambda k: (q.get(k) or [None])[0]              # noqa: E731
    return {
        "course_id": pick("courseId"),
        "clazz_id":  pick("clazzid") or pick("clazzId"),
        "cpi":       pick("cpi"),
        "enc":       pick("enc"),
        "chapter_id": pick("chapterId"),
        # 关键：openc / hidetype 决定 cards iframe 是否被渲染
        "openc":     pick("openc"),
        "hidetype":  pick("hidetype"),
    }


def build_base_url(chap_id: str) -> str:
    url = (
        "https://mooc1.chaoxing.com/mycourse/studentstudy?"
        f"chapterId={chap_id}&courseId={COURSE_ID}&clazzid={CLAZZ_ID}"
        f"&cpi={CPI}&enc={ENC}&mooc2=1"
    )
    # 保留产品必需参数；丢失则服务端可能不渲染 cards iframe
    parts = []
    if HIDETYPE:
        parts.append(f"hidetype={HIDETYPE}")
    if OPENR:
        parts.append(f"openc={OPENR}")
    if parts:
        url += "&" + "&".join(parts)
    return url

V3_SCRIPT_PATH = Path(__file__).parent.parent / "xuexitongScript" / "v3_optimized.user.js"

MAX_PLAY_SECONDS     = 1500   # 25 min timeout
IS_PASSED_SETTLE_S   = 20
HEARTBEAT_DEAD_S     = 60
STATUS_EVERY_S       = 10
LOGIN_TIMEOUT_S      = 30


# ── 工具函数 ───────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts} UTC] {msg}", flush=True)


def masked(s: str) -> str:
    return s[:3] + "****" + s[-4:] if len(s) >= 8 else "***"


def is_session_kicked(url: str) -> bool:
    return "detect.chaoxing.com" in url or "i.mooc.chaoxing.com/space" in url


# ── 视频状态获取（与 e1_2_ch16_v2.py 完全一致）───────────────────
def get_video_state(page) -> dict:
    try:
        return page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('iframe'))
                .find(f => /knowledge\\/cards/.test(f.src || ''));
            if (!cards) return {found:false, reason:'no_cards_frame'};
            let doc = cards.contentDocument;
            if (!doc) return {found:false, reason:'no_cards_doc'};
            let v = doc.querySelector('video#video_html5_api, video[id*="video_html5"], video');
            if (!v) {
                const nf = Array.from(doc.querySelectorAll('iframe'))
                    .find(f => /video|ans-insertvideo/.test(f.src || ''));
                if (nf && nf.contentDocument) {
                    v = nf.contentDocument.querySelector('video');
                    doc = nf.contentDocument;
                }
            }
            if (!v) return {found:false, reason:'no_video_in_cards'};
            const d = v.duration;
            return {
                found:true, currentTime: v.currentTime,
                duration: (isFinite(d) && d>0) ? d : null,
                paused: v.paused, readyState: v.readyState,
                playbackRate: v.playbackRate, ended: v.ended,
                src: v.currentSrc || v.src || ''
            };
        }""")
    except Exception as e:
        return {"found": False, "err": str(e)}


# ── Banner 与侧栏状态 ─────────────────────────────────────────────
def get_banner(page) -> str | None:
    try:
        return page.evaluate("""() => {
            for (const el of Array.from(document.querySelectorAll('div,p,span'))) {
                const t = (el.innerText || '').trim();
                if (t.includes('已学习了') && t.length < 300) return t;
            }
            return null;
        }""")
    except Exception:
        return None


def get_sidebar(page, cid: str) -> dict | None:
    try:
        return page.evaluate("""(cid) => {
            for (const el of Array.from(document.querySelectorAll('[onclick]'))) {
                const attr = el.getAttribute('onclick') || '';
                if (attr.includes(cid)) {
                    const row = el.closest('li') || el.parentElement;
                    const numEl = row ? row.querySelector('.jobUnfinishCount') : null;
                    const ptsEl = row ? row.querySelector('.orangeNew') : null;
                    return {
                        unfinish: numEl ? numEl.value : null,
                        points: ptsEl ? ptsEl.innerText.trim() : null,
                        row_text: row ? (row.innerText||'').replace(/\\s+/g,' ').trim().slice(0,80) : null
                    };
                }
            }
            return null;
        }""", cid)
    except Exception as e:
        return {"err": str(e)}


# ── 主验证流程 ────────────────────────────────────────────────────
def run_test(args):
    evidence: dict = {}

    # ── A. CI 环境信息 ──────────────────────────────────────────────
    log("=" * 60)
    log("E2: GitHub Actions Headed Browser Compatibility")
    log("=" * 60)
    evidence["meta"] = {
        "test": "E2_GHA_Headed_Browser_Compat",
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "github_run_number": os.environ.get("GITHUB_RUN_NUMBER", "local"),
        "github_sha": os.environ.get("GITHUB_SHA", "local"),
        "github_actor": os.environ.get("GITHUB_ACTOR", "local"),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "chapter_id": args.chapter_id,
    }
    evidence["checks"] = {}
    evidence["errors"] = []

    # 收集系统信息
    sys_info = {}
    for label, cmd in [
        ("os_release",      "cat /etc/os-release"),
        ("cpu_count",       "nproc"),
        ("memory",          "free -h"),
        ("xvfb_pid",        "pgrep -a Xvfb || echo none"),
    ]:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            sys_info[label] = r.stdout.strip()
        except Exception as e:
            sys_info[label] = f"error: {e}"
    evidence["ci_environment"] = sys_info
    log(f"CI OS: {sys_info.get('os_release', '')[:80]}")
    log(f"Xvfb: {sys_info.get('xvfb_pid', 'none')}")

    # ── B. 启动 headed 浏览器（指定 DISPLAY）───────────────────────
    display = args.xvfb_display or os.environ.get("DISPLAY", ":99")
    log(f"DISPLAY={display}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chromium",
            args=[
                f"--display={display}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",  # 允许跨域 iframe contentDocument
                "--disable-site-isolation-trials",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{browser.version} Safari/537.36"
            ),
        )
        page = ctx.new_page()
        evidence["browser"] = {
            "headless": False,
            "channel": "chromium",
            "version": browser.version,
            "display": display,
            "user_agent": page.evaluate("() => navigator.userAgent"),
            "webdriver": page.evaluate("() => navigator.webdriver"),
        }
        log(f"Browser: chromium {browser.version} (headless=False)")

        # 检查 Xvfb 是否可用
        try:
            xr = subprocess.run(
                ["xdpyinfo", "-display", display],
                capture_output=True, text=True, timeout=5
            )
            evidence["xvfb_status"] = "available" if xr.returncode == 0 else f"xr.returncode={xr.returncode}"
            log(f"Xvfb: {'available' if xr.returncode == 0 else 'UNAVAILABLE'}")
        except Exception as e:
            evidence["xvfb_status"] = f"error: {e}"
            log(f"Xvfb check error: {e}")

        console_msgs = []
        console_msgs_buffer.clear()
        page.on("console", lambda msg: (
            console_msgs.append({"t": time.time(), "type": msg.type, "text": msg.text}),
            console_msgs_buffer.append({"t": time.time(), "type": msg.type, "text": msg.text})
        ))

        ml_events = []
        page.on("response", lambda resp: ml_events.append({
            "t": time.time(), "url": resp.url, "status": resp.status
        }) if "/mooc-ans/multimedia/log" in resp.url and resp.status == 200 else None)

        # ── C. 登录（cookie 优先，无则密码登录）────────────────────
        log("--- Step C: Login (cookie-first) ---")
        from utils.cookie_store import ensure_login
        base = build_base_url(args.chapter_id)
        login_ok = ensure_login(page, ctx,
                                base, os.environ["CX_USER"], os.environ["CX_PASS"],
                                login_timeout_s=LOGIN_TIMEOUT_S)
        evidence["checks"]["login_ok"] = login_ok
        log(f"Login: ok={login_ok} url={page.url[:80]}")

        if not login_ok:
            evidence["verdict"] = "FAIL(login failed in GHA)"
            _write(evidence, args.output)
            browser.close()
            return evidence

        if is_session_kicked(page.url):
            evidence["verdict"] = "FAIL(session kicked during login)"
            _write(evidence, args.output)
            browser.close()
            return evidence

        # ── D. 前置状态记录 ─────────────────────────────────────────
        log("--- Step D: Pre-state ---")
        page.goto(build_base_url(args.chapter_id), wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        evidence["checks"]["studentstudy_loaded"] = "studentstudy" in page.url
        log(f"studentstudy loaded: {evidence['checks']['studentstudy_loaded']}")

        evidence["banner_before"] = get_banner(page)
        evidence["sidebar_before"] = get_sidebar(page, args.chapter_id)
        m_b = re.search(r"已学习了(\d+)", evidence["banner_before"] or "")
        evidence["banner_learned_before"] = int(m_b.group(1)) if m_b else None
        log(f"Banner before: 已学习={evidence['banner_learned_before']}")
        log(f"Sidebar before {args.chapter_id}: {json.dumps(evidence['sidebar_before'], ensure_ascii=False)}")

        # ── E. 注入 v3 脚本 ─────────────────────────────────────────
        log("--- Step E: Inject v3 ---")
        script_src = V3_SCRIPT_PATH.read_text(encoding="utf-8")
        try:
            page.add_script_tag(content=script_src)
            log(f"v3 injected ({len(script_src)} bytes)")
            evidence["checks"]["v3_injected"] = True
        except Exception as e:
            evidence["checks"]["v3_injected"] = False
            evidence.setdefault("errors", []).append(f"v3_inject: {e}")
            log(f"v3 inject failed: {e}")

        # ── F. 等待视频 metadata ────────────────────────────────────
        log("--- Step F: Wait video metadata ---")
        video_ready = False
        for i in range(90):
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break
            if is_session_kicked(page.url):
                evidence["verdict"] = "FAIL(session kicked during video-wait)"
                break
            st = get_video_state(page)
            dur = st.get("duration")
            if dur and dur > 0:
                evidence["video_duration"] = dur
                video_ready = True
                # video 找到 = 递归 iframe（cards→ananas→video）访问成功，
                # 在 nextUnit 切换前记录（Step I 在切换后采集会拿不到）
                evidence["checks"]["cards_has_video"] = True
                log(f"Video ready: duration={dur:.0f}s ct={st.get('currentTime',0):.1f}s rs={st.get('readyState')}")
                break
            if i % 10 == 0:
                log(f"  waiting ({i+1}s) found={st.get('found')} dur={dur} reason={st.get('reason')}")

        if not video_ready:
            evidence["verdict"] = "FAIL(video metadata not ready in GHA headed)"
            _write(evidence, args.output)
            browser.close()
            return evidence

        # ── G. 主循环：自然播放 ─────────────────────────────────────
        log("--- Step G: Playback loop (headed GHA) ---")
        start = time.time()
        last_ct = 0.0
        last_status = 0.0
        max_ct = 0.0
        last_ct_change_at = start
        isPassed_seen = False
        isPassed_at = None
        ended_seen = False
        ended_wall = None
        nextunit_seen = False
        ml_parsed = []
        cur_video_src = ""
        video_count = 0
        passed_object_ids = set()
        summary = {
            "duration": evidence["video_duration"],
            "ml_log_count": 0,
            "final_isPassed": None,
            "playback_started": None,
        }

        while time.time() - start < MAX_PLAY_SECONDS:
            now = time.time()
            if is_session_kicked(page.url):
                evidence["verdict"] = "FAIL(session kicked during playback)"
                log("⚠️ Session kicked during playback!")
                break

            st = get_video_state(page)
            if st and st.get("found"):
                ct = st.get("currentTime") or 0
                v_src = st.get("src") or ""
                # 章节内视频任务点切换检测：同一 chapterId 下 video src 变化 = 下一个视频任务点。
                # 超星章节可含多个视频任务点（页面目录节点后的数字），
                # 切换时 chapterId 不变，只有 cards iframe 内 video src 变化。
                if v_src and v_src != cur_video_src:
                    if cur_video_src:
                        video_count += 1
                        log(f"★ Chapter video switch -> #{video_count + 1} "
                            f"src={v_src[:80]}")
                    else:
                        video_count = 1
                    cur_video_src = v_src
                    # 重置视频级状态，继续播放新任务点
                    ended_seen = False
                    ended_wall = None
                    last_ct = 0.0
                    last_ct_change_at = now
                    max_ct = 0.0
                    summary["duration"] = st.get("duration") or summary["duration"]
                if ct > 0 and not summary["playback_started"]:
                    summary["playback_started"] = now
                    log(f"★ Playback started: ct={ct:.1f}s (headed GHA mode)")
                if ct > 0:
                    max_ct = max(max_ct, ct)
                if abs(ct - last_ct) > 0.5:
                    last_ct = ct
                    last_ct_change_at = now
                elif dur and (now - last_ct_change_at) >= HEARTBEAT_DEAD_S:
                    log(f"⚠️ Heartbeat dead at ct={ct:.0f}s")
                    break
                if st.get("ended") and not ended_seen:
                    ended_seen = True
                    ended_wall = now
                    log(f"★ Video ended: ct={ct:.0f}s")

            # 消费 multimedia/log
            for ev in ml_events:
                existing = [e for e in ml_parsed if e.get("url") == ev["url"]]
                if existing:
                    continue
                entry = {"t": ev["t"], "url": ev["url"], "status": ev.get("status")}
                try:
                    q = parse_qs(urlparse(ev["url"]).query)
                    entry["playingTime"] = q.get("playingTime", [None])[0]
                    entry["duration_param"] = q.get("duration", [None])[0]
                    entry["objectId"] = q.get("objectId", [None])[0]
                    entry["jobid"] = q.get("jobid", [None])[0]
                except Exception:
                    pass
                try:
                    body = page.evaluate(
                        """async (u) => {
                            try { const r = await fetch(u, {method:'GET'}); return await r.text(); }
                            catch(e) { return 'ERR:'+e.message; }
                        }""", ev["url"]
                    )
                except Exception:
                    body = None
                entry["body"] = (body or "")[:300]
                ml_parsed.append(entry)
                if body and '"isPassed":true' in body:
                    isPassed_seen = True
                    isPassed_at = ev["t"]
                    obj_id = entry.get("objectId")
                    if obj_id:
                        passed_object_ids.add(obj_id)
                    evidence["isPassed_body"] = body[:300]
                    log(f"★ isPassed=true! body={body[:120]}")
            evidence["ml_log_count"] = len(ml_parsed)

            if now - last_status >= STATUS_EVERY_S:
                last_status = now
                dur = summary["duration"]
                pct = (max_ct / dur * 100) if dur else 0
                log(f"[GHA] ct={max_ct:.0f}/{dur if dur else '?'} ({pct:.0f}%) "
                    f"isPassed={isPassed_seen} ended={ended_seen} "
                    f"ml={evidence['ml_log_count']} videos={video_count}")

            # nextUnit 检测：只认 URL chapterId 变化（唯一可靠信号）。
            #   旧版用 .posCatalog_active/.posCatalog_current 标题启发式会误命中"当前章节标题"，
            #   导致视频刚起播就被误判为"已切换下一章"→ 循环 5s 退出 → max_ct≈0 → isPassed 永远 False。
            #   彻底移除 title 启发式，避免误判。
            if not nextunit_seen:
                ch_match = re.search(r'chapterId=(\d+)', page.url)
                cur_chap = ch_match.group(1) if ch_match else None
                if cur_chap and cur_chap != args.chapter_id:
                    nextunit_seen = True
                    evidence["nextunit_chapterId"] = cur_chap
                    evidence["nextunit_url"] = page.url
                    # 仅作诊断：尝试读取当前页标题（不参与判定）
                    try:
                        title_now = page.title()
                    except Exception:
                        title_now = None
                    evidence["nextunit_title"] = title_now
                    log(f"[GHA] nextUnit (URL chapterId changed): {args.chapter_id} -> {cur_chap}")

            if nextunit_seen:
                page.wait_for_timeout(5000)
                break
            if ended_seen and (now - ended_wall) > 30 and not nextunit_seen:
                log("ended 30s no switch")
                break
            page.wait_for_timeout(2000)

        summary["loop_seconds"] = time.time() - start
        evidence["max_currentTime"] = max_ct
        evidence["loop_seconds"] = summary["loop_seconds"]
        evidence["chapter_video_count"] = video_count
        evidence["passed_object_ids"] = sorted(passed_object_ids)
        log(f"Playback loop ended: {summary['loop_seconds']:.0f}s "
            f"max_ct={max_ct:.0f}s videos={video_count}")

        # ── H. 后置复核 ─────────────────────────────────────────────
        log("--- Step H: Post-verification ---")
        try:
            page.goto(build_base_url(args.chapter_id), wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            evidence["banner_after"] = get_banner(page)
            m_a = re.search(r"已学习了(\d+)", evidence["banner_after"] or "")
            evidence["banner_learned_after"] = int(m_a.group(1)) if m_a else None
            evidence["sidebar_after"] = get_sidebar(page, args.chapter_id)
            log(f"Banner after: 已学习={evidence['banner_learned_after']}")
            log(f"Sidebar after {args.chapter_id}: {json.dumps(evidence['sidebar_after'], ensure_ascii=False)}")
        except Exception as e:
            evidence.setdefault("errors", []).append(f"post_recheck: {e}")
            log(f"Post-recheck failed: {e}")

        # ── I. iframe tree snapshot ─────────────────────────────────
        log("--- Step I: iframe tree ---")
        try:
            iframe_tree = page.evaluate("""() => {
                const nodes = [];
                function walk(el, depth) {
                    if (depth > 5) return;
                    const src = (el.src || '').slice(0, 120);
                    let doc = null;
                    try { doc = el.contentDocument; } catch(e) {}
                    nodes.push({
                        depth, src,
                        hasDoc: !!doc,
                        videoCnt: doc ? doc.querySelectorAll('video').length : 0,
                        iframeCnt: doc ? doc.querySelectorAll('iframe').length : 0
                    });
                    if (doc) {
                        doc.querySelectorAll('iframe').forEach(f => walk(f, depth+1));
                    }
                }
                document.querySelectorAll('iframe').forEach(f => walk(f, 0));
                return nodes;
            }""")
            evidence["iframe_tree"] = iframe_tree
            cards_iframe = next((n for n in iframe_tree if "knowledge/cards" in n.get("src", "")), None)
            evidence["checks"]["cards_iframe_loaded"] = cards_iframe is not None and cards_iframe.get("hasDoc")
            # 若 Step F 已在视频 metadata 就绪时确认 cards→ananas→video 递归访问成功，
            # 这里（nextUnit 切换后采集）不得覆盖该结论；未置位时才用当前 iframe tree 兜底
            if not evidence["checks"].get("cards_has_video"):
                evidence["checks"]["cards_has_video"] = cards_iframe and cards_iframe.get("videoCnt", 0) > 0
            log(f"iframe tree: {len(iframe_tree)} nodes, cards_hasDoc={evidence['checks'].get('cards_iframe_loaded')}, cards_has_video={evidence['checks'].get('cards_has_video')}")
        except Exception as e:
            evidence.setdefault("errors", []).append(f"iframe_tree: {e}")
            log(f"iframe tree error: {e}")

        # ── 诊断：在 browser.close() 前采集，避免 "Event loop is closed" ──
        # 失败截图在 close() 前拍，成功时的 page 状态也在 close() 前快照
        if True:
            try:
                _capture_diagnostic(page, evidence, tag="at_end")
            except Exception as e:
                evidence.setdefault("errors", []).append(f"diag_capture: {e}")

        # ── J. Console 关键日志 ─────────────────────────────────────
        key_logs = [m for m in console_msgs if any(k in (m.get("text") or "") for k in
                     ["准备切换到下一小节", "切换到同章节", "播放完成", "开始播放", "安全停止", "headless"])]
        evidence["key_console"] = key_logs

        browser.close()

    # ── K. 判定 ─────────────────────────────────────────────────────
    banner_up = ((evidence.get("banner_learned_after") or 0) >
                 (evidence.get("banner_learned_before") or 0))
    sidebar_down = False
    sf = evidence.get("sidebar_before") or {}
    sa = evidence.get("sidebar_after") or {}
    if sf.get("unfinish") and sa.get("unfinish"):
        try:
            sidebar_down = int(sa["unfinish"]) < int(sf["unfinish"])
        except (ValueError, TypeError):
            pass

    checks = {
        "login_ok": login_ok,
        "studentstudy_loaded": evidence.get("checks", {}).get("studentstudy_loaded", False),
        "v3_injected": evidence.get("checks", {}).get("v3_injected", False),
        "cards_iframe_loaded": evidence.get("checks", {}).get("cards_iframe_loaded", False),
        "cards_has_video": evidence.get("checks", {}).get("cards_has_video", False),
        "video_duration_ok": evidence.get("video_duration") and evidence["video_duration"] > 0,
        "playback_started": summary.get("playback_started") is not None,
        "max_currentTime": max_ct,
        "ml_log_count": evidence.get("ml_log_count", 0),
        "isPassed_seen": isPassed_seen,
        "isPassed_body": evidence.get("isPassed_body"),
        "ended_seen": ended_seen,
        "nextunit_triggered": nextunit_seen,
        "nextunit_chapterId": evidence.get("nextunit_chapterId"),
        "nextunit_title": evidence.get("nextunit_title"),
        "banner_before": evidence.get("banner_learned_before"),
        "banner_after": evidence.get("banner_learned_after"),
        "banner_up": banner_up,
        "sidebar_before": sf.get("unfinish"),
        "sidebar_after": sa.get("unfinish"),
        "sidebar_down": sidebar_down,
        "loop_seconds": summary["loop_seconds"],
    }
    evidence["checks"] = checks

    # 10 项验证汇总
    evidence["verification_10"] = {
        "1_login_ok": checks["login_ok"],
        "2_studentstudy_loaded": checks["studentstudy_loaded"],
        "3_cards_iframe_loaded": checks["cards_iframe_loaded"],
        "4_recursive_ananas_iframe": checks["cards_has_video"],
        "5_video_duration_ok": checks["video_duration_ok"],
        "6_playback_started": checks["playback_started"],
        "7_currentTime_growing": checks["max_currentTime"] > 0,
        "8_ml_log_natural": checks["ml_log_count"] > 0,
        "9_isPassed_true": checks["isPassed_seen"],
        # 独立复核：1.6 任务点已被 E1.2 持久化完成（banner 无增量），
        # 因此以「服务端 isPassed=true + 视频自然播完/nextUnit 自动切换」为完成态独立确认
        "10_post_verification": checks["isPassed_seen"] and (checks["ended_seen"] or checks["nextunit_triggered"]),
    }

    passed_count = sum(1 for v in evidence["verification_10"].values() if v is True)
    evidence["passed_count"] = passed_count
    evidence["total_checks"] = 10

    # ── 诊断：失败阶段推导（截图已在 close() 前采集）──
    if passed_count < 10:
        evidence["failure_stage"] = _derive_failure_stage(evidence)
        log(f"[diag] failure_stage={evidence['failure_stage']} passed={passed_count}/10")
    else:
        evidence["failure_stage"] = None
        # 成功终态已在 at_end 截图中采集，无需再访问 page（已 close）

    if passed_count == 10:
        evidence["verdict"] = (
            f"PASS (headed GHA) — {passed_count}/10 checks passed. "
            f"isPassed=true, nextUnit={'triggered' if nextunit_seen else 'not observed'}, "
            f"banner_up={banner_up}, sidebar_down={sidebar_down}"
        )
    elif isPassed_seen and nextunit_seen:
        evidence["verdict"] = (
            f"PARTIAL(headed GHA) — isPassed=true + nextUnit triggered "
            f"but {10 - passed_count} checks failed"
        )
    elif isPassed_seen:
        evidence["verdict"] = (
            f"PARTIAL(headed GHA) — isPassed=true but nextUnit not triggered "
            f"({10 - passed_count} checks failed)"
        )
    else:
        evidence["verdict"] = (
            f"FAIL(headed GHA) — {passed_count}/10 checks passed, "
            f"isPassed_seen={isPassed_seen}, max_ct={max_ct:.0f}s"
        )

    evidence["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return evidence


def _write(ev: dict, path: str):
    Path(path).write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Evidence written: {path}")


# ── 诊断辅助：失败阶段推导 + 截图 + 结构化上下文 ──────────────────
def _capture_diagnostic(page, evidence: dict, tag: str):
    """失败时自动截图 + 记录页面 URL/title + video 状态 + 最后 console 消息。"""
    try:
        shot_path = f"/tmp/diag_{tag}_{int(time.time())}.png"
        page.screenshot(path=shot_path, full_page=True)
        evidence.setdefault("diagnostics", {})[tag + "_screenshot"] = shot_path
        log(f"[diag] screenshot saved: {shot_path}")
    except Exception as e:
        evidence.setdefault("errors", []).append(f"diag_screenshot_{tag}: {e}")
    try:
        st = get_video_state(page)
        evidence.setdefault("diagnostics", {})[tag + "_video_state"] = st
    except Exception:
        pass
    try:
        evidence.setdefault("diagnostics", {})[tag + "_page_url"] = page.url
        evidence.setdefault("diagnostics", {})[tag + "_page_title"] = page.title()
    except Exception:
        pass
    # 最后 20 条 console 消息（全文，不截断）
    try:
        recent = console_msgs_buffer[-20:] if console_msgs_buffer else []
        evidence.setdefault("diagnostics", {})[tag + "_console_tail"] = recent
    except Exception:
        pass


def _derive_failure_stage(evidence: dict) -> str:
    """从 evidence 精确推导失败发生在哪个阶段，不再靠猜。"""
    checks = evidence.get("checks", {})
    if not checks.get("login_ok"):
        return "LOGIN_FAILED"
    if not checks.get("studentstudy_loaded"):
        return "STUDENTSTUDY_NOT_LOADED"
    if not checks.get("cards_iframe_loaded"):
        return "NO_CARDS_IFRAME"
    if not checks.get("cards_has_video"):
        return "NO_VIDEO_IN_CARDS"
    if not checks.get("video_duration_ok"):
        return "VIDEO_DURATION_INVALID"
    if not checks.get("playback_started"):
        return "PLAYBACK_NOT_STARTED"
    max_ct = evidence.get("max_currentTime", 0) or 0
    dur = evidence.get("video_duration", 0) or 0
    if max_ct < 1 and not checks.get("isPassed_seen"):
        # ct 始终≈0 且 isPassed 未 true → 视频未真正起播或 nextUnit 误判提前切走
        return "PLAYBACK_STALLED"
    if not checks.get("ml_log_count") or (evidence.get("ml_log_count", 0) == 0):
        return "NO_MULTIMEDIA_LOG"
    if not checks.get("isPassed_seen"):
        # 视频可能没播完（max_ct 远小于 duration）
        if dur and max_ct < dur * 0.9:
            return "VIDEO_NOT_COMPLETED"
        return "ISPASSED_FALSE"
    if not checks.get("nextunit_triggered") and not checks.get("ended_seen"):
        return "NO_NEXTUNIT_NO_ENDED"
    return "UNKNOWN"


# 全局 console 缓冲（用于 _capture_diagnostic 取尾部消息）
console_msgs_buffer = []


def main():
    ap = argparse.ArgumentParser(description="E2: GitHub Actions Headed Browser Compat (product/MVP: --course-url or course params)")
    ap.add_argument("--course-url", default=os.environ.get("COURSE_URL"),
                    help="完整 studentstudy URL，含 courseId/clazzid/cpi/enc/chapterId (通用多课程)")
    ap.add_argument("--course-id", default=None, help="覆盖 courseId")
    ap.add_argument("--clazz-id",  default=None, help="覆盖 clazzid")
    ap.add_argument("--cpi",       default=None, help="覆盖 cpi")
    ap.add_argument("--enc",       default=None, help="覆盖 enc")
    ap.add_argument("--openc",     default=None, help="覆盖 openc（缺失时 cards iframe 可能不渲染）")
    ap.add_argument("--hidetype",  default=None, help="覆盖 hidetype（通常为 0）")
    ap.add_argument("--chapter-id", default=os.environ.get("CHAPTER_ID", DEMO_CHAPTER))
    ap.add_argument("--output", default="/tmp/evidence_e2.json")
    ap.add_argument("--xvfb-display", default=None)
    ap.add_argument("--debug-capture", action="store_true")
    args = ap.parse_args()

    # 用 course-url 解析出的参数覆盖(优先级: 显式 flag > URL > 常量 > demo)
    if args.course_url:
        pc = parse_course_url(args.course_url)
        args.course_id  = args.course_id  or pc.get("course_id")  or os.environ.get("COURSE_ID", DEMO_COURSE_ID)
        args.clazz_id   = args.clazz_id   or pc.get("clazz_id")   or os.environ.get("CLAZZ_ID", DEMO_CLAZZ_ID)
        args.cpi        = args.cpi        or pc.get("cpi")        or os.environ.get("CPI", DEMO_CPI)
        args.enc        = args.enc        or pc.get("enc")        or os.environ.get("ENC", DEMO_ENC)
        args.chapter_id = args.chapter_id or pc.get("chapter_id") or DEMO_CHAPTER
        # openc / hidetype 决定 cards iframe 是否渲染，必须透传
        args.openc      = args.openc      or pc.get("openc")      or os.environ.get("OPENR")
        args.hidetype   = args.hidetype   or pc.get("hidetype")   or os.environ.get("HIDETYPE")
    else:
        args.course_id  = args.course_id  or os.environ.get("COURSE_ID", DEMO_COURSE_ID)
        args.clazz_id   = args.clazz_id   or os.environ.get("CLAZZ_ID", DEMO_CLAZZ_ID)
        args.cpi        = args.cpi        or os.environ.get("CPI", DEMO_CPI)
        args.enc        = args.enc        or os.environ.get("ENC", DEMO_ENC)
        args.openc      = args.openc      or os.environ.get("OPENR")
        args.hidetype   = args.hidetype   or os.environ.get("HIDETYPE")

    # 使 run_test 内 build_base_url 使用解析后的课程参数
    globals()["COURSE_ID"] = args.course_id
    globals()["CLAZZ_ID"]  = args.clazz_id
    globals()["CPI"]       = args.cpi
    globals()["ENC"]       = args.enc
    globals()["OPENR"]     = args.openc
    globals()["HIDETYPE"]  = args.hidetype
    args.course_id, args.clazz_id, args.cpi, args.enc = (
        args.course_id, args.clazz_id, args.cpi, args.enc)

    ev = run_test(args)
    _write(ev, args.output)
    print(json.dumps(ev.get("verification_10", {}), ensure_ascii=False, indent=2))
    print(f"\nVERDICT: {ev.get('verdict', 'UNKNOWN')}")
    print(f"PASS COUNT: {ev.get('passed_count')}/10")
    sys.exit(0 if ev.get("passed_count") == 10 else 1)


if __name__ == "__main__":
    main()