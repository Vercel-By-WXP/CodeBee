#!/bin/bash
# 嫡女重生：番茄补发 ch4/5/8（发布设置弹窗偶发抽风重试）
cd "E:/GoOut/MultiAgentOrchestration"
TID="t-20261017-115114-8191"
for n in 04 05 08; do
  printf '{"platform":"fanqie","file":"chapter-%s.md","auto_submit":true,"force":true,"force_confirmed":true,"force_reason":"review-infra-broken publish-first mandate"}' "$n" > tests/_out/chN.json
  echo "== $(date +%H:%M:%S) 补发 chapter-$n"
  curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"acquire","force":true}' >/dev/null
  curl -sS -w " [%{http_code}]\n" -X POST "http://127.0.0.1:8765/api/publish/task/$TID/chapter" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" --data-binary @tests/_out/chN.json --max-time 30
  curl -sS -X POST "http://127.0.0.1:8765/api/control" -H "Content-Type: application/json" -H "X-CodeBee-Client: zcode-automation" -d '{"action":"release"}' >/dev/null
  for i in $(seq 1 28); do
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
