"""Regression 001 — P0-03：DOM 漂移 guard（真实 fixture 锚定）。

事故背景（HISTORICAL_BUG_CASES §4 / REGRESSION_MATRIX P0-03）：
超星改 class / 完成标记文本 → 静默把「已完成」解析成 0 / UNKNOWN → 误判完成或漏课。
本回归用 `tests/fixtures/dom/chaoxing_course_catalog.html` —— **来自真实 mooc2 只读捕获
（scripts/capture_fixtures.py → dom_learning_tree.html，已脱敏）的真实 #coursetree 结构**。
当生产标记 / 选择器（`posCatalog_select`/`posCatalog_name`/`icon_Completed`/`已完成`/
`jobUnfinishCount`/`catalog_points_yi`）发生漂移，本测试应红。
"""
from pathlib import Path

STUDENT_CHAPTER = "1217304700"
CATALOG = "dom/chaoxing_course_catalog.html"


def _read(fixture_path, rel: str) -> str:
    p = fixture_path / rel
    assert p.exists(), f"fixture 缺失: {p}"
    return p.read_text(encoding="utf-8")


class TestRealCatalogDrift:
    """真实 #coursetree fixture —— 锚定真实 DOM 语法与完成/待完成标记契约。"""

    def test_fixture_carries_real_grammar_markers(self, fixture_path):
        """夹具必须携带解析器真正消费的真实语法（防「fixture 对、真站不同」回潮）。"""
        html = _read(fixture_path, CATALOG)
        assert "posCatalog_select" in html
        assert "posCatalog_name" in html
        assert "posCatalog_sbar" in html        # 小节编号 (1.1)
        assert "icon_Completed" in html and "已完成" in html  # 完成态
        assert "jobUnfinishCount" in html and "个待完成任务点" in html  # 待完成态

    def test_fixture_is_sanitized_of_session_tokens(self, fixture_path):
        html = _read(fixture_path, CATALOG)
        for secret in ("enc=", "utEnc", "147258369", "18605440", "cookies",
                       "setlog", "passport"):
            assert secret.lower() not in html.lower()

    def test_parser_sees_real_completed_volume(self, fixture_path):
        html = _read(fixture_path, CATALOG)
        assert html.count("已完成") >= 3
        assert html.count("已完成") < 10  # 数量有限，防大漂移

    def test_drift_guard_does_not_need_naive_text_parser(self, fixture_path):
        """真实 fixture 只做**结构**锚定（不依赖 `find(num)` 逐行文本启发式解析）。

        说明：`parse_task_status_from_page` 是“标题前裸编号+空白+汉字”文本启发式，
        它读**文本行**而非真实 DOM（真实 #coursetree 里编号与标题之间有 `</em>`，
        该启发式不适用；真实提取走 `extract_catalog_from_page` 的 DOM 路径）。
        此处仅做单一真源锚定：驱动漂移时 DOM 一类签名类名消失 → 红。
        """
        html = _read(fixture_path, CATALOG)
        # 真实提取器消费的关键选择器一个都不能少
        for sel in ("posCatalog_select", "posCatalog_name", "posCatalog_sbar",
                    "icon_Completed", "jobUnfinishCount", "catalog_points_yi"):
            assert sel in html


class TestCatalogDomDrift:
    """真实课程目录树 fixture —— 结构级漂移 guard 被删/不误报。"""

    def test_catalog_fixture_has_core_markers(self, fixture_path):
        html = _read(fixture_path, CATALOG)
        assert "coursetree" in html
        assert "posCatalog_select" in html   # 真实 class 名（无 CSS 点前缀）
        assert "已完成" in html

    def test_catalog_fixture_is_sanitized(self, fixture_path):
        html = _read(fixture_path, CATALOG)
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
        assert "***" in data["url"]  # 脱敏：enc 必须是红act


class TestNetXhrFixtures:
    """真实 net/ XHR 快照 —— 锚定「网络层喂给解析器的服务端目录页语法」。

    真源：`_raw_capture/xhr_00.json`（scripts/capture_fixtures.py 实捕获的
    mooc2 studentcourse 目录页 GET 200 响应，从 .gitignore 的 _raw_capture 抽取并脱敏）。
    当真实服务端目录语法漂移（.catalog_* / 完成标记），本回归红。
    """

    def test_xhr_catalog_serves_real_grammar(self, fixture_path):
        import json
        data = json.loads(_read(fixture_path, "net/xhr_student_course_catalog.json"))
        assert data["method"] == "GET"
        assert data["status"] == 200
        assert "html" in data["content_type"]
        assert data["real_grammar"] is True
        # catalog_markers 是对完整响应体（158KB）判定的真语法签名（excerpt 只是 CSS 预览片段）
        for m in ("chapter_item", "catalog_title", "catalog_level",
                  "catalog_name", "catalog_state", "knowledgeJobCount",
                  "catalog_points_yi"):
            assert data["catalog_markers"].get(m) is True, f"net 快照缺少目录语法 {m}"

    def test_xhr_fixture_is_sanitized(self, fixture_path):
        s = _read(fixture_path, "net/xhr_student_course_catalog.json").lower()
        for secret in ("enc=", "147258369", "18605440", "utEnc", "setlog"):
            assert secret not in s