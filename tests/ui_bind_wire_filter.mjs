/* codex-chat 排除规则回归（纯函数校验，无需浏览器/服务）：
 * provAdaptedProto 必须与后端 resolve_binding/_entry_endpoint 同源——
 * codex 0.154+ 只讲 responses，chat-only 端点（显式 wire_api=chat 或
 * wire_caps 实测 chat）对 codex 视为无可用协议；对其它 CLI 照常适配。
 * 2026-09-18 重写任务案：链显示健康、运行时必剔死的假象即源于此前端
 * 未建模这条规则。
 * 用法：node tests/ui_bind_wire_filter.mjs */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "app", "ui", "app.js"), "utf8");
const m = src.match(/function provAdaptedProto[\s\S]*?\n}/);
if (!m) { console.error("✗ 未在 app.js 中找到 provAdaptedProto"); process.exit(1); }
const provAdaptedProto = new Function("return (" + m[0].replace(/^function /, "function ") + ")")();

let bad = 0;
const check = (name, cond) => {
  console.log((cond ? "  ✓ " : "  ✗ ") + name);
  if (!cond) bad++;
};

const CHAT_CAPS = { openai: { base: "https://x/v1", wire_api: "chat", checked_at: "2026-09-18" } };
const yunzhishengAuto = { id: "prov-43", name: "云知声", protocol: "auto", base_url: "https://x", wire_caps: CHAT_CAPS };
const OPEN_CAPS = { openai: { base: "https://x/v1", wire_api: "responses" } };
const responsesAuto = { id: "p2", name: "R", protocol: "auto", base_url: "https://x", wire_caps: OPEN_CAPS };

// 1) 云知声形态：auto + 实测 chat → codex 不可用（后端必剔）
check("auto+chat wire × codex → 无可用协议", provAdaptedProto(yunzhishengAuto, ["openai"], "codex-cli") === "");
// 2) 同一供应商 × 其它 CLI → 照常适配 openai
check("auto+chat wire × kimi → 适配 openai", provAdaptedProto(yunzhishengAuto, ["anthropic", "openai"], "kimi-code") === "openai");
// 3) 显式 wire_api=chat 的 openai 供应商 × codex → 也不可用
check("显式 chat × codex → 无可用协议", provAdaptedProto({ id: "p3", protocol: "openai", wire_api: "chat" }, ["openai"], "codex") === "");
// 4) 显式 openai 未标 chat（默认 responses）× codex → 可用
check("显式 openai 默认 responses × codex → 可用", provAdaptedProto({ id: "p4", protocol: "openai" }, ["openai"], "codex-cli") === "openai");
// 5) responses 实测 × codex → 可用（正路不受影响）
check("auto+responses × codex → 适配 openai", provAdaptedProto(responsesAuto, ["openai"], "codex-cli") === "openai");
// 6) 不传 kind（向后兼容旧调用）→ 行为同旧版：chat caps 也放行
check("无 kind 旧调用 → 保持旧行为", provAdaptedProto(yunzhishengAuto, ["openai"]) === "openai");
// 7) chainDeadReasons 口径的 chainProvUsable 同样受 kind 影响（函数存在性 + 排除路径经 provAdaptedProto）
check("chat-only 供应商不带 codex kind → 可用", provAdaptedProto(yunzhishengAuto, ["openai"], "kimi-code") === "openai");

console.log(bad ? `\n${bad} 项失败` : "\n全部通过");
process.exit(bad ? 1 : 0);
