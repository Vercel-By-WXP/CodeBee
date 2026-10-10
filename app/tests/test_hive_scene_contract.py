"""蜂巢 3D 场景（WebGL 蜂巢塔）文本合同：真 3D 引擎在 ui/hive3d.js，
app.js 只做模式切换/持久化/降级，2D 列表是同数据的平面形态。
端到端行为见 tests/ui_hive3d.mjs（无头 Edge + CDP）。"""
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
ENGINE = (ROOT / "ui" / "hive3d.js").read_text(encoding="utf-8")
I18N = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")


class HiveSceneContractTests(unittest.TestCase):
    def test_hive_scene_has_gl_host_and_accessible_controls(self):
        self.assertIn('id="rd-hive-viewport"', INDEX)
        self.assertIn('id="rd-hive-gl"', INDEX)
        self.assertIn('id="rd-hive-overlay"', INDEX)
        self.assertIn('<script src="hive3d.js"></script>', INDEX)
        self.assertIn('data-hive-view="reference"', INDEX)
        self.assertIn('data-hive-view="live3d"', INDEX)
        self.assertIn('data-hive-view="2d"', INDEX)
        self.assertIn('class="hive-viewport hive-mode-live3d"', INDEX)
        self.assertIn('hive-view-btn active" data-hive-view="live3d" aria-pressed="true"', INDEX)
        self.assertIn('hive-mode-live3d', APP)
        self.assertIn('setDisplayMode(hiveMode)', APP)
        self.assertIn('data-hive-scene-action="reset"', INDEX)
        self.assertIn('data-hive-scene-action="zoom-in"', INDEX)
        self.assertIn('data-hive-scene-action="zoom-out"', INDEX)

    def test_engine_is_procedural_webgl_renderer_with_dynamic_pages_and_picking(self):
        # The reference-image renderer was replaced by a real, dependency-free WebGL scene.
        self.assertIn("function Scene(opts)", ENGINE)
        self.assertIn("Scene.prototype.buildWorld", ENGINE)
        self.assertIn("Scene.prototype.buildBatches", ENGINE)
        self.assertIn("aNormal", ENGINE)
        self.assertIn("aColor", ENGINE)
        self.assertIn("gl.drawArrays(gl.TRIANGLES", ENGINE)
        self.assertIn("showPage", ENGINE)
        self.assertIn("ResizeObserver", ENGINE)
        self.assertIn('"pointerdown"', ENGINE)
        self.assertIn('"wheel"', ENGINE)
        self.assertIn("resetView", ENGINE)
        self.assertIn("projectWorld", ENGINE)
        self.assertIn("layoutOverlayPositions", ENGINE)
        self.assertIn("this.deskPositions=[]", ENGINE)
        # The rear row spans almost the full feature wall; the wider front stations
        # are centered with tighter spacing so both rows match the reference proportions.
        self.assertIn("xs:[-8.4,-6.0,-3.6,-1.2,1.2,3.6,6.0,8.4]", ENGINE)
        self.assertIn("xs:[-6.0,-3.6,-1.2,1.2,3.6,6.0]", ENGINE)
        self.assertIn("screenWidth:1.28,screenHeight:.70", ENGINE)
        self.assertIn("screenWidth:1.48,screenHeight:.82", ENGINE)
        # Bee assistant visual contract: wings sit at shoulder height and abdomen accents
        # follow the body shell rather than floating in front of the model.
        self.assertIn("by-.245,bz+.19,.42,.145,.075,[.58,.84,1,.42]", ENGINE)
        self.assertIn("by-.42,bz+.005,.405,.54,.31", ENGINE)
        self.assertIn("by-.64,bz+.107,.315,.066,.025", ENGINE)
        self.assertIn("Scene.prototype.addLink", ENGINE)
        self.assertIn("const desk=this.deskPositions&&this.deskPositions[i]", ENGINE)
        self.assertIn("dispose=function", ENGINE)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_webgl_scene_runtime_smoke(self):
        harness = ROOT / "tests" / "hive3d_webgl_smoke.mjs"
        result = subprocess.run(
            [shutil.which("node"), str(harness)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\\n" + result.stderr)
        self.assertIn("Hive3D smoke test passed", result.stdout)

    def test_scene_batches_are_depth_safe_and_disposable(self):
        self.assertIn('channels={opaque:', ENGINE)
        self.assertIn('channels.transparent', ENGINE)
        self.assertIn('gl.depthMask(false)', ENGINE)
        self.assertIn('Scene.prototype.drawBatch', ENGINE)
        self.assertIn('this.projectRect', ENGINE)
        self.assertIn('gl.deleteBuffer(pass[key])', ENGINE)

    def test_app_wiring_mode_persist_and_fallback(self):
        self.assertIn("function setupHiveSceneControls", APP)
        self.assertIn("function hiveSceneSync", APP)
        self.assertIn("function ensureHiveScene", APP)
        self.assertIn("orch.hiveView.v2", APP)
        self.assertIn('let hiveMode = "live3d"', APP)
        self.assertIn('localStorage.getItem("orch.hiveView.v2")', APP)
        self.assertIn('localStorage.setItem("orch.hiveView.v2", hiveMode)', APP)
        self.assertIn("hive-mode-3d", APP)
        self.assertIn("onFatal", APP)

    def test_styles_include_gl_host_overlay_and_flat_fallback(self):
        self.assertIn(".hive-gl", STYLE)
        self.assertIn(".hg-badge", STYLE)
        self.assertIn(".hive-reference", STYLE)
        self.assertIn(".hive-reference-artboard", STYLE)
        self.assertIn('display: block !important', STYLE)
        self.assertIn("#rd-hive-viewport.hive-mode-2d", STYLE)
        self.assertIn("#rd-hive-viewport.hive-mode-3d #rd-hive-cells", STYLE)
        self.assertIn("@media (prefers-reduced-motion: reduce)", STYLE)

    def test_3d_office_projects_live_step_info_onto_clickable_monitors(self):
        self.assertIn('"hg-monitor"', ENGINE)
        self.assertIn("screenMeta", ENGINE)
        self.assertIn("projectWorld", ENGINE)
        self.assertIn("layoutOverlayPositions", ENGINE)
        self.assertIn("displayTail", APP)
        self.assertIn("displayStatus", APP)
        self.assertIn("displayElapsed", APP)
        self.assertIn(".hg-monitor", STYLE)
        self.assertIn(".hive-reference-layer", STYLE)
        self.assertNotIn("豆包AI生成", ENGINE)
        self.assertIn('workbench-reference.png', INDEX)
        self.assertIn('"点击查看实时日志"', I18N)

    def test_i18n_covers_3d_scene_strings(self):
        self.assertIn("拖动旋转 · 滚轮缩放 · 右键升降", I18N)
        self.assertIn("当前环境不支持 WebGL，已切换 2D 列表", I18N)
        self.assertIn("3D 场景不可用，已切换 2D 列表", I18N)
        self.assertIn("点击格子看日志", I18N)

    def test_task_details_expose_checkpoint_and_project_memory_governance(self):
        self.assertIn('id="rd-checkpoints"', INDEX)
        self.assertIn('id="rd-checkpoints-restore"', INDEX)
        self.assertIn('id="rd-project-memory"', INDEX)
        self.assertIn("window.loadRunCheckpoints", APP)
        self.assertIn("window.restoreRunCheckpoints", APP)
        self.assertIn("window.decideProjectMemory", APP)
        self.assertIn("预计可回滚", APP) if "预计可回滚" in APP else self.assertIn("不保证可回滚", APP)
        self.assertIn("项目记忆治理", I18N)


if __name__ == "__main__":
    unittest.main()
