import { DurableObject } from "cloudflare:workers";
import {
  ID, SECRET, PAIR_CODE, SESSION_SECONDS, REQUEST_HEADERS, RESPONSE_HEADERS,
  digest, randomSecret, sessionCookie, readSession, sameOrigin, safePath, assetTarget,
  encode, decode, boundedBody, securityHeaders, json, preferredLang, rateLimitSubject,
} from "./policy.mjs";

async function allowed(env, request, action, limit, seconds, session = null) {
  const key = await digest(rateLimitSubject(request, action, session));
  const response = await env.GATES.getByName(key).fetch("https://gate/check", {
    method: "POST", body: JSON.stringify({ action, limit, seconds }),
  });
  return response.status === 200;
}

async function portal(env, request) {
  const url = new URL(request.url);
  url.pathname = preferredLang(request.headers.get("accept-language")) === "en" ? "/index.en.html" : "/index.html";
  url.search = "";
  const asset = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  const headers = securityHeaders();
  headers.set("content-type", "text/html; charset=utf-8");
  headers.set("content-security-policy", "default-src 'self'; script-src 'self'; style-src 'self'; " +
    "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'");
  return new Response(asset.body, { status: asset.status, headers });
}

async function publicPage(env, request) {
  if (!['GET', 'HEAD'].includes(request.method)) return null;
  const url = new URL(request.url);
  const pathname = url.pathname.replace(/\/+$/, '') || '/';
  const pages = {
    '/docs': '/docs/index.html',
    '/compare': '/compare/index.html',
    '/feedback': '/feedback/index.html',
    '/robots.txt': '/robots.txt',
    '/sitemap.xml': '/sitemap.xml',
  };
  const target = pages[pathname];
  if (!target) return null;
  const assetURL = new URL(url);
  assetURL.pathname = target;
  assetURL.search = '';
  const asset = await env.ASSETS.fetch(new Request(assetURL, { method: request.method }));
  const headers = securityHeaders();
  headers.set('content-type', asset.headers.get('content-type') ||
    (target.endsWith('.xml') ? 'application/xml; charset=utf-8' : 'text/plain; charset=utf-8'));
  headers.set('cache-control', 'public, max-age=300');
  return new Response(request.method === 'HEAD' ? null : asset.body, { status: asset.status, headers });
}

