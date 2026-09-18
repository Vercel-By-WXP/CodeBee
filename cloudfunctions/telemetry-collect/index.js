# -*- coding: utf-8 -*-
# CloudBase HTTP 云函数 telemetry-collect：CodeBee 匿名错误遥测收集端。
#
# 入参（POST JSON）：
#   {"kind":"ping",   "app":"codebee", "version":"0.1.2", "os":"windows",
#    "python":"3.8",  "ts":"..."}
#   {"kind":"errors", "records":[{id,ts,day,category,reason,detail,provider,
#                                 model,tool,role,run_id,task_id,step,
#                                 exit_code,app_version,os}, ...]}
# 返回：{"ok":true, ...}；任何异常都返回 200/ok:false 形态或 4xx——绝不把堆栈漏给客户端。
#
# 存储：CloudBase 文档数据库 collection `telemetry`（ping 与 errors 同库，
# ping 记 kind="ping"；按 day 分区字段便于按天聚合）。字段全部白名单入库，
# detail 长度截断 800 字符，单次最多 200 条 records（服务端上限，不信任客户端）。
#
# 部署（CloudBase 控制台或 MCP manageFunctions）：
#   函数类型 HTTP，运行时 Nodejs18.15，鉴权关（匿名可写），
#   建议同时在控制台把该函数的公网访问限流调低（防滥用）。
#   部署后把函数访问 URL 填入 app/core/telemetry.py 的 ENDPOINT。
#
# 数据库集合首建：控制台或 CLI 建一次 `telemetry` 集合即可（本函数不做建集合，
# 避免把管理权限交给匿名可写函数）。索引建议：day（聚合用）、ts。

"use strict";

const cloudbase = require("@cloudbase/node-sdk");

// 白名单字段 + 截断上限：客户端传什么都不重要，入库的只有这些
const KEEP = ["id", "ts", "day", "category", "reason", "provider", "model",
  "tool", "role", "run_id", "task_id", "step", "exit_code",
  "app_version", "os"];
const MAX_DETAIL = 800;
const MAX_RECORDS = 200;
const MAX_BODY = 512 * 1024; // 512KB 足够 200 条脱敏记录

let app = null;
function db() {
  if (!app) {
    app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV });
  }
  return app.database();
}

function _str(v, limit) {
  if (v === null || v === undefined) return "";
  let s = String(v);
  return s.length > limit ? s.slice(0, limit) : s;
}

function _sanitize(r) {
  const out = {};
  for (const k of KEEP) {
    if (r[k] === undefined || r[k] === null) continue;
    out[k] = (k === "step" || k === "exit_code")
      ? (typeof r[k] === "number" ? r[k] : null)
      : _str(r[k], k === "detail" ? MAX_DETAIL : 80);
  }
  if (!out.detail && r.detail) out.detail = _str(r.detail, MAX_DETAIL);
  return out;
}

exports.main = async function (event) {
  // HTTP 函数：event 里带 httpMethod/body（CloudBase HTTP 访问服务封装）
  const method = (event.httpMethod || "POST").toUpperCase();
  if (method === "GET") {
    // 健康检查
    return { statusCode: 200, body: JSON.stringify({ ok: true, service: "telemetry-collect" }) };
  }
  let body = event.body || event;
  if (typeof body === "string") {
    if (body.length > MAX_BODY) {
      return { statusCode: 413, body: JSON.stringify({ ok: false, error: "too large" }) };
    }
    try { body = JSON.parse(body || "{}"); } catch (e) {
      return { statusCode: 400, body: JSON.stringify({ ok: false, error: "bad json" }) };
    }
  }
  const kind = _str(body.kind, 16);
  if (kind !== "ping" && kind !== "errors") {
    return { statusCode: 400, body: JSON.stringify({ ok: false, error: "bad kind" }) };
  }
  try {
    const coll = db().collection("telemetry");
    const now = new Date().toISOString().slice(0, 19).replace("T", " ");
    if (kind === "ping") {
      await coll.add({
        kind: "ping", app: _str(body.app, 24), version: _str(body.version, 24),
        os: _str(body.os, 16), python: _str(body.python, 16),
        ts: _str(body.ts, 24) || now, day: (_str(body.ts, 24) || now).slice(0, 10),
      });
      return { statusCode: 200, body: JSON.stringify({ ok: true }) };
    }
    let records = Array.isArray(body.records) ? body.records : [];
    if (records.length > MAX_RECORDS) records = records.slice(0, MAX_RECORDS);
    for (const r of records) {
      const rec = _sanitize(r);
      rec.kind = "error";
      if (!rec.day) rec.day = (rec.ts || now).slice(0, 10);
      if (!rec.ts) rec.ts = now;
      await coll.add(rec);   // 逐条入库；量级（≤200/客户端/6h）不需要批量优化
    }
    return { statusCode: 200, body: JSON.stringify({ ok: true, accepted: records.length }) };
  } catch (e) {
    // 内部错误不给客户端任何细节（集合不存在等），但返回 200 让客户端游标前进？
    // 不：返回 5xx，客户端保持游标原样，下一轮重传——宁可重传不可丢。
    return { statusCode: 500, body: JSON.stringify({ ok: false }) };
  }
};
