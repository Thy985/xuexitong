"""超星学习页 #coursetree 目录解析：把「章→节→任务点」层级映射落成可查键值。

数据源（任一兼容）：
  - 在线 DOM（消息：直接传 tvdp.fetch_course_discovery 已登录页面拿到的 HTML）。
  - 离线真实 HTML fixture（tests/fixtures/_raw_capture/dom_learning_tree.html）。

结构契约（与 tvdp._CATALOG_EXTRACT_JS / click_probe.py 一致，均已实测）：
  #coursetree 内每个章节节点是一个 `<div class="posCatalog_select …">`：
    - 章节点：class 含 `firstLayer`，`id="<数字>"`（不带 cur），名称用 `.posCatalog_title`，编号 `.posCatalog_sbar`（如 "4"）。
    - 节/任务点：class 不含 firstLayer，`id="cur<数字>"`，名称 `.posCatalog_name`，编号 `.posCatalog_sbar`（如 "4.6"）。
  顺序即目录顺序；节归属其之前最近的章。

build_chapter_map(html) -> {chapter_id: {"chapter","chapter_sbar","section","section_sbar"}}
纯正则，零第三方依赖。
"""
import re
from pathlib import Path

_SEL_RE = re.compile(
    r'<div[^>]*class="([^"]*posCatalog_select[^"]*)"[^>]*id="(cur)?(\d+)"[^>]*>(.*?)</div>',
    re.S,
)


def _sbar(block: str) -> str:
    m = re.search(r"posCatalog_sbar[^>]*>([^<]+)<", block)
    return m.group(1).strip() if m else ""


def _title(block: str) -> str:
    # 章/节标题都在 .posCatalog_title / .posCatalog_name 的 title 属性或紧跟>文本；
    # 注意 class 与后面的 attribute 之间是 `"`（如 class="posCatalog_name" title=…），
    # 因此用 [^>]*（允许空前/任意无 > 字符）而非 (?:\s+…)?。
    for cls in ("posCatalog_title", "posCatalog_name"):
        m = re.search(cls + r'[^>]*title="([^"]+)"', block)
        if m and m.group(1).strip():
            return m.group(1).strip()
    for cls in ("posCatalog_title", "posCatalog_name"):
        m = re.search(cls + r'[^>]*>([^<]{1,120})', block)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _cells(html: str) -> list[dict]:
    """返回全部 .posCatalog_select 节点（含 firstLayer 章），保 DOM 顺序。"""
    out = []
    for m in _SEL_RE.finditer(html):
        cls = m.group(1)
        is_cur = m.group(2)
        cid = m.group(3)
        block = m.group(4)[:800]
        first = "firstLayer" in cls
        # 节/任务点带 cur<id>；firstLayer 章 id 不带 cur 前缀
        if first:
            pass  # cid=ch cp 已在 group(3)
        elif not is_cur:
            continue  # 非 firstLayer 但缺 cur<id> → 非任务节点，跳过
        out.append({
            "chapter_id": cid.strip(),
            "sbar": _sbar(block),
            "title": _title(block),
            "first_layer": first,
        })
    return out


def build_chapter_map(html: str) -> dict:
    """解析整棵目录，返回 {chapter_id: {"chapter","chapter_sbar","section","section_sbar"}}。

    规则（与 DOM 顺序一致）：
      - firstLayer 节点 → 更新「当前章」（章标题=其 title，章号=其 sbar），章本身也入库。
      - 其后每个非 firstLayer 节点 → 归属到「当前章」（section=自身标题，section_sbar=自身编号）。
    """
    result = {}
    cur_chapter = "（未知章）"
    cur_bar = ""
    for c in _cells(html):
        cid = c["chapter_id"]
        if c["first_layer"]:
            cur_chapter = c["title"] or "（未知章）"
            cur_bar = c["sbar"] or cur_bar
        if not cid:
            continue
        if c["first_layer"]:
            result[cid] = {
                "chapter": cur_chapter, "chapter_sbar": cur_bar,
                "section": c["title"] or cur_chapter, "section_sbar": c["sbar"] or cur_bar,
            }
        else:
            result[cid] = {
                "chapter": cur_chapter, "chapter_sbar": cur_bar,
                "section": c["title"] or "（未命名节）", "section_sbar": c["sbar"] or cur_bar,
            }
    return result


def load_catalog(path_or_html) -> dict:
    """path_or_html 可为 Path 或已读出的 HTML 字符串。"""
    if hasattr(path_or_html, "read_text"):
        html = path_or_html.read_text(encoding="utf-8", errors="replace")
    elif isinstance(path_or_html, (str, bytes)) and ("posCatalog" in str(path_or_html)[:2000] if isinstance(path_or_html, str) else True):
        html = path_or_html.decode("utf-8", errors="replace") if isinstance(path_or_html, bytes) else path_or_html
    else:
        html = ""
    return build_chapter_map(html)


__all__ = ["build_chapter_map", "load_catalog"]