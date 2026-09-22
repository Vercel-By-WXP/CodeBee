# -*- coding: utf-8 -*-
"""borrow-log 夜间调研扫描：gh api search/repositories，串行 + sleep 4s 纪律。
JSONL 打到 stdout，进度打到 stderr，不写任何文件。403 退避 20s 一次，再撞标记 error。"""
import json
import subprocess
import sys
import time
import urllib.parse

QUERIES = [
    ("A1", "multi-agent+orchestration"), ("A1", "agent+orchestration"),
    ("A1", "meta-harness+OR+agent+harness"), ("A1", "claude+code+orchestrator+OR+codex+orchestrator"),
    ("A1", "agent+swarm+OR+crew+agents"), ("A1", "coding+agent+supervisor+OR+agent+manager"),
    ("A1", "claude+skills+OR+agent+skills+marketplace"), ("A1", "多智能体+编排"),
    ("A2", "autonomous+agent+framework"), ("A2", "agent+workflow+engine+OR+agent+pipeline"),
    ("A2", "agentic+coding"), ("A2", "agent+memory+OR+agent+evals"),
    ("A2", "context+engineering"), ("A2", "ai+employee+OR+digital+worker"),
    ("A2", "LLM+workflow+builder"),
    ("A3", "prompt+caching+OR+llm+semantic+cache"), ("A3", "token+optimization+OR+token+efficient"),
    ("A3", "context+window+management"), ("A3", "cheap+model+routing+OR+model+cascade"),
    ("A4", "ai+coding+agent+cli"), ("A4", "terminal+coding+agent"), ("A4", "headless+agent+cli"),
    ("A5", "agent+team+OR+agent+fleet"), ("A5", "computer+use+OR+computer+control+agent+cli"),
    ("A5", "spec-driven+development+agent"), ("A5", "ai+agent+sandbox+runtime"),
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
    ("A10", "ai+presentation+slides+generator"), ("A10", "ai+search+agent+OR+deep+research+agent"),
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
]

# 轮换池 B（keywords.md 同源）：小时 %7 选批，余 0=批7。--batch N 可只跑指定批补课。
BATCHES = {
    1: [("B1", q) for q in ["code+review+agent+OR+ai+code+reviewer", "pr+review+bot+github",
        "github+action+ai+review", "bug+detection+agent", "vulnerability+scanner+agent",
        "test+generation+agent+OR+ai+testing+agent", "regression+test+generation+ai",
        "refactor+agent+OR+tech+debt+agent", "secure+code+review+agent+OR+security+review+bot",
        "api+test+generation+agent+OR+integration+test+agent",
        "ast+based+code+editing+agent+OR+symbol+level+code+edit"]],
    2: [("B2", q) for q in ["self+improving+agent+OR+agent+reflexion", "agent+episodic+memory",
        "project+memory+coding+agent", "knowledge+graph+agent", "agent+learning+from+feedback",
        "experience+reuse+agent", "agent+self+correction", "skill+library+agent",
        "conversation+memory+compression+OR+memory+summarization+agent",
        "agent+skill+learning+OR+automatic+skill+discovery"]],
    3: [("B3", q) for q in ["long+running+agent+OR+persistent+planning+agent", "spec+driven+development",
        "plan+and+execute+agent", "task+decomposition+agent", "milestone+tracking+agent",
        "project+planning+ai+agent", "autonomous+long+horizon+agent", "worktree+parallel+agent",
        "agent+checkpoint+resume+OR+workflow+recovery+agent",
        "acceptance+criteria+agent+OR+requirements+validation+agent",
        "requirement+elicitation+agent+OR+spec+interview+ai"]],
    4: [("B4", q) for q in ["human+in+the+loop+ai+agent", "agent+approval+workflow", "agent+governance",
        "agent+guardrails", "agent+permission+policy", "agent+audit+trail", "agent+kill+switch",
        "agent+risk+control", "prompt+injection+defense+agent+OR+indirect+prompt+injection",
        "agent+policy+evaluation+OR+guardrail+benchmark"]],
    5: [("B5", q) for q in ["agent+rag", "deep+research+agent", "browser+use+agent+OR+browser+automation+ai",
        "web+scraping+agent", "search+agent+OR+retrieval+agent", "document+understanding+agent",
        "data+extraction+agent", "competitive+intelligence+agent",
        "citation+verification+agent+OR+source+grounding+agent",
        "knowledge+base+quality+OR+rag+evaluation+agent"]],
    6: [("B6", q) for q in ["langgraph+platform+OR+langgraph+deploy", "crewai+studio+OR+crewai+platform",
        "autogen+platform+OR+autogen+studio", "openai+agents+sdk", "google+adk+agent", "mastra+agent",
        "pydanticai+agent", "semantic+kernel+agent",
        "agent+interoperability+protocol+OR+agent+to+agent+protocol",
        "agent+framework+benchmark+OR+multi-agent+framework+comparison"]],
    7: [("B7", q) for q in ["数字员工+OR+大模型+编排", "one-api+alternative", "模型中转+OR+api+网关+大模型",
        "ollama+orchestrator", "local+llm+agent", "self+hosted+agent+platform", "rpa+ai+agent",
        "小说生成+ai+OR+ai+写作+平台", "office+document+agent+OR+办公+智能体+工作流"]],
}


def main():
    import datetime as _dtmod
    argv = sys.argv[1:]
    only = int(argv[argv.index("--batch") + 1]) if "--batch" in argv else None
    if only:
        queries = BATCHES[only]
        print("PROGRESS quick batch B%d: %d queries" % (only, len(queries)), file=sys.stderr)
    else:
        hour = _dtmod.datetime.now().hour
        b = (hour % 7) or 7
        queries = QUERIES + BATCHES[b]
        print("PROGRESS full scan: A=%d + B%d=%d" % (len(QUERIES), b, len(BATCHES[b])),
              file=sys.stderr)
    total = len(queries)
    for i, (grp, q) in enumerate(queries, 1):
        cmdline = ["gh", "api", "-X", "GET",
                   "search/repositories?q=%s&sort=stars&per_page=5"
                   % urllib.parse.quote(q, safe="+"),
                   "--jq", '.items[] | {f:.full_name,s:.stargazers_count,p:.pushed_at,d:(.description // "")[0:160]}']
        try:
            r = subprocess.run(cmdline, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        except Exception as e:
            print("PROGRESS Q%d/%d %s %s EXC %r" % (i, total, grp, q, e), file=sys.stderr)
            time.sleep(4)
            continue
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            if "403" in err:
                print("PROGRESS Q%d/%d %s %s 403 -> backoff 20s" % (i, total, grp, q), file=sys.stderr)
                time.sleep(20)
                r = subprocess.run(cmdline, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60)
            if r.returncode != 0:
                print("PROGRESS Q%d/%d %s %s FAIL %s" % (i, total, grp, q, err[:150]), file=sys.stderr)
                print(json.dumps({"group": grp, "q": q, "error": True}, ensure_ascii=False))
                time.sleep(4)
                continue
        n = 0
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except Exception:
                continue
            print(json.dumps({"group": grp, "q": q, "full": it.get("f"),
                              "stars": it.get("s"), "pushed": (it.get("p") or "")[:10],
                              "desc": it.get("d")}, ensure_ascii=False))
            n += 1
        print("PROGRESS Q%d/%d %s %s ok %d" % (i, total, grp, q, n), file=sys.stderr)
        time.sleep(4)
    print("PROGRESS DONE %d queries" % total, file=sys.stderr)


if __name__ == "__main__":
    main()
