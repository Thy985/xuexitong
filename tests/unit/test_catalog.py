"""app.catalog 的目录「章→节→任务」解析契约测试（钉住 /#coursetree 语法）。

用**最小化但严格贴合真实 DOM** 的 HTML（与 dom_learning_tree.html 抽取结构一致）：
  章  .posCatalog_select.firstLayer  id="<数字>"（不带 cur），名称 posCatalog_title，编号 posCatalog_sbar(如 "4")
  节  .posCatalog_select(非 firstLayer)  id="cur<数字>"，名称 posCatalog_name，编号 posCatalog_sbar(如 "4.6")
本节被 reconcile/查询复用：把 chapter_id 归属到具体章 + 节。
"""
from app.catalog import build_chapter_map, load_catalog

MINI_DOM = """<div id="coursetree">
 <ul>
  <li>
   <div class="posCatalog_select firstLayer" id="1217304693">
     <span class="posCatalog_title posCatalog_rotate titleIcon" title="概述"><em class="posCatalog_sbar">1</em>  概述</span>
   </div>
   <div class="posCatalog_level"><ul>
     <li><div class="posCatalog_select" id="cur1217304700">
       <span class="posCatalog_name" title="互联网概述" onclick="getTeacherAjax('265997861','151695658','1217304700');"><em class="posCatalog_sbar">1.1</em>   互联网概述</span>
     </div></li>
     <li><div class="posCatalog_select" id="cur1217304701">
       <span class="posCatalog_name" title="互联网的组成"><em class="posCatalog_sbar">1.2</em>   互联网的组成</span>
     </div></li>
   </ul></div>
  </li>
  <li>
   <div class="posCatalog_select firstLayer" id="1217304696">
     <span class="posCatalog_title" title="网络层"><em class="posCatalog_sbar">4</em>  网络层</span>
   </div>
   <div class="posCatalog_level"><ul>
     <li><div class="posCatalog_select" id="cur1217304734">
       <span class="posCatalog_name" title="IP数据报的分片与重组"><em class="posCatalog_sbar">4.6</em>   IP数据报的分片与重组</span>
     </div></li>
     <li><div class="posCatalog_select" id="cur1217304735">
       <span class="posCatalog_name" title="IP层转发分组的流程"><em class="posCatalog_sbar">4.7</em>   IP层转发分组的流程</span>
     </div></li>
   </ul></div>
  </li>
 </ul>
</div>"""


def test_build_chapter_map_section_to_own_chapter():
    m = build_chapter_map(MINI_DOM)
    # 节归属到其父章
    assert m["1217304734"]["chapter"] == "网络层"
    assert m["1217304734"]["chapter_sbar"] == "4"
    assert m["1217304734"]["section"] == "IP数据报的分片与重组"
    assert m["1217304734"]["section_sbar"] == "4.6"
    assert m["1217304735"]["section"] == "IP层转发分组的流程"
    assert m["1217304735"]["section_sbar"] == "4.7"
    # 前一个章不影响本节的章归属
    assert m["1217304700"]["chapter"] == "概述"
    assert m["1217304701"]["chapter"] == "概述"
    assert m["1217304701"]["section_sbar"] == "1.2"


def test_chapter_node_itself_is_mapped():
    m = build_chapter_map(MINI_DOM)
    # firstLayer 章自身也入库（id 不带 cur）
    assert m["1217304693"]["chapter"] == "概述"
    assert m["1217304693"]["section_sbar"] == "1"
    assert m["1217304696"]["chapter"] == "网络层"


def test_load_catalog_accepts_path_and_string():
    assert load_catalog(MINI_DOM)["1217304734"]["chapter"] == "网络层"
    import tempfile, pathlib
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(MINI_DOM)
        p = pathlib.Path(f.name)
    try:
        assert load_catalog(p)["1217304735"]["section"] == "IP层转发分组的流程"
    finally:
        p.unlink(missing_ok=True)