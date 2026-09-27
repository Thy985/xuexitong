"""Shared domain models (single source of truth).

收敛此前散落在 resolvers / state / e2 各处的重复模型与隐式全局参数：

  - CourseIdentity  课程稳定身份（resolvers 与 state 共用，替代两份重复定义）
  - CourseParams     引擎运行参数（替代 e2_headed_gha 里被四处 E.* 注入的模块级全局）

统一通过 dataclass 显式传递，避免跨模块“靠 import 后手动赋值全局”的隐式耦合。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

# ── P0-2：账号隔离 namespace ────────────────────────────────────────
# 存储 identity 从「course_id_clazz_id」升级为「account_id / course_id_clazz_id」。
# 关键 invariant：same course + different account ≠ same state。
#
# account_id 来源（方案 B）：当前登录账号 CX_USER（超星手机号/账号，对同一账号恒定）。
# 使用确定性哈希做 namespace/key 规范（不是安全机制），保证稳定、一致、跨会话不变。
# 一旦将来拿到服务端稳定 uid（方案 A），只需替换 _resolve_account_id() 单点实现。
_ACCOUNT_ID_TEST_HOOK = None  # 测试注入：callable() -> str|None，优先于 env


def _resolve_account_id(cx_user: str) -> str:
    """保序的 16 进制短哈希：稳定、无碰撞即可用于目录命名（非安全）。"""
    return hashlib.sha256(cx_user.strip().encode("utf-8")).hexdigest()[:16]


def resolve_account_id(cx_user: Optional[str] = None) -> str:
    """返回当前登录账号的稳定 namespace id。

    优先级：测试 hook > 显式 cx_user > 环境变量 CX_USER。
    未设置任何账号（测试/本地无登录）→ 空串（legacy unscoped 存根），保持确定性。
    """
    global _ACCOUNT_ID_TEST_HOOK
    if _ACCOUNT_ID_TEST_HOOK is not None:
        got = _ACCOUNT_ID_TEST_HOOK()
        if got:
            return got
    cx = cx_user if cx_user is not None else os.environ.get("CX_USER")
    if not cx:
        return ""
    return _resolve_account_id(cx)


def set_account_id_hook(fn) -> None:
    """测试/工具注入：callable() -> str|None；None 则回退到 env。传 None 清除。"""
    global _ACCOUNT_ID_TEST_HOOK
    _ACCOUNT_ID_TEST_HOOK = fn


@dataclass
class CourseIdentity:
    """课程统一身份，不随 URL 中普通参数变化而改变。"""
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    raw_url: str
    resolved_at_utc: str
    account_id: str = ""

    def key(self) -> str:
        """生成稳定内部 key: course_id_clazz_id（课程级引用，不带账号）。"""
        return f"{self.course_id}_{self.clazz_id}"

    def scoped_key(self, account_id: Optional[str] = None) -> str:
        """生成**存储** key: account_id/course_id_clazz_id（P0-2 隔离边界）。

        未提供 account_id 时用当前登录账号；无登录（测试/legacy）→ 前缀为空，
        落回旧裸路径（离线诊断兼容）。"""
        acc = account_id if account_id is not None else resolve_account_id()
        if not acc:
            return self.key()
        return f"{acc}/{self.key()}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CourseIdentity":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CourseParams:
    """引擎运行参数——从 studentstudy URL 解析，显式传给 run/build_base_url。

    openc / hidetype 决定 cards iframe 是否渲染，缺失会导致 no_cards_frame，
    因此必须随主要参数一起保留透传。
    """
    course_id: str = ""
    clazz_id: str = ""
    cpi: str = ""
    enc: str = ""
    chapter_id: str = ""
    openc: Optional[str] = None
    hidetype: Optional[str] = None
    # 章内视频段序号（1-based）：若 >1，引擎需把播放推进到第 N 个视频点再正式播放
    # 并只把该段判完成（Options B：每 run 只处理一个视频任务点）。0/None = 默认按自然连播。
    video_index: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v}

    def build_base_url(self) -> str:
        """构造 studentstudy 页面 URL（保留 openc/hidetype，服务端据此渲染 cards iframe）。"""
        url = (
            "https://mooc1.chaoxing.com/mycourse/studentstudy?"
            f"chapterId={self.chapter_id}&courseId={self.course_id}"
            f"&clazzid={self.clazz_id}&cpi={self.cpi}&enc={self.enc}&mooc2=1"
        )
        parts = []
        if self.hidetype:
            parts.append(f"hidetype={self.hidetype}")
        if self.openc:
            parts.append(f"openc={self.openc}")
        if parts:
            url += "&" + "&".join(parts)
        return url

    @classmethod
    def from_url(cls, url: str | None) -> "CourseParams":
        """从 studentstudy URL 解析出全部运行参数（与 parse_course_url 等价）。"""
        if not url:
            return cls()
        q = parse_qs(urlparse(url).query)
        pick = lambda k: (q.get(k) or [None])[0]          # noqa: E731

        def _camel(k):
            return pick(k) or pick(k.lower()) or pick(k.upper())

        return cls(
            course_id=pick("courseId") or "",
            clazz_id=pick("clazzid") or pick("clazzId") or "",
            cpi=pick("cpi") or "",
            enc=pick("enc") or "",
            chapter_id=pick("chapterId") or "",
            openc=pick("openc"),
            hidetype=pick("hidetype"),
        )