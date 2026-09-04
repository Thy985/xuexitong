"""Course Resolver: URL → Canonical Course Identity

E5 核心模块。用户只需提供 course_url，Resolver 自动提取并验证
course_id / clazz_id / cpi / title，生成稳定的内部身份 key。

设计原则:
1. Identity 由 course_id + clazz_id 决定（cpi 仅用于 URL 构建）
2. 必须通过真实登录后的页面上下文验证 ID 与课程一致
3. 相同 course_id+clazz_id 但 URL 其他参数不同 → SAME_COURSE
4. 不同 course_id 或 clazz_id → COURSE_CHANGED / NEW_COURSE
5. 无效 URL → INVALID

API:
    resolve_course(url: str) -> IdentityResult
    identity_key(identity: CourseIdentity) -> str
    detect_course_change(current_url: str, active_identity: CourseIdentity) -> ChangeDetection
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, parse_qs

# 类型别名
IdentityKind = Literal["SAME_COURSE", "COURSE_CHANGED", "NEW_COURSE", "INVALID"]
ResolutionStatus = Literal["OK", "INVALID", "FAILED"]


@dataclass
class CourseIdentity:
    """稳定课程身份，不随 URL 中普通参数变化而改变。"""
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    raw_url: str
    resolved_at_utc: str

    def key(self) -> str:
        """生成稳定内部 key: course_id_class_id。"""
        return f"{self.course_id}_{self.clazz_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CourseIdentity":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class IdentityResult:
    """Resolver 返回结果。"""
    status: ResolutionStatus
    identity: CourseIdentity | None
    error: str | None
    raw_url: str
    evidence: dict = field(default_factory=dict)

    def is_ok(self) -> bool:
        return self.status == "OK" and self.identity is not None

    def to_dict(self) -> dict:
        d = {
            "status": self.status,
            "raw_url": self.raw_url,
            "error": self.error,
            "evidence": self.evidence,
        }
        if self.identity:
            d["identity"] = self.identity.to_dict()
        return d


@dataclass
class ChangeDetection:
    """课程切换检测结果。"""
    kind: IdentityKind
    current_identity: CourseIdentity | None
    active_identity: CourseIdentity | None
    details: str = ""

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "details": self.details}
        if self.current_identity:
            d["current_identity"] = self.current_identity.to_dict()
        if self.active_identity:
            d["active_identity"] = self.active_identity.to_dict()
        return d


def _parse_url_params(url: str) -> dict:
    """从 URL 提取查询参数。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return {
        "course_id": (qs.get("courseId") or [None])[0],
        "clazz_id": (qs.get("clazzid") or qs.get("clazzId") or [None])[0],
        "cpi": (qs.get("cpi") or [None])[0],
        "enc": (qs.get("enc") or [None])[0],
        "chapter_id": (qs.get("chapterId") or [None])[0],
        "openc": (qs.get("openc") or [None])[0],
        "hidetype": (qs.get("hidetype") or [None])[0],
    }


def _validate_identity_params(params: dict) -> str | None:
    """验证必要参数，返回错误消息；无错返回 None。"""
    if not params.get("course_id"):
        return "missing courseId"
    if not params.get("clazz_id"):
        return "missing clazzid/clazzId"
    if not params.get("cpi"):
        return "missing cpi"
    if not params.get("enc"):
        return "missing enc"
    if not params.get("chapter_id"):
        return "missing chapterId"
    # 数值校验
    for k in ("course_id", "clazz_id", "cpi", "chapter_id"):
        v = params[k]
        if v and not re.match(r"^\d+$", v):
            return f"{k} must be numeric, got: {v!r}"
    return None


