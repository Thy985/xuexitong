"""本地测试 click_probe：点击 DOM 节点获取 chapterId。"""
import os, sys
os.environ["CX_USER"] = "18605440838"
os.environ["CX_PASS"] = "147258369Thy"

from e6.click_probe import click_probe_chapter_id
from tvdp.tdvp import fetch_course_discovery

COURSE_URL = "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a"

# 先 DOM 提取，找第一个没有 chapter_id 的节点（应该是"计算机网络的性能"）
print("=== DOM extraction ===")
chapters = fetch_course_discovery(COURSE_URL)
print(f"Found {len(chapters)} chapters")
for i, c in enumerate(chapters):
    cid = c.get("chapter_id", "")
    ci = c.get("chapter_index", 0)
    si = c.get("cell_index", 0)
    print(f"  [{i:2d}] idx=({ci},{si}) cid={cid or 'NONE':>12} title={c.get('title', '')[:30]}")
    if not cid and cid is not None:
        print(f"       ^^^ FIRST without chapter_id — will click_probe this")
        break

print()
print("=== click_probe test ===")
# 找第一个无 chapter_id 的节点
target = None
for c in chapters:
    if not c.get("chapter_id"):
        target = c
        break
if not target:
    print("No target found (all have chapter_id)")
    sys.exit(0)

ci = target.get("chapter_index", 0)
si = target.get("cell_index", 0)
print(f"Clicking node ci={ci} si={si}: {target.get('title')}")
result = click_probe_chapter_id(COURSE_URL, ci, si)
print(f"Result: chapterId = {result or 'NOT_FOUND'}")
if result:
    print("✅ click_probe succeeded!")
else:
    print("❌ click_probe failed")
