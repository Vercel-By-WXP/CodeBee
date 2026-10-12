# -*- coding: utf-8 -*-
"""borrow-log 夜间调研扫描：gh api search/repositories，串行 + sleep 4s 纪律。
JSONL 打到 stdout，进度打到 stderr，不写任何文件。403 退避 20s 一次，再撞标记 error。"""
import json
import subprocess
import sys
import time
import urllib.parse

# Windows 默认控制台可能是 GBK；仓库描述含 emoji 或其他 Unicode 时，
# JSONL 扫描结果必须仍能完整落盘，不能因输出编码中止整轮扫描。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

QUERIES = [
    ("A1", "multi-agent+orchestration"), ("A1", "agent+orchestration"),
    ("A1", "meta-harness+OR+agent+harness"), ("A1", "claude+code+orchestrator+OR+codex+orchestrator"),
    ("A1", "agent+swarm+OR+crew+agents"), ("A1", "coding+agent+supervisor+OR+agent+manager"),
    ("A1", "claude+skills+OR+agent+skills+marketplace"), ("A1", "多智能体+编排"),
    ("A2", "autonomous+agent+framework"), ("A2", "agent+workflow+engine+OR+agent+pipeline"),
    ("A2", "agentic+coding"), ("A2", "agent+memory+OR+agent+evals"),
    ("A2", "context+engineering"), ("A2", "ai+employee+OR+digital+worker+OR+digital+employee"),
    ("A2", "LLM+workflow+builder"),
    ("A3", "prompt+caching+OR+llm+semantic+cache"), ("A3", "token+optimization+OR+token+efficient"),
    ("A3", "context+window+management"), ("A3", "cheap+model+routing+OR+model+cascade"),
    ("A3", "token+efficient+agent+OR+openclacky"),
    ("A4", "ai+coding+agent+cli"), ("A4", "terminal+coding+agent"), ("A4", "headless+agent+cli"),
    ("A5", "agent+team+OR+agent+fleet"), ("A5", "computer+use+OR+computer+control+agent+cli"),
    ("A5", "spec-driven+development+agent"), ("A5", "ai+agent+sandbox+runtime"),
    ("A5", "openharness+OR+domain+specific+harness"),
    ("A6", "ai+agent+evals+OR+agent+benchmark"), ("A6", "agent+observability+OR+agent+tracing"),
    ("A6", "mcp+orchestration+OR+mcp+manager"), ("A6", "agent+handoff+OR+multi-agent+collaboration"),
    ("A6", "model+router+OR+llm+router"), ("A6", "智能体+编排"),
    ("A7", "novel+writing+ai+OR+story+generation+ai"), ("A7", "creative+writing+agent"),
    ("A7", "long+form+writing+ai+OR+book+writing+agent"), ("A7", "网文+AI+写作+OR+小说+生成"),
    ("A7", "article+writing+ai+OR+blog+writing+agent"), ("A7", "speech+writing+ai+OR+presentation+script+generator"),
    ("A7", "translation+agent+OR+ai+translation+workflow"), ("A7", "story+consistency+check+OR+long+document+consistency"),
    ("A8", "prompt+management+platform+OR+prompt+registry"), ("A8", "prompt+versioning+OR+prompt+ab+testing"),
    ("A8", "one-api+alternative+OR+llm+api+gateway"), ("A8", "hallucination+detection+OR+llm+output+validation"),
    ("A8", "structured+output+agent+OR+schema+guard+llm"),
    ("A9", "agent+scheduler+OR+cron+ai+tasks"), ("A9", "self+update+cli+OR+auto+update+mechanism"),
    ("A9", "rate+limit+backoff+llm+OR+429+retry+agent"), ("A9", "onboarding+wizard+cli+OR+first+run+experience"),
    ("A9", "webnovel+author+tools+OR+小说+作者+工具"), ("A9", "ai+cover+image+generator+OR+book+cover+generation"),
    ("A10", "ai+email+writing+OR+business+email+generator"), ("A10", "weekly+report+ai+OR+work+report+generator"),
    ("A10", "short+video+script+ai+OR+tiktok+script+generator"), ("A10", "ai+translation+quality+OR+translation+agent"),
    ("A10", "chatbot+memory+OR+conversational+agent+memory"), ("A10", "ai+code+generation+OR+code+completion+agent"),
    ("A10", "ai+code+refactoring+OR+code+improvement+agent"), ("A10", "ai+documentation+generator+OR+doc+writing+agent"),
    ("A10", "ai+presentation+slides+generator"), ("A10", "ppt-master+OR+powerpoint+native+generator"),
    ("A10", "ai+search+agent+OR+deep+research+agent"),
    ("A11", "persistent+memory+coding+agent+OR+agent+facts+store"), ("A11", "ai+decision+log+OR+architecture+decision+records"),
    ("A11", "findings+file+OR+discovery+log+agent"), ("A11", "spec+archive+OR+specification+versioning"),
    ("A11", "project+constitution+OR+coding+standards+auto"),
    ("A12", "web+novel+publish+automation"), ("A12", "story+to+video+pipeline+OR+novel+adaptation"),
    ("A12", "multi+platform+content+publishing+agent"), ("A12", "reader+feedback+analysis+ai"),
    ("A12", "chapter+hook+optimization+OR+serial+pacing"),
    ("A13", "agent+dashboard+OR+multi+agent+cli+panel"), ("A13", "claude+code+web+ui+OR+codex+web+terminal"),
    ("A13", "llm+usage+metrics+OR+token+throughput+dashboard"), ("A13", "agent+credential+vault+OR+secret+management+agent"),
    ("A13", "agent+out+of+scope+edit+OR+scope+creep+agent+OR+half+finished+agent"),
    ("A13", "proof+gated+completion+OR+agent+self+report+trust"), ("A13", "defect+retrospective+ai+OR+bug+postmortem+agent"),
    ("B1", "code+review+agent+OR+ai+code+reviewer"), ("B1", "pr+review+bot+github"),
    ("B1", "github+action+ai+review"), ("B1", "bug+detection+agent"),
    ("B1", "vulnerability+scanner+agent"), ("B1", "test+generation+agent+OR+ai+testing+agent"),
    ("B1", "regression+test+generation+ai"), ("B1", "refactor+agent+OR+tech+debt+agent"),
    ("B1", "secure+code+review+agent+OR+security+review+bot"), ("B1", "api+test+generation+agent+OR+integration+test+agent"),
    ("B1", "ast+based+code+editing+agent+OR+symbol+level+code+edit"),
    ("B1", "open-code-review+alternative+OR+codeai+review"),
    ("B1", "strix+OR+penetration+testing+agent"),