// Static workbench assets live at the same paths the desktop serves; see
// assetTarget in policy.mjs for the shape rules and sync-ui.mjs for the copy.
async function workbenchAsset(env, request, url) {
  if (request.method !== "GET" && request.method !== "HEAD") return null;
  const target = assetTarget(url.pathname);
  if (!target) return null;
  const assetURL = new URL(url);
  assetURL.pathname = target;
  assetURL.search = "";
  const etag = request.headers.get("if-none-match");
  const asset = await env.ASSETS.fetch(new Request(assetURL,
    { headers: etag ? { "if-none-match": etag } : {} }));
  if (asset.status !== 200 && asset.status !== 304) return null;
  const headers = securityHeaders();
  headers.set("content-type", asset.headers.get("content-type") || "application/octet-stream");
  // Short TTL: an unversioned URL must pick up a redeployed bundle quickly.
  headers.set("cache-control", "public, max-age=300");
  if (asset.headers.get("etag")) headers.set("etag", asset.headers.get("etag"));
  headers.set("x-codebee-edge", "assets");
  const body = request.method === "HEAD" || asset.status === 304 ? null : asset.body;
  return new Response(body, { status: asset.status, headers });
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const path = url.pathname;
      const page = await publicPage(env, request);
      if (page) return page;
      if (path === "/relay/health" && request.method === "GET") return json({ ok: true, service: "codebee-phone-relay" });
      if (path === "/relay/portal" && request.method === "GET") return portal(env, request);
      if (["/relay/portal.js", "/relay/portal.css", "/relay/codebee-logo.png"].includes(path) && request.method === "GET") {
        return env.ASSETS.fetch(request);
      }
      const connector = path.match(/^\/relay\/connector\/([a-f0-9]{32})$/);
      if (connector && request.method === "GET") {
        if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") return json({ error: "WebSocket required" }, 426);
        const secret = (request.headers.get("authorization") || "").replace(/^Bearer /, "");
        if (!SECRET.test(secret) || (await digest(secret)).slice(0, 32) !== connector[1]) return json({ error: "Unauthorized" }, 401);
        if (!await allowed(env, request, "connector", 120, 60)) return json({ error: "Too many connections" }, 429);
        return env.DEVICES.getByName(connector[1]).fetch(request);
      }
      if (path === "/relay/pair" && request.method === "POST") {
        if (!sameOrigin(request)) return json({ error: "来源验证失败", code: "bad_origin" }, 403);
        if (!await allowed(env, request, "pair", 10, 60)) return json({ error: "尝试过于频繁，请稍后再试", code: "rate_limited" }, 429);
        let body;
        try { body = JSON.parse(new TextDecoder().decode(await boundedBody(request, 2048))); }
        catch { return json({ error: "连接码格式不正确", code: "invalid_code" }, 400); }
        if (!ID.test(body?.device) || !PAIR_CODE.test(body?.code)) return json({ error: "连接码格式不正确", code: "invalid_code" }, 400);
        return env.DEVICES.getByName(body.device).fetch(new Request(request.url, {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
        }));
      }

      // PWA metadata and other static workbench files are safe to serve before
      // session validation. Browsers request the manifest during first paint;
      // returning 401 here makes an otherwise successful phone login look
      // broken and pollutes the console.
      if (path !== "/") {
        const publicAsset = await workbenchAsset(env, request, url);
        if (publicAsset) return publicAsset;
      }
      const session = readSession(request);
      if (!session) {
        if (path === "/" && request.method === "GET") return portal(env, request);
        return json({ error: "请先在电脑上打开手机连接并扫码配对", pairing: "/relay/portal" }, 401);
      }
      if (!["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].includes(request.method)) return json({ error: "Method not allowed" }, 405);
      if (!["GET", "HEAD"].includes(request.method) && !sameOrigin(request)) return json({ error: "来源验证失败" }, 403);
      // Edge-served workbench assets: no relay hop, no relay rate budget.
      const asset = await workbenchAsset(env, request, url);
      if (asset) return asset;
      if (path.startsWith("/relay/") && !["/relay/logout", "/relay/session"].includes(path)) return json({ error: "Not found" }, 404);
      if (!path.startsWith("/relay/") && !safePath(path + url.search)) return json({ error: "此操作请在电脑上完成" }, 403);
      if (!await allowed(env, request, "request", 600, 60, session)) return json({ error: "请求过于频繁" }, 429);
      const response = await env.DEVICES.getByName(session[0]).fetch(request);
      if (path === "/" && request.method === "GET" && [401, 503].includes(response.status)) {
        const landing = await portal(env, request);
        const headers = new Headers(landing.headers);
        if (response.headers.has("set-cookie")) headers.set("set-cookie", response.headers.get("set-cookie"));
        return new Response(landing.body, { status: 200, headers });
      }
      // Durable Object responses cross an internal fetch boundary. Preserve
      // the connector's compression marker there, then expose the standard
      // response header only at the public Worker boundary.
      const encoding = response.headers.get("x-codebee-content-encoding");
      if (encoding) {
        const headers = new Headers(response.headers);
        headers.delete("x-codebee-content-encoding");
        headers.set("content-encoding", encoding);
        // Re-stream the body so the public response owns the encoding header;
        // Wrangler otherwise treats a streamed DO response as an internal
        // fetch and removes the standard content-encoding metadata.
        const stream = new TransformStream();
        response.body?.pipeTo(stream.writable).catch(() => {});
        return new Response(stream.readable, { status: response.status, statusText: response.statusText, headers });
      }
      return response;
    } catch (error) {
      const tooLarge = error instanceof RangeError;
      return json({ error: tooLarge ? "请求超过 16 MiB 限制" : "连接服务暂时不可用",
        code: tooLarge ? "too_large" : "service_unavailable" }, tooLarge ? 413 : 503);
    }
  },
};

export class RequestGate extends DurableObject {
  async fetch(request) {
    const { action, limit, seconds } = await request.json();
    const now = Date.now();
    const key = `rate:${action}`;
    const record = await this.ctx.storage.get(key) || { count: 0, until: now + seconds * 1000 };
    if (now >= record.until) { record.count = 0; record.until = now + seconds * 1000; }
    record.count++;
    await this.ctx.storage.put(key, record);
    return new Response(null, { status: record.count <= limit ? 200 : 429 });
  }
}

