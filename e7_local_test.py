"""本地测试 TDVP DOM 提取 + click probe。"""
import os, sys
os.environ["CX_USER"] = "18605440838"
os.environ["CX_PASS"] = "147258369Thy"

from tvdp.tdvp import fetch_course_discovery, build_tasks_from_discovery
from e6.task_registry import load_registry, save_registry, reconcile_queue, TaskRecord
from e6.click_probe import click_probe_chapter_id
from state.course_state import load_course_state
from resolvers.course_resolver import _parse_url_params

COURSE_URL = "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706&courseId=265997861&clazzid=151695658&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a"
COURSE_KEY = "265997861_151695658"

# 1. DOM 提取
print("=== Step 1: DOM extraction ===")
chapters_raw = fetch_course_discovery(COURSE_URL)
if not chapters_raw:
    print("FAILED: no chapters found")
    sys.exit(1)
print(f"Found {len(chapters_raw)} chapters")
with_cid = [c for c in chapters_raw if c.get("chapter_id")]
without_cid = [c for c in chapters_raw if not c.get("chapter_id")]
print(f"  with chapter_id: {len(with_cid)}")
print(f"  without chapter_id: {len(without_cid)}")
for c in chapters_raw[:6]:
    print(f"    cid={c.get('chapter_id') or '?'} idx=({c.get('chapter_index')},{c.get('cell_index')}) "
          f"title={c.get('title')[:40]} status={c.get('status')}")

# 2. Build tasks
tasks = build_tasks_from_discovery(chapters_raw)
print(f"\n=== Step 2: Built {len(tasks)} tasks ===")
for t in tasks[:6]:
    print(f"  [{t.status}] cid={t.chapter_id or '?'} title={t.title[:40]}")

# 3. Load history done_ids
state = load_course_state(COURSE_KEY)
done_ids = {h["chapter_id"] for h in (state.history or []) if h.get("chapter_id")}
print(f"\n=== Step 3: done_ids={sorted(done_ids)} ===")

# 4. E6 Registry
existing = load_registry(COURSE_KEY)
for t in tasks:
    tid = t.task_id
    if tid not in existing:
        tr = TaskRecord(
            task_id=tid,
            chapter_id=t.chapter_id or "",
            title=t.title,
            task_type="video",
            status="DISCOVERED",
            priority=len(existing),
            _ch_idx=getattr(t, "_ch_idx", 0),
            _cell_idx=getattr(t, "_cell_idx", 0),
        )
        existing[tid] = tr
    else:
        existing[tid].title = t.title
        if t.status == "COMPLETED":
            if existing[tid].status not in ("COMPLETED", "VERIFYING"):
                existing[tid].status = "COMPLETED"
save_registry(COURSE_KEY, existing)
print(f"\n=== Step 4: Registry has {len(existing)} tasks ===")
for tid, rec in list(existing.items())[:6]:
    print(f"  [{rec.status}] cid={rec.chapter_id or '?'} title={rec.title[:40]}")

# 5. Reconcile
queue = reconcile_queue(COURSE_KEY, existing, done_ids)
print(f"\n=== Step 5: Queue has {len(queue.items)} READY tasks ===")
for item in queue.items[:5]:
    rec = existing.get(item["task_id"])
    print(f"  priority={item['priority']} cid={rec.chapter_id if rec else '?'} title={rec.title[:40] if rec else '?'}")

# 6. Pick next
if queue.items:
    first = queue.items[0]
    rec = existing.get(first["task_id"])
    if rec:
        if rec.chapter_id:
            print(f"\n=== Step 6: next_chapter = {rec.chapter_id} ({rec.title}) ===")
        else:
            print(f"\n=== Step 6: need click-probe for {rec.title} ci={rec._ch_idx} si={rec._cell_idx} ===")
            cid = click_probe_chapter_id(COURSE_URL, rec._ch_idx, rec._cell_idx)
            print(f"  resolved chapterId = {cid or 'NOT_FOUND'}")
else:
    print("\n=== All chapters done ===")