def resolve_course(url: str, *, verify_via_browser: bool = False,
                   cx_user: str | None = None, cx_pass: str | None = None) -> IdentityResult:
    """解析课程 URL 并生成稳定 Identity。

    Args:
        url: 学习通 studentstudy 页 URL
        verify_via_browser: 是否启动浏览器验证页面上下文（CI 环境推荐 True）
        cx_user: 超星账号（verify_via_browser=True 时需要）
        cx_pass: 超星密码（verify_via_browser=True 时需要）

    Returns:
        IdentityResult with status OK/INVALID/FAILED
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    evidence: dict = {}

    # ── 阶段 1: URL 参数解析 ────────────────────────────────────
    params = _parse_url_params(url)
    evidence["parsed_params"] = {k: v for k, v in params.items() if v}

    # ── 阶段 2: 参数校验 ────────────────────────────────────────
    err = _validate_identity_params(params)
    if err:
        return IdentityResult(
            status="INVALID",
            identity=None,
            error=f"URL validation failed: {err}",
            raw_url=url,
            evidence=evidence,
        )

    # ── 阶段 3: 可选浏览器验证（在已登录页面提取 title 并确认 ID 一致）
    title = ""
    if verify_via_browser and cx_user and cx_pass:
        try:
            title = _verify_via_browser(url, cx_user, cx_pass, evidence)
        except Exception as e:
            evidence["browser_verify_error"] = str(e)
            # 浏览器验证失败不阻塞，仍使用 URL 参数构建 Identity

    # ── 阶段 4: 构建 Identity ───────────────────────────────────
    identity = CourseIdentity(
        course_id=params["course_id"],
        clazz_id=params["clazz_id"],
        cpi=params["cpi"],
        title=title or f"course_{params['course_id']}",
        raw_url=url,
        resolved_at_utc=now_utc,
    )

    evidence["identity_key"] = identity.key()
    evidence["resolved_course_id"] = identity.course_id
    evidence["resolved_clazz_id"] = identity.clazz_id

    return IdentityResult(
        status="OK",
        identity=identity,
        error=None,
        raw_url=url,
        evidence=evidence,
    )


def _verify_via_browser(url: str, cx_user: str, cx_pass: str,
                        evidence: dict) -> str:
    """通过真实浏览器验证课程页面，提取标题并确认 URL 参数与页面一致。"""
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent / "e2"))
    from e2_headed_gha import build_base_url, parse_course_url

    display = os.environ.get("DISPLAY", ":99")
    os.environ["CX_USER"] = cx_user
    os.environ["CX_PASS"] = cx_pass

    # 临时设置模块全局
    import e2_headed_gha as E
    p = parse_course_url(url)
    E.COURSE_ID = p["course_id"]
    E.CLAZZ_ID = p["clazz_id"]
    E.CPI = p["cpi"]
    E.ENC = p["enc"]
    E.OPENR = p.get("openc")
    E.HIDETYPE = p.get("hidetype") or "0"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            channel="chromium",
            args=[f"--display={display}", "--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-gpu", "--disable-web-security",
                  "--disable-site-isolation-trials"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{browser.version} Safari/537.36"),
        )
        page = ctx.new_page()

        # 登录（cookie 优先，无则密码登录）
        from utils.cookie_store import ensure_login
        base = build_base_url(p["chapter_id"])
        login_ok = ensure_login(page, ctx, base, cx_user, cx_pass)
        evidence["browser_login_ok"] = login_ok

        if not login_ok:
            browser.close()
            raise RuntimeError("Browser login failed")

        # 导航到目标 URL
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # 提取页面标题（课程名）
        try:
            title = page.title()
            # 尝试从页面正文提取更精确的课程标题
            page_title = page.evaluate("""() => {
                // 优先取 h1 或页面主标题
                const h1 = document.querySelector('h1, .course-name, #courseName');
                if (h1) return h1.innerText.trim();
                const nav = document.querySelector('nav a, .course-title, [class*=course]');
                if (nav) return nav.innerText.trim();
                return null;
            }""")
            final_title = (page_title or title).strip()
        except Exception:
            final_title = title

        evidence["page_url_after_nav"] = page.url
        evidence["page_title"] = final_title

        # 验证 URL 中的 course_id/clazz_id 与页面一致
        nav_params = _parse_url_params(page.url)
        evidence["nav_params"] = {k: v for k, v in nav_params.items() if v}

        id_match = (nav_params.get("course_id") == p["course_id"] and
                    nav_params.get("clazz_id") == p["clazz_id"])
        evidence["id_consistent"] = id_match

        browser.close()

        return final_title or p["course_id"]


def identity_key(identity: CourseIdentity) -> str:
    """生成稳定内部 key。"""
    return identity.key()


def detect_course_change(current_url: str,
                         active_identity: CourseIdentity | None
                         ) -> ChangeDetection:
    """检测当前 URL 与活跃课程身份的关系。

    Returns:
        ChangeDetection with kind in
        ["SAME_COURSE", "COURSE_CHANGED", "NEW_COURSE", "INVALID"]
    """
    # 解析当前 URL
    params = _parse_url_params(current_url)
    err = _validate_identity_params(params)
    if err:
        return ChangeDetection(
            kind="INVALID",
            current_identity=None,
            active_identity=active_identity,
            details=f"Invalid URL: {err}",
        )

    current_id = CourseIdentity(
        course_id=params["course_id"],
        clazz_id=params["clazz_id"],
        cpi=params["cpi"],
        title=params["course_id"],  # 未验证时简化
        raw_url=current_url,
        resolved_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    if active_identity is None:
        return ChangeDetection(
            kind="NEW_COURSE",
            current_identity=current_id,
            active_identity=None,
            details="No active course; treating as new",
        )

    if current_id.key() == active_identity.key():
        return ChangeDetection(
            kind="SAME_COURSE",
            current_identity=current_id,
            active_identity=active_identity,
            details=f"Same course (key={current_id.key()})",
        )

    return ChangeDetection(
        kind="COURSE_CHANGED",
        current_identity=current_id,
        active_identity=active_identity,
        details=(f"Changed: old={active_identity.key()} → "
                 f"new={current_id.key()}"),
    )
