import json

with open('state/tdvp_tasks.json', encoding='utf-8') as f:
    d = json.load(f)

print('top-level keys:', list(d.keys()))
for k, v in d.items():
    print(f'\n--- {k} ---')
    if isinstance(v, dict):
        print('  task count:', len(v))
        items = list(v.items())[:10]
        for tid, t in items:
            status = t.get('status')
            title = t.get('title')
            detail = t.get('source_detail')
            print('  %s: status=%s title=%s detail=%s' % (tid, status, title, detail))
    else:
        print('  ', v)