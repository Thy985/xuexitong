"""Simulate reconcile_queue from CI artifact."""
import json

with open('./tmp_artifacts3/home/runner/work/xuexitong/xuexitong/state/registry/265997861_151695658/tasks.json', encoding='utf-8') as f:
    data = json.load(f)
tasks = data if isinstance(data, dict) else data.get('tasks', {})

done_ids = {'1217304700', '1217304702', '1217304704', '1217304706'}
ready = []
for tid, r in tasks.items():
    cid = r.get('chapter_id', '')
    status = r.get('status', '')
    if cid in done_ids or status == 'COMPLETED':
        continue
    ready.append((tid, r))

ready.sort(key=lambda x: (0 if x[1].get('chapter_id') else 1, x[1].get('_ch_idx', 0), x[1].get('_cell_idx', 0)))
print(f'READY tasks: {len(ready)}')
for tid, r in ready[:5]:
    ch = r.get('_ch_idx', 0)
    cell = r.get('_cell_idx', 0)
    cid = r.get('chapter_id', '?')
    status = r.get('status', '')
    title = r.get('title', '')[:22]
    print(f'  {tid} ch={ch} cell={cell} cid={cid} status={status} title={title}')
