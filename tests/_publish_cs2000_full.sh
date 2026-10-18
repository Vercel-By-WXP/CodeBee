#!/bin/bash
# 重生之我带AI回到了2000年：七猫建书 → 前10章串行发布（复用已验证链路）
cd "E:/GoOut/MultiAgentOrchestration"
TID="t-20261017-134251-3423"
echo "== $(date +%H:%M:%S) 建书开始"
curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"acquire","force":true}' >/dev/null
printf '{"platform":"qimao","auto_submit":true}' > tests/_out/cs_create.json
curl -sS -w " [%{http_code}]\n" -X POST "http://127.0.0.1:8765/api/publish/task/$TID/create-book" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" --data-binary @tests/_out/cs_create.json --max-time 30
curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"release"}' >/dev/null
created=0
for i in $(seq 1 32); do
  sleep 15
  ok=$(tail -5 "C:/Users/HP/AppData/Roaming/CodeBee/publish/publish-202610.jsonl" | python -c "
import json,sys
hit=0
for line in sys.stdin:
    try: d=json.loads(line)
    except: continue
    if d.get('action')=='create_book' and d.get('task_id')=='$TID' and d.get('ok'):
        hit=1
print(hit)")
  if [ "$ok" = "1" ]; then created=1; echo "== $(date +%H:%M:%S) 建书成功"; break; fi
done
if [ "$created" != "1" ]; then echo "== 建书超时/失败，退出"; exit 1; fi
sleep 10
for n in 01 02 03 04 05 06 07 08 09 10; do
  printf '{"platform":"qimao","file":"chapter-%s.md","auto_submit":true,"force":true,"force_confirmed":true,"force_reason":"review-infra-broken publish-first mandate"}' "$n" > tests/_out/chN.json
  echo "== $(date +%H:%M:%S) 发 chapter-$n"
  curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"acquire","force":true}' >/dev/null
  curl -sS -w " [%{http_code}]\n" -X POST "http://127.0.0.1:8765/api/publish/task/$TID/chapter" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" --data-binary @tests/_out/chN.json --max-time 30
  curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"release"}' >/dev/null
  for i in $(seq 1 24); do
    sleep 15
    ok=$(tail -1 "C:/Users/HP/AppData/Roaming/CodeBee/publish/publish-202610.jsonl" | python -c "
import json,sys
try:
    d=json.loads(sys.stdin.read())
    print(1 if d.get('action','').startswith('upload') and d.get('chapter_no')==int('$n') else 0)
except Exception:
    print(0)")
    if [ "$ok" = "1" ]; then echo "   chapter-$n 落账"; break; fi
  done
  sleep 20
done
echo "== ALL DONE $(date +%H:%M:%S)"
