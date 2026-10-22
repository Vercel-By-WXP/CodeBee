import json, os, time, sys

base = os.path.join(os.environ['APPDATA'], 'CodeBee')

def load(name):
    p = os.path.join(base, name)
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding='utf-8'))

now = time.time()

print('=== provider_health.json ===')
d = load('provider_health.json')
if d is None:
    print('(missing)')
else:
    provs = d.get('providers', {}) if isinstance(d, dict) else {}
    for k, v in sorted(provs.items()):
        if not isinstance(v, dict):
            continue
        du = v.get('down_until') or 0
        status = 'DOWN' if du and du > now else ('was-down' if du else 'ok')
        left = (du - now) / 60 if du else -1
        print('%-28s %-8s left=%6.1fmin fails=%s silenced=%s' % (
            k, status, left, v.get('consecutive_failures'), v.get('silenced_until', 0) and round((v['silenced_until']-now)/60, 1)))
        for fld in ('last_error', 'last_failure', 'error'):
            if v.get(fld):
                print('    last_%s: %s' % (fld, str(v[fld])[:200]))
        md = v.get('down_models', {}) or v.get('models') or {}
        if isinstance(md, dict):
            for mk, mv in md.items():
                mdu = mv.get('down_until') if isinstance(mv, dict) else mv
                mst = 'MDOWN' if isinstance(mdu, (int, float)) and mdu > now else 'ok'
                mleft = ((mdu - now) / 60) if (isinstance(mdu, (int, float)) and mdu > now) else 0
                print('    model %-40s %-6s left=%.1fmin fails=%s last=%s' % (
                    mk, mst, mleft, (mv.get('consecutive_failures') if isinstance(mv, dict) else ''),
                    str((mv.get('last_error') if isinstance(mv, dict) else '') or '')[:150]))

print()
print('=== account_health.json (top-level keys & down) ===')
a = load('account_health.json')
if a is None:
    print('(missing)')
else:
    if isinstance(a, dict):
        accts = a.get('accounts', a.get('providers', {}))
        if isinstance(accts, dict):
            for k, v in list(accts.items())[:40]:
                if isinstance(v, dict):
                    halted = v.get('halted') or v.get('disabled') or v.get('stopped')
                    if halted:
                        print(k, 'HALTED', str(v.get('reason') or v.get('last_error'))[:180])
        else:
            print(json.dumps(a)[:800])
