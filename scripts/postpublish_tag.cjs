// postpublish 自动打标：npm publish 成功后给当前 HEAD 打 v<version> 并推送。
// 推送失败不阻塞（tag 留在本地，下次任意 push 自带）；重复发布同版本时
// tag 已存在则跳过（幂等）。失败必须如实报告——日志不许在 tag 滞留本地时
// 还宣称“已推送”。
const { execFileSync } = require("child_process");
try {
  const ver = JSON.parse(require("fs").readFileSync(require("path").join(__dirname, "..", "package.json"), "utf8")).version;
  const tag = "v" + ver;
  const sh = (cmd, opts) => {
    try { return execFileSync("git", cmd, { stdio: "pipe", ...opts }); }
    catch (e) { return null; }
  };
  const existing = sh(["tag", "-l", tag]);
  if (existing && existing.toString().trim() === tag) {
    console.log("[postpublish] tag %s 已存在，跳过", tag);
  } else {
    const tagged = sh(["tag", "-a", tag, "-m", "codebee " + ver + " (npm)"]);
    console.log(tagged === null
      ? "[postpublish] 打标签 " + tag + " 失败（tag 未创建，不阻塞）"
      : "[postpublish] 已打标签 " + tag);
  }
  const pushed = sh(["push", "origin", tag]);
  console.log(pushed === null
    ? "[postpublish] 推送 " + tag + " 失败（tag 留在本地，下次任意 push 自带）"
    : "[postpublish] 标签已推送");
} catch (e) {
  console.log("[postpublish] 打标失败（不阻塞）：" + (e.message || e));
}
