"""Inspect latest CI registry."""
import json

with open('./tmp_artifacts3/home/runner/work/xuexitong/xuexitong/state/registry/265997861_151695658/tasks.json', encoding='utf-8') as f:
    data = json.load(f)
tasks = data if isinstance(data, dict) else data.get('tasks', {})
print(f'total={len(tasks)}')
no_cid = [(tid, r) for tid, r in tasks.items() if not r.get('chapter_id')]
sorted_nc = sorted(no_cid, key=lambda x: (x[1].get('_ch_idx', 999), x[1].get('_cell_idx', 999)))
for tid, r in sorted_nc[:6]:
    print(f'  {tid} ch={r.get("_ch_idx")} cell={r.get("_cell_idx")} status={r.get("status")} title={r.get("title","")[:22]}')
