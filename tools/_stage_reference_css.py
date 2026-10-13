import difflib
import pathlib
import subprocess

root = pathlib.Path(__file__).resolve().parents[1]
path = root / "app" / "ui" / "desktop-workbench.css"
head = subprocess.check_output(["git", "show", "HEAD:app/ui/desktop-workbench.css"], cwd=root, text=True)
cur = path.read_text(encoding="utf-8")
start = cur.index("/* Reference pass:")
end = cur.index("/* Compact task navigation", start)
block = cur[start:end].rstrip() + "\n\n"
staged = head.rstrip() + "\n\n" + block
patch = "".join(difflib.unified_diff(
    head.splitlines(True), staged.splitlines(True),
    fromfile="a/app/ui/desktop-workbench.css",
    tofile="b/app/ui/desktop-workbench.css",
))
patch_path = root / ".codex-reference.patch"
patch_path.write_text(patch, encoding="utf-8")
subprocess.run(["git", "apply", "--cached", "--ignore-space-change", "--ignore-whitespace", str(patch_path)], cwd=root, check=True)
patch_path.unlink(missing_ok=True)
