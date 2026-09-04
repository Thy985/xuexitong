"""Verify _ch_idx/_cell_idx are set in TaskInfo."""
import os, sys
os.environ["CX_USER"] = "18605440838"
os.environ["CX_PASS"] = "147258369Thy"

from tvdp.tdvp import fetch_course_discovery, build_tasks_from_discovery

URL = ("https://mooc1.chaoxing.com/mycourse/studentstudy?"
       "chapterId=1217304706&courseId=265997861&clazzid=151695658"
       "&cpi=506830460&enc=1bc1bd778f9e00d924fe97b3c63f76f4"
       "&mooc2=1&hidetype=0&openc=9b5661be6351e4d46bc29bfa2d69236a")

chapters_raw = fetch_course_discovery(URL)
tasks = build_tasks_from_discovery(chapters_raw)

print(f"DOM: {len(chapters_raw)} nodes, Tasks: {len(tasks)}")
no_cid_tasks = [t for t in tasks if not t.chapter_id]
print(f"no_cid tasks: {len(no_cid_tasks)}")
for t in no_cid_tasks[:5]:
    print(f"  task_id={t.task_id} ch_idx={t._ch_idx} cell_idx={t._cell_idx} title={t.title[:20]}")
