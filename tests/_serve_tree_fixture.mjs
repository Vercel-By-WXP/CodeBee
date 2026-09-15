/* 临时辅助：ui_tree.mjs 的自含前置——造数 + 起 18798 服务（Windows 路径，避 MSYS 转换）。
 * 用法：node tests/_serve_tree_fixture.mjs  （Ctrl+C 或由调用方杀进程） */
import { spawn, execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const seed = mkdtempSync(join(tmpdir(), "tutti-tree-"));
execSync("python -X utf8 tests/_seed_tree_fixtures.py", {
  env: { ...process.env, TUTTI_DATA: seed }, stdio: "inherit", cwd: ROOT });
const svc = spawn("python", ["-X", "utf8", join(ROOT, "app", "main.py"), "--port", "18768",
  "--no-browser", "--host", "127.0.0.1"],
  { env: { ...process.env, TUTTI_DATA: seed }, stdio: "ignore", cwd: ROOT });
let up = false;
// Node24 undici keep-alive 断言会异步炸进程（assert !this.paused），护栏吞掉保服务常驻
process.on("uncaughtException", () => {});
for (let i = 0; i < 30 && !up; i++) {
  await new Promise((r) => setTimeout(r, 500));
  try { up = (await fetch("http://127.0.0.1:18768/api/state", { headers: { connection: "close" } })).ok; } catch (e) { /* wait */ }
}
console.log("18798 seeded service up:", up, "| seed:", seed);
if (!up) { svc.kill(); process.exit(1); }
// 撑住进程直到被外部杀掉
setInterval(() => {}, 1 << 30);
