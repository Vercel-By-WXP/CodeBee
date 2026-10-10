"""OpenAPI 3 contract for the stable headless CodeBee integrations."""


def document():
    schemas = {
        "Error": {"type": "object", "required": ["error"], "properties": {
            "error": {"type": "string"}, "control": {"$ref": "#/components/schemas/Control"}}},
        "Control": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["free", "held"]},
            "mine": {"type": "boolean"}, "holder": {"type": "string"},
            "expires_in": {"type": "integer", "minimum": 0}}},
        "TaskCreate": {"type": "object", "required": ["goal"], "properties": {
            "type": {"type": "string"}, "title": {"type": "string"},
            "goal": {"type": "string", "maxLength": 4000}, "workdir": {"type": "string"},
            "acceptance_criteria": {"type": "array", "maxItems": 40,
                                     "items": {"type": "string", "maxLength": 500}},
            "approval_required": {"type": "boolean"}}},
        "TaskResult": {"type": "object", "required": ["task_id", "run_id"], "properties": {
            "task_id": {"type": "string"}, "run_id": {"type": "string"}}},
        "Evidence": {"type": "object", "required": ["status"], "properties": {
            "id": {"type": "string"}, "criterion": {"type": "string", "maxLength": 500},
            "status": {"type": "string", "enum": ["passed", "failed", "unknown", "not_evaluated"]},
            "summary": {"type": "string", "maxLength": 2000},
            "source": {"type": "string", "maxLength": 80}, "created_at": {"type": "string"}}},
        "Contract": {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"$ref": "#/components/schemas/Evidence"}},
            "approval_required": {"type": "boolean"}, "approval": {"type": "object"},
            "blocked_reason": {"type": "string"}, "completion_receipt": {"type": "object"}}},
        "StoryChapter": {"type": "object", "properties": {
                "op": {"type": "string"},
                "chapter": {"type": "integer", "minimum": 1, "maximum": 5000},
                "outline": {"type": "string", "maxLength": 12000}, "prose": {"type": "string"},
                "facts": {"type": "array", "items": {"type": "string"}},
                "characters": {"type": "array", "items": {"type": "string"}},
                "timeline": {"type": "array", "items": {"type": "string"}},
                "foreshadowing": {"type": "array", "items": {"type": "object"}},
                "next_promises": {"type": "array", "items": {"type": "string"}},
                "author_truth": {"type": "array", "items": {"type": "string"}},
                "reader_known": {"type": "array", "items": {"type": "string"}}},
            "oneOf": [
                {"required": ["op"], "properties": {"op": {"const": "init"}}},
                {"required": ["chapter", "outline", "prose"]},
            ]},
        "StoryTrackingResult": {"type": "object", "properties": {
            "state": {"type": "object"}, "check": {"type": "object"}}},
        "Presence": {"type": "object", "required": ["agent_id"], "properties": {
            "agent_id": {"type": "string", "maxLength": 100}, "label": {"type": "string", "maxLength": 120},
            "capabilities": {"type": "array", "maxItems": 30,
                              "items": {"type": "string", "maxLength": 80}},
            "task_id": {"type": "string", "maxLength": 100}, "status": {"type": "string", "maxLength": 30},
            "metadata": {"type": "object"}}},
        "BackendProfile": {"type": "object", "required": ["label", "command"], "properties": {
            "id": {"type": "string"}, "agent_id": {"type": "string"},
            "label": {"type": "string", "maxLength": 80}, "command": {"type": "string", "maxLength": 500},
            "args": {"type": "array", "maxItems": 80, "items": {"type": "string"}},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "workspace_path": {"type": "string"}, "enabled": {"type": "boolean"}}},
        "CommandAuth": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["legacy", "restricted"]},
            "pending": {"type": "array"}, "rules": {"type": "array"}, "audit": {"type": "array"}}},
    }

    def body(schema):
        return {"required": True, "content": {"application/json": {"schema": schema}}}

    def response(description, schema=None):
        out = {"description": description}
        if schema is not None:
            out["content"] = {"application/json": {"schema": schema}}
        return out

    ref = lambda name: {"$ref": "#/components/schemas/" + name}
    security = [{"codebeeToken": []}, {"codebeeQueryToken": []}]
    errors = {code: response(desc, ref("Error")) for code, desc in (
        ("400", "请求无效"), ("401", "缺少或错误的访问令牌"),
        ("404", "资源不存在"), ("409", "状态冲突或审批未完成"),
        ("423", "其他设备持有控制权"))}
    ok = response("成功", {"type": "object", "properties": {"ok": {"type": "boolean"}}})

    paths = {
        "/api/tasks": {"post": {"summary": "创建并启动任务", "security": security,
            "requestBody": body(ref("TaskCreate")),
            "responses": {"200": response("已排队", ref("TaskResult")),
                          "503": response("运行记录或入队失败", ref("Error")), **errors}}},
        "/api/a2a/tasks": {"post": {"summary": "A2A-lite 创建任务", "security": security,
            "requestBody": body(ref("TaskCreate")),
            "responses": {"200": response("已排队", ref("TaskResult")), "400": errors["400"],
                          "401": errors["401"], "423": errors["423"]}}},
        "/api/a2a/tasks/{task_id}": {"get": {"summary": "A2A-lite 读取任务状态", "security": security,
            "responses": {"200": response("任务、运行和契约", {"type": "object"}),
                          "401": errors["401"], "404": errors["404"]}}},
        "/api/tasks/{task_id}/detail": {"get": {"summary": "读取任务详情和契约", "security": security,
            "responses": {"200": response("任务详情", {"type": "object"}), "401": errors["401"], "404": errors["404"]}}},
        "/api/tasks/{task_id}/contract": {
            "get": {"summary": "读取任务契约", "security": security,
                    "responses": {"200": response("契约", ref("Contract")), "401": errors["401"], "404": errors["404"]}},
            "post": {"summary": "更新契约、追加证据或审批任务", "security": security,
                     "requestBody": body({"type": "object", "properties": {
                         "op": {"type": "string", "enum": ["update", "create", "evidence", "approve", "reject", "blocked"]},
                         "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                         "approval_required": {"type": "boolean"}, "evidence": ref("Evidence"),
                         "note": {"type": "string", "maxLength": 1000}, "reason": {"type": "string", "maxLength": 1000}}}),
                     "responses": {"200": response("契约已更新", {"type": "object", "properties": {"contract": ref("Contract")}}), **errors}}},
        "/api/tasks/{task_id}/story-tracking": {
            "get": {"summary": "读取小说追踪状态", "security": security,
                    "responses": {"200": response("追踪状态", ref("StoryTrackingResult")), "401": errors["401"],
                                  "404": errors["404"], "409": errors["409"]}},
            "post": {"summary": "提交章节结构化状态", "security": security, "requestBody": body(ref("StoryChapter")),
                     "responses": {"200": response("追踪状态已更新", ref("StoryTrackingResult")), **errors}}},
        "/api/agents/presence": {
            "get": {"summary": "Agent 在线状态", "security": security,
                    "responses": {"200": response("在线 Agent 列表", {"type": "object"}), "401": errors["401"]}},
            "post": {"summary": "Agent 心跳", "security": security, "requestBody": body(ref("Presence")),
                     "responses": {"200": response("心跳已记录", {"type": "object"}), "400": errors["400"],
                                  "401": errors["401"], "423": errors["423"]}}},
        "/api/backend-profiles": {
            "get": {"summary": "读取 ACP Backend Profiles（密钥脱敏）", "security": security,
                    "responses": {"200": response("Profiles", {"type": "object"}), "401": errors["401"]}},
            "post": {"summary": "保存 ACP Backend Profile", "security": security,
                     "requestBody": body(ref("BackendProfile")),
                     "responses": {"200": response("Profile", {"type": "object"}), "400": errors["400"], "401": errors["401"]}}},
        "/api/backend-profiles/{profile_id}/delete": {
            "post": {"summary": "删除 ACP Backend Profile", "security": security,
                     "responses": {"200": ok, "400": errors["400"], "401": errors["401"], "404": errors["404"]}}},
        "/api/command-auth": {
            "get": {"summary": "读取命令授权策略与待处理请求", "security": security,
                    "responses": {"200": response("Authorization state", ref("CommandAuth")), "401": errors["401"]}},
            "post": {"summary": "切换命令授权策略", "security": security,
                     "requestBody": body({"type": "object", "properties": {"mode": {"type": "string", "enum": ["legacy", "restricted"]}}}),
                     "responses": {"200": response("Authorization state", ref("CommandAuth")), "400": errors["400"], "401": errors["401"]}}},
        "/api/command-auth/requests/{request_id}": {
            "post": {"summary": "批准或拒绝等待中的命令", "security": security,
                     "requestBody": body({"type": "object", "required": ["approve"], "properties": {
                         "approve": {"type": "boolean"}, "scope": {"type": "string"}, "match": {"type": "string"}, "prefix": {"type": "string"}}}),
                     "responses": {"200": ok, "400": errors["400"], "401": errors["401"], "404": errors["404"]}}},
        "/api/command-auth/rules/{rule_id}/revoke": {
            "post": {"summary": "撤销命令授权规则", "security": security,
                     "responses": {"200": ok, "400": errors["400"], "401": errors["401"], "404": errors["404"]}}},
        "/api/retrieval/search": {"get": {"summary": "本地检索", "security": security,
            "parameters": [{"name": "q", "in": "query", "required": True,
                            "schema": {"type": "string", "maxLength": 500}},
                           {"name": "limit", "in": "query",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 50}}],
            "responses": {"200": response("检索结果", {"type": "object"}), "400": errors["400"], "401": errors["401"]}}},
        "/api/retrieval/index": {"post": {"summary": "为配置工作区/任务目录建立本地检索索引", "security": security,
            "requestBody": body({"type": "object", "properties": {
                "source": {"type": "string"}, "text": {"type": "string"}, "metadata": {"type": "object"},
                "doc_id": {"type": "string"}, "root": {"type": "string"}, "task_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500}}}),
            "responses": {"200": response("索引已更新", {"type": "object"}), "400": errors["400"],
                          "401": errors["401"], "404": errors["404"], "423": errors["423"]}}},
        "/api/analytics": {"get": {"summary": "任务成本质量分析", "security": security,
            "responses": {"200": response("分析摘要", {"type": "object"}), "401": errors["401"]}}},
        "/api/runs/{run_id}": {"get": {"summary": "读取运行状态", "security": security,
            "responses": {"200": response("运行状态", {"type": "object"}), "401": errors["401"], "404": errors["404"]}}},
        "/api/runs/{run_id}/trace": {"get": {"summary": "读取脱敏 trace", "security": security,
            "responses": {"200": response("Trace", {"type": "object"}), "401": errors["401"], "404": errors["404"]}}},
        "/api/runs/{run_id}/otel": {"get": {"summary": "导出 OTLP JSON trace", "security": security,
            "responses": {"200": response("OTLP trace", {"type": "object"}), "401": errors["401"], "404": errors["404"]}}},
        "/api/runs/{run_id}/checkpoints": {"get": {"summary": "读取 checkpoint 回放预览", "security": security,
            "responses": {"200": response("Checkpoint", {"type": "object"}), "401": errors["401"]}}},
        "/api/flows/{flow_id}/graph": {"get": {"summary": "读取流程图投影", "security": security,
            "responses": {"200": response("Flow graph", {"type": "object"}), "401": errors["401"], "404": errors["404"]}}},
        "/api/hooks/run": {"post": {"summary": "Webhook 触发任务（可选 HMAC）", "security": security,
            "requestBody": body(ref("TaskCreate")),
            "responses": {"200": response("已排队", ref("TaskResult")), "401": errors["401"], "403": errors["401"], "400": errors["400"]}}},
        "/api/eval-matrix/normalize": {"post": {"summary": "规范化评测矩阵 manifest", "security": security,
            "requestBody": body({"type": "object"}), "responses": {"200": response("Manifest", {"type": "object"}), "400": errors["400"]}}},
        "/api/eval-matrix/evaluate": {"post": {"summary": "对比评测矩阵与 baseline", "security": security,
            "requestBody": body({"type": "object"}), "responses": {"200": response("Report", {"type": "object"}), "400": errors["400"]}}},
        "/api/knowledge/pipeline": {"get": {"summary": "读取知识管线质量报告", "security": security,
            "parameters": [{"name": "ingest_id", "in": "query", "schema": {"type": "string"}}],
            "responses": {"200": response("Quality report", {"type": "object"})}},
            "post": {"summary": "分块并建立知识索引", "security": security,
            "requestBody": body({"type": "object"}), "responses": {"200": response("Ingest report", {"type": "object"}), "400": errors["400"]}}},
        "/api/policy": {"get": {"summary": "读取规范化沙箱策略", "security": security,
            "parameters": [{"name": "workdir", "in": "query", "schema": {"type": "string"}}],
            "responses": {"200": response("Sandbox policy", {"type": "object"})}}},
        "/api/runs/{run_id}/resume_now": {"post": {"summary": "立即重试运行", "security": security,
            "responses": {"200": ok, "400": errors["400"], "401": errors["401"], "404": errors["404"], "423": errors["423"]}}},
        "/api/control": {
            "get": {"summary": "读取当前控制权", "security": security,
                    "responses": {"200": response("控制权状态", {"type": "object"}), "401": errors["401"]}},
            "post": {"summary": "接管或释放控制权", "security": security,
                     "requestBody": body({"type": "object", "required": ["action"], "properties": {
                         "action": {"type": "string", "enum": ["acquire", "release"]},
                         "force": {"type": "boolean"}}}),
                     "responses": {"200": response("控制权已更新", {"type": "object"}),
                                   "400": errors["400"], "401": errors["401"], "423": errors["423"]}}},
        "/api/control/heartbeat": {
            "get": {"summary": "续期当前控制权", "security": security,
                    "responses": {"200": response("控制权状态", {"type": "object"}), "401": errors["401"]}},
            "post": {"summary": "续期当前控制权", "security": security,
                     "responses": {"200": response("控制权状态", {"type": "object"}), "401": errors["401"]}}},
        "/api/openapi.json": {"get": {"summary": "读取 OpenAPI 文档", "security": security,
            "responses": {"200": response("OpenAPI 文档", {"type": "object"}), "401": errors["401"]}}},
        "/api/health": {"get": {"summary": "读取服务健康状态", "security": security,
            "responses": {"200": response("健康状态", {"type": "object"}), "401": errors["401"]}}},
        "/api/state": {"get": {"summary": "读取任务状态聚合", "security": security,
            "responses": {"200": response("状态聚合", {"type": "object"}), "401": errors["401"]}}},
    }
    return {"openapi": "3.0.3", "info": {"title": "CodeBee API", "version": "1.0",
        "description": "本机/局域网 API。远程请求需 X-CodeBee-Token 或 token 查询参数；本机 loopback 默认免令牌。"},
        "servers": [{"url": "/"}], "components": {"securitySchemes": {
            "codebeeToken": {"type": "apiKey", "in": "header", "name": "X-CodeBee-Token"},
            "codebeeQueryToken": {"type": "apiKey", "in": "query", "name": "token"}},
            "schemas": schemas}, "paths": paths}
