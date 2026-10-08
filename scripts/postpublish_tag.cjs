// postpublish 自动打标：npm publish 成功后给当前 HEAD 打 v<version> 并推送。
// 推送失败不阻塞（tag 留在本地，下次任意 push 自带）；重复发布同版本时
// tag 已存在则跳过（幂等）。
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
    sh(["tag", "-a", tag, "-m", "codebee " + ver + " (npm)"]);
    console.log("[postpublish] 已打标签 " + tag);
  }
  sh(["push", "origin", tag]);
  console.log("[postpublish] 标签已推送");
} catch (e) {
  console.log("[postpublish] 打标失败（不阻塞）：" + (e.message || e));
}
