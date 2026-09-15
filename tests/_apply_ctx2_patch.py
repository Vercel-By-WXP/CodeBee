# -*- coding: utf-8 -*-
"""一次性补丁：label 等高对齐 + /api/browse + 工作目录选择按钮 + pickFolder 弹框。
幂等：已应用的锚点跳过。"""
import io

def patch(path, subs):
    src = io.open(path, encoding="utf-8").read()
    changed = False
    for old, new, tag in subs:
        if new in src and old not in src:
            print("SKIP(已应用):", tag)
            continue
        assert src.count(old) == 1, "%s 锚点数量=%d" % (tag, src.count(old))
        src = src.replace(old, new, 1)
        changed = True
        print("OK:", tag)
    if changed:
        io.open(path, "w", encoding="utf-8", newline="").write(src)

# ---------------- 1) style.css：label 等高 + 选目录弹框行样式 ----------------
patch("app/ui/style.css", [
    (
        ".field > label { font-size: 12.5px; font-weight: 600; color: var(--muted); }",
        """/* label 等高（min-height 对齐内嵌 small 按钮的高度）：否则「类型」行嵌了管理
 * 按钮、「继续会话」行是纯文本，grid-2 两列的控件会错位几像素 */
.field > label {
  font-size: 12.5px; font-weight: 600; color: var(--muted);
  display: flex; align-items: center; gap: 6px; min-height: 26px;
}
.field > label button { padding: 3px 10px; font-size: 11.5px; border-radius: 8px; flex: none; }""",
        "css: label 等高",
    ),
    (
        "/* 悬停图标组" if False else ".grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }",
        """.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

/* 文件夹选择弹框（服务端目录浏览，仅本机） */
.pk-tools { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.pk-path {
  flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 12px; color: var(--muted); padding: 0 2px;
}
.pk-list { display: flex; flex-direction: column; gap: 1px; max-height: 46vh; overflow-y: auto; }
.pk-row {
  display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 8px;
  cursor: pointer; transition: background-color .1s;
}
.pk-row:hover { background: var(--panel2); }
.pk-row .ico { width: 15px; height: 15px; color: var(--muted); flex: none; }
.pk-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.pk-empty { padding: 18px 4px; text-align: center; }
""",
        "css: pickFolder 弹框样式",
    ),
])

# ---------------- 2) index.html：两处工作目录输入加「选择」按钮 ----------------
patch("app/ui/index.html", [
    (
        """<div class="field"><label>工作目录</label><input id="f-workdir" placeholder="留空则保存到默认路径；或填绝对路径，例 E:\\GoOut\\my-project">
            <p class="hint" id="f-workdir-hint"></p></div>""",
        """<div class="field"><label>工作目录</label>
            <div class="input-row"><input id="f-workdir" placeholder="留空则保存到默认路径；或填绝对路径，例 E:\\GoOut\\my-project">
              <button class="ghost small" type="button" title="浏览本机目录选择" onclick="pickFolder('f-workdir')">选择…</button></div>
            <p class="hint" id="f-workdir-hint"></p></div>""",
        "html: f-workdir 选择按钮",
    ),
    (
        """<input id="set-workdir" type="text" placeholder="例 E:\\GoOut\\workspace">
              <button class="ghost small" onclick="saveDefaultWorkdir()">保存</button>""",
        """<input id="set-workdir" type="text" placeholder="例 E:\\GoOut\\workspace">
              <button class="ghost small" type="button" title="浏览本机目录选择" onclick="pickFolder('set-workdir')">选择…</button>
              <button class="ghost small" onclick="saveDefaultWorkdir()">保存</button>""",
        "html: set-workdir 选择按钮",
    ),
])

# ---------------- 3) main.py：/api/browse 路由 + _api_browse 方法 ----------------
patch("app/main.py", [
    (
        """        m = re.match(r"^/api/(tasks|runs)/([^/]+)/reveal$", path)
        if m:
            return self._api_reveal(m.group(1), m.group(2))""",
        """        m = re.match(r"^/api/(tasks|runs)/([^/]+)/reveal$", path)
        if m:
            return self._api_reveal(m.group(1), m.group(2))
        if path == "/api/browse":
            return self._api_browse()""",
        "main: browse 路由",
    ),
    (
        "    def _api_control(self):",
        """    def _api_browse(self):
        \"\"\"本机目录浏览（工作目录「选择…」弹框用）。仅限本机请求：目录枚举是信息
        泄露面，手机/局域网端不提供、继续手填。path 缺省=用户主目录；__drives__=盘符
        列表（Windows 从「此电脑」开始选）。只列目录不列文件，纯只读。\"\"\"
        if self._client_id() != "local":
            return self._json(403, {"error": "目录选择仅限本机使用，请手动输入路径"})
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("path") or [""])[0].strip()
        if raw == "__drives__":
            drives = ["%s:\\" % c for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                      if Path("%s:\\" % c).exists()]
            return self._json(200, {"path": "此电脑", "parent": "", "dirs": drives})
        try:
            p = Path(raw).expanduser() if raw else Path.home()
        except Exception:
            return self._json(400, {"error": "非法路径"})
        if not p.exists():
            return self._json(404, {"error": "目录不存在: %s" % p})
        if not p.is_dir():
            return self._json(400, {"error": "不是目录: %s" % p})
        try:
            dirs = sorted((d.name for d in p.iterdir() if d.is_dir()), key=str.lower)
        except PermissionError:
            dirs = []  # 无权限的目录按空目录处理，可继续选它本身
        parent = "" if p.parent == p else str(p.parent)
        return self._json(200, {"path": str(p), "parent": parent, "dirs": dirs})

    def _api_control(self):""",
        "main: _api_browse 方法",
    ),
])

