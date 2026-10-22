import shutil, sys, time
from pathlib import Path

sys.path.insert(0, r"E:\GoOut\MultiAgentOrchestration\app")
from core import story_tracking

wd = r"E:\AI小说\神瞳鉴宝：我在当铺断真假"
st = Path(wd) / ".codebee" / "story_tracking.json"
bak = st.with_name("story_tracking.json.bak-1022-reconcile")
shutil.copy2(st, bak)
print("backup ->", bak.name)

before = story_tracking.check(wd)
print("before ok:", before.get("ok"), before.get("errors"))

healed = story_tracking.reconcile_prose(wd)
print("healed chapters:", healed)

after = story_tracking.check(wd)
print("after ok:", after.get("ok"), after.get("errors"))
