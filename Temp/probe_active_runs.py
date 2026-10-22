import json, os, glob

base = os.path.join(os.environ['APPDATA'], 'CodeBee')
runs = sorted(glob.glob(os.path.join(base, 'runs', 'r-*')),
              key=os.path.getmtime, reverse=True)
for d in runs[:8]:
    rj = os.path.join(d, 'run.json')
    if not os.path.exists(rj):
        continue
    r = json.load(open(rj, encoding='utf-8'))
    if r.get('status') not in ('running', 'queued', 'pending'):
        continue
    t = json.load(open(os.path.join(base, 'tasks', r['task_id'] + '.json'),
                       encoding='utf-8'))
    print(r.get('status'), os.path.basename(d), r.get('task_id'),
          'workdir=', t.get('workdir'))