# ---------------- 4) app.js：pickFolder 弹框 ----------------
PICKER_JS = '''/* 文件夹选择弹框：服务端目录浏览（/api/browse 仅本机；浏览器拿不到绝对路径）。
 * 选择结果写回 targetId 输入框。进入=点行，选定=行内「选这个」或底部「使用当前目录」。 */
const pickerSt = { target: "", cwd: "", parent: "" };

function pickerRender(r) {
  pickerSt.cwd = r.path; pickerSt.parent = r.parent || "";
  const esc2 = (s) => esc(s);
  const rows = (r.dirs || []).map((d) =>
    \'<div class="pk-row" data-p="\' + esc2(d) + \'" onclick="pickEnterP(this.dataset.p)">\' +
    \'<svg class="ico" aria-hidden="true"><use href="#i-folder"></use></svg>\' +
    \'<span class="pk-name">\' + esc2(d) + \'</span>\' +
    \'<button class="ghost small" onclick="event.stopPropagation(); pickUseP(this.closest(\\'.pk-row\\').dataset.p)">选这个</button></div>\'
  ).join("");
  const atDrives = r.path === "此电脑";
  $("pk-body").innerHTML =
    \'<div class="pk-tools">\' +
    (atDrives ? "" : \'<button class="ghost small" onclick="pickDrivesP()">此电脑</button>\' +
      (r.parent ? \'<button class="ghost small" onclick="pickParentP()">上级</button>\' : "")) +
    \'<span class="pk-path" title="\' + esc2(r.path) + \'">\' + esc2(r.path) + "</span></div>" +
    \'<div class="pk-list">\' + (rows || \'<div class="pk-empty hint">（没有子目录）</div>\') + "</div>";
}

async function pickerBrowse(p) {
  const body = $("pk-body");
  if (body) body.innerHTML = \'<div class="hint">正在读取目录…</div>\';
  let r;
  try { r = await api("/api/browse?path=" + encodeURIComponent(p)); }
  catch (e) { if (body) body.innerHTML = \'<div class="msg bad">读取失败：\' + esc(e.message) + "</div>"; return; }
  if (r.error) { if (body) body.innerHTML = \'<div class="msg bad">\' + esc(r.error) + "</div>"; return; }
  pickerRender(r);
}

window.pickFolder = function (targetId) {
  pickerSt.target = targetId;
  const cur = (($(targetId) || {}).value || "").trim();
  openModal("选择文件夹", \'<div id="pk-body" class="hint">正在读取目录…</div>\',
    \'<button class="ghost" onclick="closeModal()">取消</button>\' +
    \'<button class="primary" onclick="pickUseP(pickerCwd())">使用当前目录</button>\');
  pickerBrowse(cur || "__drives__");
};

window.pickerCwd = () => pickerSt.cwd;
window.pickEnterP = (p) => pickerBrowse(p);
window.pickParentP = () => pickerBrowse(pickerSt.parent || "__drives__");
window.pickDrivesP = () => pickerBrowse("__drives__");
window.pickUseP = (p) => {
  const el = $(pickerSt.target);
  if (el) { el.value = p; el.dispatchEvent(new Event("change", { bubbles: true })); }
  closeModal();
};

async function revealPath(kind, id, open) {'''

patch("app/ui/app.js", [
    (
        "async function revealPath(kind, id, open) {",
        PICKER_JS,
        "app: pickFolder 弹框",
    ),
    (
        "window.revealPath = revealPath;",
        "window.revealPath = revealPath;\nwindow.pickFolder = pickFolder;",
        "app: pickFolder 导出",
    ),
])

# ---------------- 5) index.html：i-folder 精灵图标 ----------------
patch("app/ui/index.html", [
    (
        '<symbol id="i-grip"',
        '<symbol id="i-folder" viewBox="0 0 24 24"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></symbol>\n  <symbol id="i-grip"',
        "html: i-folder 图标",
    ),
])

print("全部补丁完成")
