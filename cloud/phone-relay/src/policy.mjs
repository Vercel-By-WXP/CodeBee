export const MAX_BODY = 16 * 1024 * 1024;
export const COOKIE = "__Host-codebee-device";
export const SESSION_SECONDS = 30 * 24 * 60 * 60;
export const ID = /^[a-f0-9]{32}$/;
export const SECRET = /^[a-f0-9]{64}$/;
export const PAIR_CODE = /^[a-f0-9]{32}$/;
export const REQUEST_HEADERS = new Set([
  "accept", "accept-language", "content-type", "range", "if-none-match",
  "if-modified-since", "accept-encoding", "x-codebee-client", "x-codebee-name", "origin",
]);
export const RESPONSE_HEADERS = new Set([
  "content-type", "content-disposition", "content-length", "content-range", "accept-ranges",
  "etag", "last-modified", "location",
]);

export async function digest(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2, "0")).join("");
}

export function randomSecret() {
  return [...crypto.getRandomValues(new Uint8Array(32))]
    .map(x => x.toString(16).padStart(2, "0")).join("");
}

export function sessionCookie(value, age = SESSION_SECONDS) {
  return `${COOKIE}=${value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${age}`;
}

export function readSession(request) {
  const value = (request.headers.get("cookie") || "").split(";")
    .map(x => x.trim()).find(x => x.startsWith(`${COOKIE}=`))?.slice(COOKIE.length + 1);
  const parts = (value || "").split(".");
  return parts.length === 2 && ID.test(parts[0]) && SECRET.test(parts[1]) ? parts : null;
}

export function sameOrigin(request) {
  const origin = request.headers.get("origin");
  return origin === new URL(request.url).origin;
}

// zh-speaking browsers get the Chinese portal; everyone else — including an
// absent header from bots — gets English, the neutral default for i18n.
export function preferredLang(acceptLanguage) {
  let zhQ = -1, enQ = -1;
  for (const part of String(acceptLanguage || "").split(",")) {
    const pieces = part.trim().split(";");
    const tag = (pieces[0] || "").trim().toLowerCase();
    let q = 1;
    for (const p of pieces.slice(1)) {
      const m = p.trim().match(/^q=([0-9.]+)$/);
      if (m) q = parseFloat(m[1]) || 0;
    }
    if (tag.startsWith("zh") && q > zhQ) zhQ = q;
    else if (tag.startsWith("en") && q > enQ) enQ = q;
  }
  return zhQ >= 0 && zhQ >= enQ ? "zh" : "en";
}

export function safePath(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") ||
      /[\r\n\\\x00]/.test(path) || path.length > 8192) return false;
  try {
    const decoded = decodeURIComponent(path.split("?")[0]);
    return !decoded.startsWith("//") && !/[\\\r\n\x00]/.test(decoded) &&
      !decoded.startsWith("/relay/") && !decoded.startsWith("/api/connect");
  } catch { return false; }
}

// Static workbench files under the same paths the desktop serves (its route
// regex on the unslashed path), mirrored into public/ui by sync-ui.mjs. "/"
// maps to the mirrored workbench shell: an unpaired phone never reaches it
// (the portal branch answers first), so the edge only serves it to paired
// sessions — including the desktop-offline case, where a cached shell beats
// the old 503 landing.
const STATIC_NAME = /^[\w.-]+(\/[\w.-]+)*$/;
const STATIC_EXT = /\.(?:js|css|html|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|map|json|txt)$/i;

export function assetTarget(pathname) {
  if (pathname === "/") return "/ui/index.html";
  const name = pathname.slice(1);
  if (!STATIC_NAME.test(name) || !STATIC_EXT.test(name)) return null;
  return "/ui" + pathname;
}

export function encode(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 32768) {
    text += String.fromCharCode(...bytes.subarray(i, i + 32768));
  }
  return btoa(text);
}

export function decode(text) {
  const raw = atob(text);
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

export async function boundedBody(request, limit = MAX_BODY) {
  if (Number(request.headers.get("content-length") || 0) > limit) throw new RangeError();
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) { await reader.cancel(); throw new RangeError(); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

export function securityHeaders() {
  return new Headers({
    "cache-control": "no-store", "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff", "x-frame-options": "SAMEORIGIN",
    "content-security-policy": "frame-ancestors 'self'; object-src 'none'; base-uri 'self'",
  });
}

export function json(data, status = 200, extra = {}) {
  const headers = securityHeaders();
  headers.set("content-type", "application/json; charset=utf-8");
  for (const [key, value] of Object.entries(extra)) headers.set(key, value);
  return new Response(JSON.stringify(data), { status, headers });
}