export class DeviceRelay extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.pending = new Map();
    this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  socket() { return this.ctx.getWebSockets("connector").find(ws => ws.readyState === 1); }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/relay/connector/")) {
      // The Worker checks the secret-derived ID. Check again inside the object.
      const device = url.pathname.split("/").pop();
      const secret = (request.headers.get("authorization") || "").replace(/^Bearer /, "");
      if (!SECRET.test(secret) || (await digest(secret)).slice(0, 32) !== device) return json({ error: "Unauthorized" }, 401);
      if (this.socket()) return json({ error: "Device already connected" }, 409);
      const pair = new WebSocketPair();
      this.ctx.acceptWebSocket(pair[1], ["connector"]);
      pair[1].serializeAttachment({ device });
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    if (url.pathname === "/relay/pair") {
      const { device, code } = await request.json();
      const token = randomSecret();
      const hash = await digest(token);
      const codeHash = await digest(code);
      const result = await this.ctx.storage.transaction(async storage => {
        const pairing = await storage.get("pairing");
        if (!this.socket() || !pairing || pairing.expires <= Date.now() || pairing.hash !== codeHash) return 401;
        const sessions = (await storage.get("sessions") || []).filter(x => x.expires > Date.now());
        if (sessions.length >= 16) return 409;
        await storage.delete("pairing");
        sessions.push({ hash, expires: Date.now() + SESSION_SECONDS * 1000 });
        await storage.put("sessions", sessions);
        return 200;
      });
      if (result === 401) return json({ error: "连接码已失效或电脑离线，请在电脑上重新生成", code: "pair_unavailable" }, 401);
      if (result === 409) return json({ error: "已配对设备达到上限，请在电脑上撤销旧连接", code: "device_limit" }, 409);
      this.socket()?.send(JSON.stringify({ type: "paired" }));
      return json({ ok: true }, 200, { "set-cookie": sessionCookie(`${device}.${token}`) });
    }
    const session = readSession(request);
    if (!session) return json({ error: "请重新配对" }, 401);
    const hash = await digest(session[1]);
    const sessions = (await this.ctx.storage.get("sessions") || []).filter(x => x.expires > Date.now());
    if (!sessions.some(x => x.hash === hash)) return json({ error: "连接已撤销或过期，请重新配对" }, 401, { "set-cookie": sessionCookie("", 0) });
    if (url.pathname === "/relay/session" && request.method === "GET") return json({ paired: true, connected: !!this.socket() });
    if (url.pathname === "/relay/logout" && request.method === "POST") {
      await this.ctx.storage.transaction(async storage => {
        const current = await storage.get("sessions") || [];
        await storage.put("sessions", current.filter(x => x.hash !== hash && x.expires > Date.now()));
      });
      for (const [id, item] of this.pending) if (item.hash === hash) this.finish(id, "logged out");
      return json({ ok: true }, 200, { "set-cookie": sessionCookie("", 0) });
    }
    const socket = this.socket();
    if (!socket) return json({ error: "你的电脑当前离线，请启动 Codebee 并保持联网" }, 503);
    if (this.pending.size >= 24) return json({ error: "电脑连接繁忙，请稍后再试" }, 429);
    const headers = {};
    for (const [key, value] of request.headers) if (REQUEST_HEADERS.has(key)) headers[key] = value;
    headers.host = url.host;
    const body = await boundedBody(request);
    if (this.pending.size >= 24 || socket.readyState !== 1) return json({ error: "电脑连接繁忙或已断开" }, 503);
    // Body reads yield. A concurrent revoke must not allow a buffered write
    // to reach the computer after its session was deleted.
    const current = await this.ctx.storage.get("sessions") || [];
    if (!current.some(x => x.hash === hash && x.expires > Date.now())) return json({ error: "连接已撤销" }, 401);
    // In-memory after the first wake; one storage read when hibernated.
    const caps = this.caps || (this.caps = await this.ctx.storage.get("caps")) || null;
    // Wrangler's local Durable Object bridge strips content-encoding from
    // streamed responses; Cloudflare marks real edge requests with this
    // header, so local browser tests stay identity-encoded.
    const acceptsGzip = request.headers.has("cf-ray") &&
      /(?:^|,)\s*gzip\s*(?:;|,|$)/i.test(headers["accept-encoding"] || "");
    const id = crypto.randomUUID();
    return new Promise(resolve => {
      const stream = new TransformStream();
      const item = { resolve, writer: stream.writable.getWriter(), body: stream.readable, socket,
        started: false, method: request.method, origin: url.origin, hash };
      this.pending.set(id, item);
      this.touch(id);
      request.signal.addEventListener("abort", () => this.finish(id, "cancelled"), { once: true });
      try { socket.send(JSON.stringify({ type: "request", id, method: request.method,
        path: url.pathname + url.search, headers, body: encode(body),
        // 2026-10-24/25 双重 gzip 事故：connector 层压缩的编码标记在
        // DO→Worker 内部 fetch 边界丢失，Cloudflare 边缘再压一层，手机解
        // 一层剩一层二进制、state JSON 必炸。云端彻底不向 connector 请求
        // 压缩（旧 connector 的 caps 声明一并无视）：线上走 identity，由
        // CF 边缘做唯一一层透明压缩。
        gzip: undefined })); }
      catch { this.finish(id, "disconnected"); }
    });
  }

  touch(id) {
    const item = this.pending.get(id);
    if (!item) return;
    clearTimeout(item.timer);
    item.timer = setTimeout(() => this.finish(id, "timeout"), 120000);
  }

  finish(id, reason = "") {
    const item = this.pending.get(id);
    if (!item) return;
    this.pending.delete(id);
    clearTimeout(item.timer);
    if (!item.started) item.resolve(json({ error: "电脑连接已中断，请稍后重试" }, 503));
    if (reason) {
      // The cancellation reason is sent over the control socket. Aborting
      // with an Error leaks an unhandled stream rejection in Wrangler and
      // turns normal disconnects into noisy runtime exceptions.
      item.writer.abort().catch(() => {});
      try { item.socket.send(JSON.stringify({ type: "cancel", id })); } catch { /* already closed */ }
    } else if (item.empty) item.writer.abort().catch(() => {});
    else item.writer.close().catch(() => {});
  }

  async webSocketMessage(ws, raw) {
    if (raw === "ping") { ws.send("pong"); return; }
    let message;
    try { message = JSON.parse(raw); } catch { ws.close(1008, "Invalid message"); return; }
    if (message.type === "pair") {
      if (!PAIR_CODE.test(message.code) || !Number.isFinite(message.expires) ||
          message.expires > Date.now() + 301000 || message.expires <= Date.now()) return;
      await this.ctx.storage.put("pairing", { hash: await digest(message.code), expires: message.expires });
      ws.send(JSON.stringify({ type: "pair-ready", code: message.code }));
      return;
    }
    if (message.type === "revoke") {
      await this.ctx.storage.delete(["sessions", "pairing"]);
      for (const id of [...this.pending.keys()]) this.finish(id, "revoked");
      ws.send(JSON.stringify({ type: "revoked" }));
      return;
    }
    if (message.type === "hello") {
      // Connector transport capabilities, persisted so a hibernated object
      // still offers gzip after waking for a phone request. Ignored entirely
      // by older connectors, which simply never compress.
      const caps = { gzip: message.gzip === true };
      this.caps = caps;
      await this.ctx.storage.put("caps", caps);
      return;
    }
    const item = this.pending.get(message.id);
    if (!item || item.socket !== ws) return;
    this.touch(message.id);
    if (message.type === "headers" && !item.started) {
      const headers = securityHeaders();
      for (const [key, value] of Object.entries(message.headers || {})) {
        if (RESPONSE_HEADERS.has(key.toLowerCase())) headers.set(key, value);
      }
      // Trust the connector's announcement, not the request flag: it may
      // have declined (e.g. a byte-range body). Old connectors never send
      // enc and old workers never honored it, so both directions stay plain.
      if (message.enc === "gzip") {
        headers.set("x-codebee-content-encoding", "gzip");
        headers.delete("content-length");
      }
      if (!Number.isInteger(message.status) || message.status < 200 || message.status > 599) return this.finish(message.id, "invalid response");
      const empty = item.method === "HEAD" || [204, 205, 304].includes(message.status);
      item.empty = empty;
      item.started = true;
      item.resolve(new Response(empty ? null : item.body, { status: message.status, headers }));
    } else if (message.type === "chunk" && item.started) {
      try {
        // The connector uses 128 KiB source chunks (about 171 KiB base64).
        // Keep the cap bounded while avoiding a WebSocket round trip for every
        // 48 KiB of the desktop bundle.
        if (typeof message.data !== "string" || message.data.length > 192 * 1024) throw new Error("Chunk limit");
        await item.writer.write(decode(message.data));
        ws.send(JSON.stringify({ type: "ack", id: message.id }));
      } catch { this.finish(message.id, "cancelled"); }
    } else if (message.type === "end") this.finish(message.id);
    else if (message.type === "error") this.finish(message.id, "upstream error");
  }

  webSocketClose(ws) {
    for (const [id, item] of this.pending) if (item.socket === ws) this.finish(id, "disconnected");
  }

  webSocketError(ws) { this.webSocketClose(ws); }
}
