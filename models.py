"""Shared domain models (single source of truth).

收敛此前散落在 resolvers / state / e2 各处的重复模型与隐式全局参数：

  - CourseIdentity  课程稳定身份（resolvers 与 state 共用，替代两份重复定义）
  - CourseParams     引擎运行参数（替代 e2_headed_gha 里被四处 E.* 注入的模块级全局）

统一通过 dataclass 显式传递，避免跨模块“靠 import 后手动赋值全局”的隐式耦合。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse


@dataclass
class CourseIdentity:
    """课程稳定身份，不随 URL 中普通参数变化而改变。"""
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    raw_url: str
    resolved_at_utc: str

    def key(self) -> str:
        """生成稳定内部 key: course_id_clazz_id。"""
        return f"{self.course_id}_{self.clazz_id}"

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