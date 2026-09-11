"""TDVP Module — Task Discovery & Verification Protocol

E7: 两阶段探针协议（Passive Probe + Active Probe）
-----------------------------------------------
1. Passive Probe（低成本）：从 studentstudy 页面解析章节/任务列表和 UI 完成标记
2. Active Probe（高成本）：对 pending/unknown 任务调用真实 Runtime 验证

注意：TDVP 内置于 Scheduler，用户只需传入 course_url，无需关心 chapter_id。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

# ── 类型定义 ───────────────────────────────────────────────────────
TaskStatus = Literal["COMPLETED", "PENDING", "UNKNOWN"]
ProbeSource = Literal["UI", "SERVER_VERIFIED", "UNKNOWN"]
TaskType = Literal["video", "quiz", "discussion", "other"]


# ── 数据模型 ───────────────────────────────────────────────────────

@dataclass
class TaskEvidence:
    status: TaskStatus
    confidence: ProbeSource
    source_detail: str
    observed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskInfo:
    task_id: str
    chapter_id: str
    title: str
    task_type: TaskType
    status: TaskStatus
    confidence: ProbeSource
    source_detail: str
    evidence: TaskEvidence
    discovered_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _ch_idx: int = field(default=0, repr=False)
    _cell_idx: int = field(default=0, repr=False)

    @property
    def key(self) -> str:
        return self.task_id

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "chapter_id": self.chapter_id,
            "title": self.title,
            "task_type": self.task_type,
            "status": self.status,
            "confidence": self.confidence,
            "source_detail": self.source_detail,
            "evidence": self.evidence.to_dict(),
            "discovered_at_utc": self.discovered_at_utc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskInfo":
        ev = d.pop("evidence", None)
        if ev is None:
            ev = TaskEvidence(
                status=d.get("status", "UNKNOWN"),
                confidence=d.get("confidence", "UNKNOWN"),
                source_detail=d.get("source_detail", ""),
            ).to_dict()
        return cls(
            task_id=d["task_id"],
            chapter_id=d.get("chapter_id", d["task_id"]),
            title=d.get("title", ""),
            task_type=d.get("task_type", "other"),
            status=d.get("status", "UNKNOWN"),
            confidence=d.get("confidence", "UNKNOWN"),
            source_detail=d.get("source_detail", ""),
            evidence=TaskEvidence(**ev) if isinstance(ev, dict) else TaskEvidence("UNKNOWN", "UNKNOWN", ""),
            discovered_at_utc=d.get("discovered_at_utc", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class ChapterInfo:
    chapter_id: str
    title: str
    tasks: list[TaskInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "task_count": len(self.tasks),
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass
class CourseDiscovery:
    course_id: str
    clazz_id: str
    course_key: str
    chapters: list[ChapterInfo]
    discovered_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def all_tasks(self) -> list[TaskInfo]:
        tasks = []
        for ch in self.chapters:
            tasks.extend(ch.tasks)
        return tasks

    @property
    def completed_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "COMPLETED"]

    @property
    def pending_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "PENDING"]

    @property
    def unknown_tasks(self) -> list[TaskInfo]:
        return [t for t in self.all_tasks if t.status == "UNKNOWN"]

    def to_dict(self) -> dict:
        return {
            "course_key": self.course_key,
            "course_id": self.course_id,
            "clazz_id": self.clazz_id,
            "chapter_count": len(self.chapters),
            "task_count": len(self.all_tasks),
            "completed_count": len(self.completed_tasks),
            "pending_count": len(self.pending_tasks),
            "unknown_count": len(self.unknown_tasks),
            "chapters": [c.to_dict() for c in self.chapters],
            "discovered_at_utc": self.discovered_at_utc,
        }


# ── Passive Probe ──────────────────────────────────────────────────

def _tdvp_course_params(params: dict) -> "CourseParams":
    """把 _parse_url_params 的 dict 转成 CourseParams（复用共享模型，替代 E.* 全局注入）。"""
    from models import CourseParams
    return CourseParams(
        course_id=params.get("course_id", ""),
        clazz_id=params.get("clazz_id", ""),
        cpi=params.get("cpi", ""),
        enc=params.get("enc", ""),
        chapter_id=params.get("chapter_id", ""),
        openc=params.get("openc"),
        hidetype=params.get("hidetype") or "0",
    )


def parse_task_status_from_page(html: str, chapter_id: str) -> list[TaskInfo]:
    """从 studentstudy 页面 HTML 解析任务列表。

    支持学习通实际 UI 标记：
      - 标题后跟 "已完成" → COMPLETED(UI)
      - 标题后跟 "N个待完成任务点" → PENDING(UI)
      - 标题后紧跟 >数字< → COMPLETED if >0 else PENDING
      - 其他 → UNKNOWN
    """
    tasks = []

    pattern_title = re.compile(
        r'([\d]+(?:\.[\d]+)?)\s+([\u4e00-\u9fff][\u4e00-\u9fff\s\w]{1,30})',
        re.UNICODE
    )
    matches = list(pattern_title.finditer(html))

    for m in matches:
        num = m.group(1)
        title = m.group(2)
        start = html.find(num)
        if start == -1:
            continue
        snippet = html[start:start+500]

        if "已完成" in snippet[:200]:
            status, confidence, detail = "COMPLETED", "UI", "标记=已完成"
        elif re.search(r'(\d+)个待完成', snippet):
            m2 = re.search(r'(\d+)个待完成', snippet)
            status, confidence, detail = "PENDING", "UI", f"标记={m2.group(1)}个待完成"
        elif re.search(r'>\s*(\d+)\s*<', snippet):
            val = int(re.search(r'>\s*(\d+)\s*<', snippet).group(1))
            status = "COMPLETED" if val > 0 else "PENDING"
            confidence = "UI"
            detail = f"UI marker={val}"
        else:
            status, confidence, detail = "UNKNOWN", "UI", "no status marker found"

        task_id = f"{chapter_id}_{num.replace('.', '_')}"
        tasks.append(TaskInfo(
            task_id=task_id,
            chapter_id=chapter_id,
            title=title.strip(),
            task_type="video",
            status=status,
            confidence=confidence,
            source_detail=detail,
            evidence=TaskEvidence(status, confidence, detail),
        ))

    return tasks


_CATALOG_EXTRACT_JS = """
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
            const m4 = nodeHtml.match(/getTeacherAjax\\('[^']*',\\s*"([^"]+)"/);
            if (m1) cid = m1[1];
            else if (m2) cid = m2[1];
            else if (m3) cid = m3[1];
            else if (m4) cid = m4[1];
            const isActive = cell.classList.contains('posCatalog_active');
            let jobRemaining = 0;
            const unf = cell.querySelector('input[type="hidden"][class*="UnfinishCount"], input[type="hidden"][class*="unfinish"], input[name*="job"]');
            if (unf && unf.value) {
                jobRemaining = parseInt(unf.value, 10) || 0;
            }
            results.push({
                chapter_id: cid, title: title, status: status,
                is_active: isActive, chapter_index: gi, cell_index: gi,
                text: text.slice(0, 150), mirrored: false,
                job_remaining: jobRemaining,
            });
        });
    }
    if (results.length === 0) {
        document.querySelectorAll('a[href*="chapterId"]').forEach(a => {
            const href = a.href || '';
            const m = href.match(/chapterId=(\\d+)/);
            if (!m) return;
            let container = a.closest('li, .catalog_list, tr, [class*="item"], [class*="node"]') || a.parentElement;
            const text = container ? (container.innerText || '') : '';
            results.push({
                chapter_id: m[1], title: (a.textContent || '').trim(),
                status: /已完成/.test(text) ? 'completed' : (/待完成/.test(text) ? 'pending' : 'unknown'),
                is_active: false, chapter_index: 0, cell_index: 0,
                text: text.slice(0, 150), mirrored: true,
            });
        });
    }
    return results;
}
"""


def extract_catalog_from_page(page, course_url: str) -> list[dict]:
    """从已登录的课程目录页提取章节列表（可在已打开的同 browser 里复用）。

    目录渲染竞态防护：真实站点的 #coursetree 先挂空 `<ul>`，章节节点是异步
    填充的。若 selector 一附加就提取，极易拿到空（CI/Xvfb 上尤其明显——
    对应 real run 34564369602「TDVP fetch returned empty」）。这里在首轮取
    得为空且 #coursetree 已存在时，轮询等 `.posCatalog_select` 出现（最多
    约 12s）再重取，显著降低「探针空→整轮空抓」抖动。
    """
    import time as _time
    import re as _re
    chapters = page.evaluate(_CATALOG_EXTRACT_JS)

    if not chapters:
        # 只等目录树子节点 hydration，不额外开新浏览器
        try:
            trees = page.locator("#coursetree").count()
        except Exception:
            trees = 0
        if trees:
            deadline = _time.time() + 12.0
            while _time.time() < deadline:
                try:
                    cells = page.locator(
                        "#coursetree .posCatalog_select:not(.firstLayer)").count()
                except Exception:
                    cells = 0
                if cells > 0:
                    break
                _time.sleep(1.5)
            chapters = page.evaluate(_CATALOG_EXTRACT_JS)

    by_title = {}
    for ch in chapters:
        t = ch.get("title", "")
        if not t:
            continue
        if t not in by_title or ch.get("chapter_id"):
            by_title[t] = ch
    unique = list(by_title.values())

    # 激活节点 chapterId 兜底
    current_url = page.url
    url_cid = ""
    m_url = _re.search(r'chapterId[=:](\d+)', current_url)
    if m_url:
        url_cid = m_url.group(1)
    for ch in unique:
        if ch.get("is_active") and not ch.get("chapter_id") and url_cid:
            ch["chapter_id"] = url_cid

    # 点击探测第一个缺 id 的非完成节点（仅切页，不播放）
    for ch in unique:
        if ch.get("status") != "completed" and not ch.get("chapter_id") and not ch.get("is_active"):
            try:
                clicked = page.evaluate("""(si) => {
                    const tree = document.querySelector('#coursetree');
                    if (!tree) return false;
                    const cells = tree.querySelectorAll('.posCatalog_select:not(.firstLayer)');
                    const list = Array.from(cells);
                    const target = list[si];
                    if (!target) return false;
                    const name = target.querySelector('.posCatalog_name');
                    if (!name) return false;
                    name.click(); return true;
                }""", ch.get("cell_index", 0))
                if clicked:
                    page.wait_for_timeout(4000)
                    m2 = _re.search(r'chapterId[=:](\d+)', page.url)
                    if m2:
                        ch["chapter_id"] = m2.group(1)
            except Exception:
                pass
            break
    return unique


def fetch_course_discovery(course_url: str, cx_user: Optional[str] = None,
                           cx_pass: Optional[str] = None) -> Optional[list[dict]]:
    """在浏览器 DOM 中直接提取目录树章节列表 + 状态。

    比 fetch_page_html 更可靠：不依赖 HTML 字符串正则，
    而是在活的 DOM 里查找所有带 chapterId 的链接和它们的完成状态标记。

    Returns:
        list of {chapter_id, title, status, text} 或 None
    """
    import os
    user = cx_user or os.environ.get("CX_USER")
    pw = cx_pass or os.environ.get("CX_PASS")
    if not user or not pw:
        return None

    try:
        from resolvers.course_resolver import _parse_url_params
        params = _parse_url_params(course_url)
        chapter_id = params.get("chapter_id") or ""

        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        from app import e2_headed_gha as E
        cp = _tdvp_course_params(params)

        from playwright.sync_api import sync_playwright
        display = os.environ.get("DISPLAY", ":99")

        with sync_playwright() as pwc:
            browser = pwc.chromium.launch(
                headless=False,
                channel="chromium",
                args=[f"--display={display}", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            f"Chrome/{browser.version} Safari/537.36"),
            )
            page = ctx.new_page()

            # ── 登录（cookie 优先，无则密码登录）─────────────────
            from utils.cookie_store import ensure_login
            base = E.build_base_url(chapter_id, cp)
            ensure_login(page, ctx, base, user, pw)

            # ── 导航到课程目录页 ──────────────────────────────────
            page.goto(course_url, wait_until="domcontentloaded", timeout=45000)
            # 目录树可渲染得慢（偶发空表）；等 selector 出现再提取。
            try:
                page.wait_for_selector(
                    "#coursetree, a[href*='chapterId']",
                    timeout=30000, state="attached",
                )
            except Exception:
                print("[tdvp] catalog tree selector not found before timeout "
                      "(still extracting)", file=sys.stderr)

            # ── DOM 提取目录树（借鉴 xuexitongScript/v3：#coursetree 结构）──
            # 结构： #coursetree > ul > li(章)  →  .posCatalog_select:not(.firstLayer)(小节)
            #       .posCatalog_active = 当前激活；.posCatalog_name = 标题
            js_extract = """
            () => {
                const results = [];
                const seenTitles = new Set();

                // 方法1：v3 脚本已知的 #coursetree 结构（超星学生学习页标准目录树）
                const tree = document.querySelector('#coursetree');
                if (tree) {
                    // 全局遍历所有非 firstLayer 的 posCatalog_select（保持 DOM 顺序）
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
                        // 从节点的 onclick / data 属性提取 chapterId
                        let cid = '';
                        const nodeHtml = cell.outerHTML || '';
                        const m1 = nodeHtml.match(/chapterId[=:'"](\\d+)/);
                        const m2 = nodeHtml.match(/data-?chapter[-_]?id[=:'"](\\d+)/);
                        const m3 = nodeHtml.match(/getTeacherAjax\\([^)]*,\\s*'([^']+)'/);
                        const m4 = nodeHtml.match(/getTeacherAjax\\([^)]*,\\s*"([^"]+)"/);
                        if (m1) cid = m1[1];
                        else if (m2) cid = m2[1];
                        else if (m3) cid = m3[1];
                        else if (m4) cid = m4[1];
                        // 从激活状态推断：当前 URL 的 chapterId 就是激活节点
                        const isActive = cell.classList.contains('posCatalog_active');
                        // E6.2: 读取本章节「待完成任务点」数量（hidden input），
                        // 用于把 chapter 拆分为 video + 残余 task，而不是单 TaskInfo。
                        let jobRemaining = 0;
                        const unf = cell.querySelector('input[type="hidden"][class*="UnfinishCount"], input[type="hidden"][class*="unfinish"], input[name*="job"]');
                        if (unf && unf.value) {
                            jobRemaining = parseInt(unf.value, 10) || 0;
                        }
                        results.push({
                            chapter_id: cid,
                            title: title,
                            status: status,
                            is_active: isActive,
                            chapter_index: gi,
                            cell_index: gi,
                            text: text.slice(0, 150),
                            mirrored: false,
                            job_remaining: jobRemaining,   // 待完成任务点数量
                        });
                    });
                }

                // 方法2：回退——任意含 chapterId 的链接
                if (results.length === 0) {
                    document.querySelectorAll('a[href*="chapterId"]').forEach(a => {
                        const href = a.href || '';
                        const m = href.match(/chapterId=(\\d+)/);
                        if (!m) return;
                        let container = a.closest('li, .catalog_list, tr, [class*="item"], [class*="node"]') || a.parentElement;
                        const text = container ? (container.innerText || '') : '';
                        results.push({
                            chapter_id: m[1],
                            title: (a.textContent || '').trim(),
                            status: /已完成/.test(text) ? 'completed' : (/待完成/.test(text) ? 'pending' : 'unknown'),
                            is_active: false,
                            chapter_index: 0, cell_index: 0,
                            text: text.slice(0, 150),
                            mirrored: true
                        });
                    });
                }
                return results;
            }
            """
            chapters = page.evaluate(js_extract)

            # 去重（同 title 只保留一条；有 chapterId 优先）
            by_title = {}
            for ch in chapters:
                t = ch.get("title", "")
                if not t:
                    continue
                if t not in by_title or ch.get("chapter_id"):
                    by_title[t] = ch
            unique = list(by_title.values())

            # 记录当前页面 URL 的 chapterId（激活节点的兜底映射）
            current_url = page.url
            url_cid = ""
            m_url = re.search(r'chapterId[=:](\d+)', current_url)
            if m_url:
                url_cid = m_url.group(1)
            for ch in unique:
                if ch.get("is_active") and not ch.get("chapter_id") and url_cid:
                    ch["chapter_id"] = url_cid

            # ── 点击探测：若存在未知节点的 chapter，但没有 chapterId → 点击 → 读 URL ──
            # 只点击第一个非激活节点，避免干扰页面状态（不播放视频，仅切换加载）
            picked = None
            for ch in unique:
                if ch.get("status") != "completed" and not ch.get("chapter_id") and not ch.get("is_active"):
                    picked = ch
                    break
            if picked and picked.get("chapter_index") is not None:
                try:
                    clicked = page.evaluate("""(si) => {
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
                    }""", picked.get("cell_index", 0))
                    if clicked:
                        page.wait_for_timeout(4000)
                        new_url = page.url
                        m2 = re.search(r'chapterId[=:](\d+)', new_url)
                        if m2:
                            picked["chapter_id"] = m2.group(1)
                except Exception as e:
                    print(f"[tdvp] click-probe error: {e}", file=sys.stderr)

            # dump 调试信息
            try:
                ev_dir = Path("./evidence")
                ev_dir.mkdir(parents=True, exist_ok=True)
                (ev_dir / "tdvp_discovery.json").write_text(
                    json.dumps(unique, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                (ev_dir / "tdvp_page.html").write_text(
                    page.content(), encoding="utf-8")
            except Exception:
                pass

            browser.close()
            return unique
    except Exception as e:
        print(f"[tdvp] fetch_course_discovery error: {e}", file=sys.stderr)
        return None


def fetch_course_detail_and_verify(
    course_url: str,
    target_cid: str = "",
    cx_user: Optional[str] = None,
    cx_pass: Optional[str] = None,
) -> Optional[dict]:
    """洞3：把「目录发现 + 队首章点级深读」合并进**一次**浏览器会话。

    只登录一次、只开一个 browser/context，既拿目录树（发现），又顺便对
    target_cid 打开其 cards 帧读真实点（L2 深度验证）。相比 `fetch_course_discovery`
    再单独 `live_verify_chapter`（两次 launch + 两次登录），显著降低 CI 的双开抖动。

    Returns:
        {"chapters": [...], "points": [{...}] }  （points 为空列表表示非 target/no 卡帧）
        失败返回 None。
    """
    import os
    user = cx_user or os.environ.get("CX_USER")
    pw = cx_pass or os.environ.get("CX_PASS")
    if not user or not pw:
        return None
    try:
        from resolvers.course_resolver import _parse_url_params
        params = _parse_url_params(course_url)
        chapter_id = params.get("chapter_id") or ""
        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        from app import e2_headed_gha as E
        cp = _tdvp_course_params(params)
        from playwright.sync_api import sync_playwright
        import re as _re
        display = os.environ.get("DISPLAY", ":99")

        with sync_playwright() as pwc:
            browser = pwc.chromium.launch(
                headless=False, channel="chromium",
                args=[f"--display={display}", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            f"Chrome/{browser.version} Safari/537.36"),
            )
            page = ctx.new_page()
            from utils.cookie_store import ensure_login
            ensure_login(page, ctx, E.build_base_url(chapter_id, cp), user, pw)

            page.goto(course_url, wait_until="domcontentloaded", timeout=45000)
            # 目录树渲染可慢于卡片帧（实测：本章点已返回、目录仍空表）。
            # 不再用固定 5s，改等目录树 selector 出现（最多 ~30s），降低
            # 'catalog 空 → 整轮空抓' 抖动；超时也照常 extract（fallback 兜底）。
            try:
                page.wait_for_selector(
                    "#coursetree, a[href*='chapterId']",
                    timeout=30000, state="attached",
                )
            except Exception:
                print("[tdvp] catalog tree selector not found before timeout "
                      "(still extracting)", file=sys.stderr)
            chapters = extract_catalog_from_page(page, course_url)

            points = []
            if target_cid:
                try:
                    loaded = read_chapter_job_points(
                        page, target_cid,
                        params.get("course_id", ""),
                        params.get("clazz_id", ""),
                        params.get("cpi", ""),
                    )
                    points = loaded or []
                except Exception as pe:
                    # 洞3弹：点级 deep-read 失败不影响已拿到的目录；后续由
                    # step4.5 的 live_verify 兜底（不在"一次读取"里多开一次浏览器前丢目录）。
                    print(f"[tdvp] combined point-read failed (catalog kept): {pe}",
                          file=sys.stderr)
            browser.close()
            return {"chapters": chapters or [], "points": points}
    except Exception as e:
        print(f"[tdvp] fetch_course_detail_and_verify error: {e}", file=sys.stderr)
        return None


def resolve_click_probe_chapter_id(course_url: str, ch_idx: int, cell_idx: int) -> Optional[str]:
    """点击探测：点击目录树中指定位置的节点，从 URL 提取 chapterId。

    仅在 fetch_course_discovery 拿不到节点的 chapterId 时使用。
    点击不会自动播放视频（只触发页面内章节切换）。
    """
    import os
    user = os.environ.get("CX_USER")
    pw = os.environ.get("CX_PASS")
    if not user or not pw:
        return None
    try:
        from resolvers.course_resolver import _parse_url_params
        params = _parse_url_params(course_url)
        chapter_id = params.get("chapter_id") or ""
        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        from app import e2_headed_gha as E
        cp = _tdvp_course_params(params)

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
            base = E.build_base_url(chapter_id, cp)
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
            page.goto(course_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # 点击指定位置的目录节点
            clicked = page.evaluate("""
                (ci, si) => {
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
            """, cell_idx)
            if not clicked:
                print(f"[tdvp] click-probe: click failed at ci={ch_idx}, si={cell_idx}", file=sys.stderr)
                browser.close()
                return None
            page.wait_for_timeout(4000)
            new_url = page.url
            m = re.search(r'chapterId[=:](\d+)', new_url)
            cid = m.group(1) if m else ""
            browser.close()
            print(f"[tdvp] click-probe: ci={ch_idx} si={cell_idx} → chapterId={cid or 'NOT_FOUND'}", flush=True)
            return cid or None
    except Exception as e:
        print(f"[tdvp] resolve_click_probe error: {e}", file=sys.stderr)
        return None


def fetch_page_html(course_url: str, cx_user: Optional[str] = None,
                    cx_pass: Optional[str] = None) -> Optional[str]:
    """轻量级页面抓取（兼容旧接口）：返回页面 HTML 字符串。

    新代码应优先用 fetch_course_discovery() 直接从 DOM 提取。
    """
    import os
    user = cx_user or os.environ.get("CX_USER")
    pw = cx_pass or os.environ.get("CX_PASS")
    if not user or not pw:
        return None

    try:
        from resolvers.course_resolver import _parse_url_params
        params = _parse_url_params(course_url)
        chapter_id = params.get("chapter_id") or ""

        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        from app import e2_headed_gha as E
        cp = _tdvp_course_params(params)

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
            base = E.build_base_url(chapter_id, cp)
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
            page.goto(course_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"[tdvp] fetch_page_html error: {e}", file=sys.stderr)
        return None


def build_tasks_from_discovery(chapters_raw: list[dict],
                               fallback_chapter: str = "",
                               video_counts: Optional[dict] = None) -> list[TaskInfo]:
    """将 DOM 提取的章节列表转换为「Chapter 内多个 Task」的 TaskInfo 列表（E6.2）。

    之前只生成 1 个 chapter TaskInfo 的原因（E6.2 §1）：
      旧实现把「每个目录节点（.posCatalog_select）」当作一个 task 直接映射，
      task_type 恒为 video，没有把 chapter 内部可能存在的多个 job/task 拆开。

    现在：每个 chapter 默认产出 1 个 video Task（runtime 支持）。
      若该章还有「待完成任务点」剩余（job_remaining > 0 / 非完成态），
      再补 1 个 other(unsupported) Task：用于告知 reconcile——
      「video 即使完成，也不能让 chapter 聚合为完成」（§9），且不会被 Queue 选中。

    chapter_raw = {chapter_id, title, status, text, cell_index, chapter_index, job_remaining}
    """
    video_counts = video_counts or {}
    tasks = []
    for ch in chapters_raw:
        title = ch.get("title", "").strip()
        if not title:
            continue
        cid = str(ch.get("chapter_id", ""))
        status_raw = ch.get("status", "unknown")
        if status_raw == "completed":
            status, conf = "COMPLETED", "UI"
        elif status_raw == "pending":
            status, conf = "PENDING", "UI"
        else:
            status, conf = "UNKNOWN", "UI"
        job_remaining = int(ch.get("job_remaining", 0) or 0)
        detail = ch.get("text", "")[:80]
        ch_idx = ch.get("chapter_index", 0)
        cell_idx = ch.get("cell_index", 0)
        task_id = cid if cid else f"_gi{ch_idx}"

        # 1) video task —— 每个真实视频点一个 Task；默认当视频点数量未知时视为 1 个。
        n_videos = int(video_counts.get(cid, 1) or 0)
        known_count = cid in video_counts   # 已知真实视频点数量
        if not known_count:
            n_videos = 1
        total_points = n_videos
        for vi in range(n_videos):
            vtid = task_id if vi == 0 else f"{task_id}:video{vi + 1}"
            v_detail = detail if vi == 0 else f"{vtid} 视频点 {vi + 1}/{n_videos}"
            tasks.append(TaskInfo(
                task_id=vtid,
                chapter_id=cid if cid else "",
                title=title,
                task_type="video",
                status=status,
                confidence=conf,
                source_detail=v_detail,
                evidence=TaskEvidence(status, conf, v_detail),
                _ch_idx=ch_idx,
                _cell_idx=cell_idx,
            ))

        # 2) 残余非 video task —— 当该章还有「非视频待完成任务点」
        not_all_done = (status_raw != "completed") or (job_remaining > 0)
        non_video_remain = job_remaining - total_points
        if cid and not_all_done and non_video_remain > 0:
            tasks.append(TaskInfo(
                task_id=f"{cid}:other",
                chapter_id=cid,
                title=title,
                task_type="other",          # 非 video → unsupported/pending
                status="PENDING",           # §6: 非 video 先记 pending/unsupported
                confidence="UI",
                source_detail=f"余 {non_video_remain} 个非视频任务点",
                evidence=TaskEvidence("PENDING", "UI",
                                      f"uncategorised; {non_video_remain} 待完成"),
                _ch_idx=ch_idx,
                _cell_idx=cell_idx,
            ))
    return tasks


# ── Live per-chapter calibration (E6.2) ───────────────────────────

# 该章内每个任务点由 .ans-job-icon 承载，完成与否看其 parent 是否带
# .ans-job-finished；类型看 icon 的附加类（.ans-job-video 已确认，quiz 等按约定）。


def _classify_job(marker_class: str) -> str:
    """从 .ans-job-icon 的 class 推断任务点类型。video 已确认，其余按约定。"""
    for t in ("video", "quiz", "exam", "document", "discussion", "homework"):
        if f"ans-job-{t}" in (marker_class or ""):
            return t
    return "other"


def read_chapter_job_points(
    page,
    knowledge_id: str,
    course_id: str,
    clazz_id: str,
    cpi: str,
) -> list[dict]:
    """L2 live verification：打开指定章节的 cards 帧，读其真实任务点列表。

    返回 [{task_id, type, title, finished}]，task_id 形如 <chapterId>（video）或
    <chapterId>:<type>。只对 conflict/STALE 的章节做，不用于被动 discovery
    （成本梯度：L1 catalog 便宜，L2 每章一次，L3 才真正重播）。
    """
    page.goto(
        f"https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId={knowledge_id}"
        f"&courseId={course_id}&clazzid={clazz_id}&cpi={cpi}"
        "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0",
        wait_until="domcontentloaded", timeout=30000,
    )
    page.wait_for_timeout(7000)

    points: list[dict] = []
    for fr in page.frames:
        if "knowledge/cards" not in fr.url:
            continue
        try:
            fr.wait_for_selector(".ans-job-item, .ans-job-icon", timeout=8000)
        except Exception:
            continue
        page.wait_for_timeout(1200)
        rows = fr.evaluate("""() => {
            const out = [];
            const seen = new Set();
            document.querySelectorAll('.ans-job-item, .ans-item, .ans-job-icon').forEach(n => {
                const icon = n.classList.contains('ans-job-icon')
                    ? n : n.querySelector('.ans-job-icon');
                if (!icon) return;
                // 完成标志在任务点最近的 .ans-job-item / .ans-item 上（confirmed）
                const item = icon.closest('.ans-job-item, .ans-item') || icon.parentElement;
                const finished = item ? item.classList.contains('ans-job-finished') : false;
                const marker = (icon.className||'').toString();
                let type = 'other';
                if (/\\bans-job-video\\b/i.test(marker)) type = 'video';
                else if (/ans-job-work|ans-homework/i.test(marker)) type = 'homework';
                else if (/ans-job-test|ans-job-19/i.test(marker)) type = 'quiz';
                else if (/ans-job-exam/i.test(marker)) type = 'exam';
                else if (/ans-job-discuss/i.test(marker)) type = 'discuss';
                else if (/ans-job-pdf|ans-job-read|ans-job-doc/i.test(marker)) type = 'document';
                else {
                    // 内容级启发式（confirmed 视频：video/ananas iframe；文本 fallback）
                    const hasVideo = item.querySelector('video, [class*=video_html5], iframe[src*=ananas], .videoContainer, .ans-insertvideo');
                    const txt = (item.innerText || '');
                    if (hasVideo || /观看.*视频|播放|总时长的?\\s*\\d+%|视频点/i.test(txt)) type = 'video';
                    else if (/达标测试|测验|测试|作业|考试/i.test(txt)) type = 'quiz';
                }
                const key = marker + '|' + (item.innerText||'').slice(0,20);
                if (seen.has(key)) return; seen.add(key);
                out.push({ marker, type, isFinished: finished,
                           titleText: (item.innerText||'').trim().replace(/\\s+/g,' ').slice(0,60) });
            });
            return out;
        }""")
        video_seen = 0
        for r in rows:
            typ = r["type"] or _classify_job(r["marker"] or "")
            if typ == "video":
                # 第 1 个视频沿用章节 id（与 registry 的 video task_id 一致）；
                # 第 2+ 个用 <chapterId>:video<idx>（与 build_tasks 的多视频拆分一致）。
                video_seen += 1
                r["task_id"] = knowledge_id if video_seen == 1 else f"{knowledge_id}:video{video_seen}"
            else:
                r["task_id"] = f"{knowledge_id}:{typ}"
            r["type"] = typ
            points.append(r)
        break
    return points


def build_live_pending(job_points: list[dict]) -> set[str]:
    """从一章的实时 job 点列表推导「当前确实未完成」的 task_id 集合。

    Registry 里一个章节的 video task 会用章节 id 作 task_id（不加后缀）。
    若该章存在未 finished 的 video 点 → 该 video task 应降级。
    """
    return {p["task_id"] for p in job_points if not p.get("isFinished")}


def chapter_video_summary(job_points: list[dict]) -> tuple[int, int]:
    """返回一章的实时 job 点里 (视频点总数, 已完成视频点数)。

    用于 Discovery 拆分多视频章：当一章有 N>1 个视频且未全部完成时，
    build_tasks_from_discovery 用 N 生成 N 个视频 task，逐个进入队列。
    返回 (total, finished)；无视频点时 total=0。
    """
    videos = [p for p in job_points if p.get("type") == "video"]
    total = len(videos)
    finished = sum(1 for p in videos if p.get("isFinished"))
    return total, finished


def live_verify_chapter(
    knowledge_id: str,
    course_id: str,
    clazz_id: str,
    cpi: str,
    cx_user: str,
    cx_pass: str,
) -> Optional[dict]:
    """独立章节 live 复核：打开浏览器 → read_chapter_job_points。

    Returns dict 含 {points, video_total, video_finished, live_pending}；
    失败返回 None。供 scheduler 在选任务前对目标章做 L2 实校（成本有界）。
    """
    from playwright.sync_api import sync_playwright
    import os
    user = cx_user or os.environ.get("CX_USER")
    pw = cx_pass or os.environ.get("CX_PASS")
    if not user or not pw:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
        from utils.cookie_store import ensure_login
        with sync_playwright() as p:
            b = p.chromium.launch(
                headless=False, channel="chromium",
                args=["--no-sandbox", "--disable-gpu"],
            )
            pg = b.new_page()
            ensure_login(pg, pg.context, "https://mooc1.chaoxing.com", user, pw)
            cid_old = knowledge_id
            pts = read_chapter_job_points(pg, cid_old, course_id, clazz_id, cpi)
            b.close()
    except Exception as e:
        print(f"[tdvp] live_verify_chapter error: {e}", file=sys.stderr)
        return None
    total, finished = chapter_video_summary(pts)
    return {
        "points": pts,
        "video_total": total,
        "video_finished": finished,
        "live_pending": build_live_pending(pts),
    }


def aggregate_evidence(
    passive_results: dict[str, TaskInfo],
    active_results: Optional[dict[str, TaskInfo]] = None,
) -> dict[str, TaskInfo]:
    """合并被动探测和主动验证结果，SERVER_VERIFIED 优先级高于 UI。"""
    merged = {}
    for task_id, task in passive_results.items():
        merged[task_id] = task

    if active_results:
        for task_id, active_task in active_results.items():
            if task_id not in merged:
                merged[task_id] = active_task
                continue
            existing = merged[task_id]
            if active_task.confidence == "SERVER_VERIFIED":
                merged[task_id] = active_task
            elif active_task.status == "COMPLETED" and existing.status == "PENDING":
                merged[task_id] = TaskInfo(
                    task_id=task_id,
                    chapter_id=active_task.chapter_id,
                    title=active_task.title,
                    task_type=active_task.task_type,
                    status="COMPLETED",
                    confidence="SERVER_VERIFIED",
                    source_detail=f"active_probe_verified({existing.source_detail})",
                    evidence=TaskEvidence("COMPLETED", "SERVER_VERIFIED",
                                          f"active_probe_verified({existing.source_detail})"),
                )
    return merged


# ── Task Registry ──────────────────────────────────────────────────

TASKS_FILE = Path(__file__).parent.parent / "state" / "tdvp_tasks.json"


def load_task_registry(course_key: str) -> dict[str, TaskInfo]:
    if not TASKS_FILE.exists():
        return {}
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        return {k: TaskInfo.from_dict(v) for k, v in data.get(course_key, {}).items()}
    except Exception:
        return {}


def save_task_registry(course_key: str, tasks: dict[str, TaskInfo]) -> None:
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8")) if TASKS_FILE.exists() else {}
    except Exception:
        data = {}
    data[course_key] = {k: v.to_dict() for k, v in tasks.items()}
    tmp = TASKS_FILE.with_suffix(TASKS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TASKS_FILE)


def get_pending_tasks(course_key: str, registry: Optional[dict[str, TaskInfo]] = None) -> list[TaskInfo]:
    if registry is None:
        registry = load_task_registry(course_key)
    return sorted(
        [t for t in registry.values() if t.status in ("PENDING", "UNKNOWN")],
        key=lambda t: t.task_id,
    )


def get_completed_tasks(course_key: str, registry: Optional[dict[str, TaskInfo]] = None) -> list[TaskInfo]:
    if registry is None:
        registry = load_task_registry(course_key)
    return [t for t in registry.values() if t.status == "COMPLETED"]


# ── Progress Synchronization ──────────────────────────────────────

def sync_progress_to_course_state(course_key: str, discovered: CourseDiscovery) -> dict:
    """将发现结果同步到 course_state.json 的 task_queue。"""
    from state.course_state import load_course_state, save_course_state, CourseProgress

    state = load_course_state(course_key)
    if not state:
        return {"error": f"No course state for {course_key}"}

    all_tasks = discovered.all_tasks
    completed = discovered.completed_tasks
    pending = discovered.pending_tasks
    unknown = discovered.unknown_tasks

    state.progress = CourseProgress(
        completed=len(completed),
        total=len(all_tasks),
        last_completed_task=completed[-1].task_id if completed else None,
        active_task=pending[0].task_id if pending else None,
    )

    task_queue = [t.task_id for t in sorted(pending + unknown, key=lambda t: t.task_id)]

    if not hasattr(state, 'discoveries'):
        state.discoveries = []
    state.discoveries.append({
        "discovered_at_utc": discovered.discovered_at_utc,
        "total_tasks": len(all_tasks),
        "completed": len(completed),
        "pending": len(pending),
        "unknown": len(unknown),
    })

    save_course_state(state)
    return {
        "course_key": course_key,
        "total": len(all_tasks),
        "completed": len(completed),
        "pending": len(pending),
        "unknown": len(unknown),
        "task_queue": task_queue,
        "next_task": task_queue[0] if task_queue else None,
    }


# ── Discovery ──────────────────────────────────────────────────────

def discover_course(course_url: str, html: str, chapter_id: Optional[str] = None) -> CourseDiscovery:
    """从 HTML 中解析课程所有章节的任务状态。"""
    from resolvers.course_resolver import _parse_url_params

    params = _parse_url_params(course_url)
    course_id = params.get("course_id", "")
    clazz_id = params.get("clazz_id", "")
    course_key = f"{course_id}_{clazz_id}"

    target_chapter = chapter_id or params.get("chapter_id")
    chapters = {}

    if target_chapter:
        tasks = parse_task_status_from_page(html, target_chapter)
        chapters[target_chapter] = ChapterInfo(
            chapter_id=target_chapter,
            title=f"Chapter {target_chapter}",
            tasks=tasks,
        )
    else:
        chapter_ids = set()
        if params.get("chapter_id"):
            chapter_ids.add(params["chapter_id"])
        for m in re.finditer(r'chapterId[=:](\d+)', html):
            chapter_ids.add(m.group(1))

        for cid in sorted(chapter_ids):
            tasks = parse_task_status_from_page(html, cid)
            chapters[cid] = ChapterInfo(chapter_id=cid, title=f"Chapter {cid}", tasks=tasks)

    return CourseDiscovery(
        course_id=course_id,
        clazz_id=clazz_id,
        course_key=course_key,
        chapters=list(chapters.values()),
    )


def run_passive_probe(course_url: str, html: str, chapter_id: Optional[str] = None) -> CourseDiscovery:
    """执行 Passive Probe（纯 HTML 解析，不启动浏览器）。"""
    return discover_course(course_url, html, chapter_id=chapter_id)
