import json, os, time, glob

base = os.path.join(os.environ['APPDATA'], 'CodeBee', 'runs')
dirs = sorted(glob.glob(os.path.join(base, 'r-*')), key=os.path.getmtime, reverse=True)
print('total runs:', len(dirs))
for d in dirs[:14]:
    rj = os.path.join(d, 'run.json')
    if not os.path.exists(rj):
        print(os.path.basename(d), '(no run.json)')
        continue
    try:
        r = json.load(open(rj, encoding='utf-8'))
    except Exception as e:
        print(os.path.basename(d), 'BROKEN', e)
        continue
    st = r.get('status')
    err = r.get('error') or ''
    steps = r.get('steps') or []
    last_step = steps[-1] if steps else {}
    task = r.get('task_id', '')
    print('%s task=%s status=%s' % (os.path.basename(d), task, st))
    if err:
        print('   error:', str(err)[:400].replace('\n', ' | '))
    ls_err = last_step.get('error') or ''
    if ls_err and ls_err != err:
        print('   last-step error:', str(ls_err)[:300].replace('\n', ' | '))
    # model used in steps
    models = set()
    for s in steps:
        m = s.get('model') or (s.get('meta') or {}).get('model')
        if m:
            models.add(m)
    if models:
        print('   models:', ','.join(list(models)[:6]))
