// 一次性探针：验 verdictDigestText 对真实评审原文 / 老数据截断残文的行为。
// 用法：node tests/_probe_verdict_digest.mjs <run.json 路径> <步骤n>
import fs from "node:fs";

const src = fs.readFileSync(new URL("../app/ui/app.js", import.meta.url), "utf8");
const start = src.indexOf("function verdictDigestText");
if (start < 0) throw new Error("helper not found in app.js");
// 函数体内正则字面量带 {/}，花括号计数不可靠：切到下一个已知锚点
const anchor = src.indexOf('let chatSig = "";', start);
if (anchor < 0) throw new Error("anchor after helper not found");
const verdictDigestText = eval("(" + src.slice(start, anchor).trim().replace(/;$/, "") + ")");

const [, , runPath, nArg] = process.argv;
const run = JSON.parse(fs.readFileSync(runPath, "utf8"));
const step = run.steps.find((s) => s.n === Number(nArg || 4));
const out = String(step.output || step.summary || "");

console.log("--- 单行版（完整原文）---");
console.log(verdictDigestText(out));
console.log("--- 老 summary 截600字（salvage 分支）---");
console.log(verdictDigestText(out.slice(0, 600)));
console.log("--- 多行版前3行 ---");
console.log(verdictDigestText(out, true).split("\n").slice(0, 3).join("\n"));
console.log("--- 普通回答不误伤（应均为空串）---");
console.log(JSON.stringify(verdictDigestText("这是普通回答，提到 scores 关键词但没有 JSON。")));
console.log(JSON.stringify(verdictDigestText("")));
