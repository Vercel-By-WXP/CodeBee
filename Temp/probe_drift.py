import json, os, sys, hashlib

base = os.path.join(os.environ['APPDATA'], 'CodeBee', 'tasks')
for tid in ('t-20261022-115109-7965', 't-20261022-135720-4105'):
    p = os.path.join(base, tid + '.json')
    if not os.path.exists(p):
        print(tid, 'missing'); continue
    t = json.load(open(p, encoding='utf-8'))
    wd = t.get('workdir')
    print('==', tid, 'title=', t.get('title'), 'workdir=', wd)
    st_path = os.path.join(wd, '.codebee', 'story_tracking.json')
    if not os.path.exists(st_path):
        print('   no story_tracking.json'); continue
    st = json.load(open(st_path, encoding='utf-8'))
    ch49 = next((c for c in st.get('chapters', []) if c.get('chapter') == 49), None)
    if not ch49:
        print('   chapter 49 not registered'); continue
    prose_path = os.path.join(wd, '.codebee', 'chapters', '0049', 'prose.md')
    disk = open(prose_path, 'rb').read().decode('utf-8', 'replace')
    def digest(text):
        return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:16]
    cands = {digest(disk.strip()), digest(disk.replace('\r\n', '\n').strip())}
    print('   registered digest:', ch49.get('prose_digest'))
    print('   disk digests      :', cands)
    print('   match:', ch49.get('prose_digest') in cands)
    print('   registered words=', ch49.get('word_count'), ' disk words=', len(disk.strip()))
