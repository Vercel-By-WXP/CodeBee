/* 前端 JS 语法哨兵：对 UI 目录全部 *.js 跑 node --check。
 * 背景：并行改动曾三次把 app.js 改出语法错误（引号未转义/正则断行/i18n t 遮蔽），
 * 语法错误会让整个前端加载失败（空白任务树），且运行期异常被 SSE catch 吞掉，
 * 极难排查——上测试前先跑这个能 1 秒兜底。用法：node tests/check_ui_syntax.mjs */
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const UI = join(dirname(fileURLToPath(import.meta.url)), "..", "app", "ui");
const files = readdirSync(UI).filter((f) => f.endsWith(".js"));
let bad = 0;
for (const f of files) {
  const src = readFileSync(join(UI, f), "utf8");
  const r = spawnSync(process.execPath, ["--check", "--input-type=module", "-"],
    { input: src, encoding: "utf8" });
  // --check 对 module/普通脚本规则不同；按普通脚本再验一次，任一通过即算过
  const r2 = r.status === 0 ? r : spawnSync(process.execPath, ["--check", "-"], { input: src, encoding: "utf8" });
  if (r2.status !== 0) {
    bad++;
    console.error(`✗ ${f}\n${r2.stderr}`);
  } else {
    console.log(`✓ ${f}`);
  }
}
console.log(bad ? `\n${bad} 个文件有语法错误` : "\n全部通过");
process.exit(bad ? 1 : 0);
