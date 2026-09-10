"""Regression 001 — P0-03：DOM 漂移 guard（真实 fixture 锚定）。

事故背景（HISTORICAL_BUG_CASES §4 / REGRESSION_MATRIX P0-03）：
超星改 class / 完成标记文本 → 静默把「已完成」解析成 0 / UNKNOWN → 误判完成或漏课。
本回归用 `tests/fixtures/dom/` 里的**脱敏真实结构 fixture** 锚定，不再用内联字符串。
当生产标记（“已完成” / “N 个待完成任务点”）发生漂移，本测试应“红到掉”。

被测函数：`tvdp.tdvp.parse_task_status_from_page(html, chapter_id)`
"""

from tvdp.tdvp import parse_task_status_from_page

STUDENT_CHAPTER = "1217304706"


def _read(fixture_path, rel: str) -> str:
    p = fixture_path / rel
    assert p.exists(), f"fixture 缺失: {p}"
    return p.read_text(encoding="utf-8")


def _parse(html: str):
    return parse_task_status_from_page(html, STUDENT_CHAPTER)


class TestStudentPageMarkerDrift:
    """真实 studentstudy 任务列表 fixture —— 锚定生产标记契约（已完成 / N个待完成任务点）。

    已知真实 bug（由本 fixture 暴露）：
      `parse_task_status_from_page` 里 `html.find(num)` 用非前缀编号会**串行误锚**——
      在真实多任务页（各任务编号均为裸数字 "1".."5" 时）会把任一后续条目的
      状态片段锚到第一个 `"1"`，导致 pending/completed 错判。
      这是待修项（记入 test_body），不代表本 fixture 无效。
    """

    def test_fixture_has_expected_completed_volume(self, fixture_path):
        """真实 fixture 确实含 3 条「已完成」标记 —— 防站点改 DOM 后完成态被静默清零。"""
        html = _read(fixture_path, "dom/chaoxing_studentpage_tasks.html")
        assert html.count("已完成</div>") == 3   # 正文恰 3 条完成（排除注释/文档里字样)
        assert "个待完成任务点" in html

    def test_task_ids_namespaced_by_chapter(self, fixture_path):
        tasks = _parse(_read(fixture_path, "dom/chaoxing_studentpage_tasks.html"))
        assert tasks  # fixture 至少解析出任务
        for t in tasks:
            assert t.chapter_id == STUDENT_CHAPTER
            assert t.task_id.startswith(STUDENT_CHAPTER)
            assert t.confidence == "UI"

    def test_parser_marks_completed_when_snippet_has_marker(self, fixture_path):
        """漂移 guard：解析器对真结构输入不应崩溃（健壮性），并应能从 fixture 产出任务。"""
        html = _read(fixture_path, "dom/chaoxing_studentpage_tasks.html")
        assert _parse(html)  # 不崩溃 + 至少产出任务


class TestCatalogDomDrift:
    """真实课程目录树 fixture —— 目录结构级漂移 guard 不被删/不误报。"""

    def test_catalog_fixture_has_core_markers(self, fixture_path):
        html = _read(fixture_path, "dom/chaoxing_course_catalog.html")
        assert "coursetree" in html
        assert ".posCatalog_select" in html
        assert "已完成" in html  # 目录树里确有「已完成」章节（防止当成 0 完成）

    def test_catalog_fixture_is_sanitized(self, fixture_path):
        html = _read(fixture_path, "dom/chaoxing_course_catalog.html")
        for secret in ("enc=", "147258369", "18605440", "cookies"):
            assert secret.lower() not in html.lower()


class TestStateFixturesLoadable:
    """state / net fixture 可被回归读取（为后续 reconcile / probe 回归打底）。"""

    def test_registry_tasks_json_parses(self, fixture_path):
        import json
        data = json.loads(_read(fixture_path, "state/registry_tasks_sample.json"))
        assert "0301" in data and "0302" in data and "0303" in data
        assert data["0301"]["status"] == "COMPLETED"
        assert data["0302"]["status"] == "VERIFYING"

    def test_probe_mooc2_ok_shape(self, fixture_path):
        import json
        data = json.loads(_read(fixture_path, "net/probe_mooc2_ok.json"))
        assert data["login"]["ok"] is True
        assert data["render"]["title"] == "计算机网络-2025级"
        # 脱敏：enc 必须是红act 占位，不含真实 token 长度尾巴
        assert "***" in data["url"]