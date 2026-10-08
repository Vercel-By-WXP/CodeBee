/* hive3d.js — 蜂巢办公室 3D 场景（零依赖 WebGL2 迷你引擎）。
 *
 * 场景语义（对照参考效果图）：流水线阶段 = 办公室里沿 X 轴一字排开的工位
 * （左→右=执行顺序），每个工位=白桌+暗色代码屏显示器+LED 灯条+办公椅+
 * 一只低多边形蜜蜂；阶段的步骤=桌面上的一排状态色「稿卡」。
 *   蜂王   队首讲台上戴皇冠的大号蜜蜂，始终面向当前在干活的工位（分派中）。
 *   巡逻蜂 沿工位线巡视，有工位在跑就飞过去悬停督工。
 *   状态   稿卡/LED=状态灯：运行中 accent 脉动、失败红、完成绿、排队暗；
 *          蜜蜂=状态姿势：干活的打字颤翅、完成的闲坐、出错的趴桌、排队的打盹。
 *   颜色   状态色全部取自页面 CSS 变量（--accent/--ok/--bad/--warn/--muted），
 *          换肤/明暗主题自动跟随（MutationObserver 监听 html[data-skin]/[data-theme]）。
 * 交互：左键拖动=轨道旋转、滚轮=推拉、右键拖动=升降视角、双击空白=复位、
 *   空闲 8s 缓慢自转（prefers-reduced-motion 时全部静帧，只在数据/交互变化时重绘）。
 * 拾取：CPU 射线对工位包围盒求交——悬停出气泡卡、点按开日志（回调宿主页面）。
 * 标签：阶段徽章与运行中芯片是 HTML 投影（每帧把世界坐标锚点投到屏幕），
 *   文本内容完全由宿主（app.js）以同步数据供给，本文件不含任何用户可见文案。
 * 降级：WebGL2 不可用 / 上下文丢失 → create 返回 null / onFatal 回调，
 *   宿主负责切回 2D 列表。数据链路见 app.js renderHive → hiveSceneSync。 */
window.Hive3D = (function () {
  "use strict";

  /* ---------- 小工具 ---------- */
  function parseColor(v, fb) {
    const s = String(v || "").trim();
    let m = /^#([0-9a-f]{3})$/i.exec(s);
    if (m) return [parseInt(m[1][0] + m[1][0], 16) / 255, parseInt(m[1][1] + m[1][1], 16) / 255, parseInt(m[1][2] + m[1][2], 16) / 255];
    m = /^#([0-9a-f]{6})$/i.exec(s);
    if (m) return [parseInt(m[1].slice(0, 2), 16) / 255, parseInt(m[1].slice(2, 4), 16) / 255, parseInt(m[1].slice(4, 6), 16) / 255];
    m = /^rgba?\(([^)]+)\)$/i.exec(s);
    if (m) {
      const p = m[1].split(",").map(parseFloat);
      if (p.length >= 3 && p.every((x) => !isNaN(x))) return [p[0] / 255, p[1] / 255, p[2] / 255];
    }
    return fb;
  }
  const mix3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const easeOut = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);

  /* 列主序 4x4 */
  function mat4Persp(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  }
  function mat4LookAt(eye, ctr, up) {
    let zx = eye[0] - ctr[0], zy = eye[1] - ctr[1], zz = eye[2] - ctr[2];
    let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return new Float32Array([
      xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
      -(xx * eye[0] + xy * eye[1] + xz * eye[2]),
      -(yx * eye[0] + yy * eye[1] + yz * eye[2]),
      -(zx * eye[0] + zy * eye[1] + zz * eye[2]), 1]);
  }
  function mat4Mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }

  /* ---------- 几何 ---------- */
  /* 单位立方体（±0.5），实例端给 (sx, sy, sz) */
  function boxMesh() {
    const f = [
      [[-.5, -.5, .5], [.5, -.5, .5], [.5, .5, .5], [-.5, .5, .5], [0, 0, 1]],
      [[.5, -.5, -.5], [-.5, -.5, -.5], [-.5, .5, -.5], [.5, .5, -.5], [0, 0, -1]],
      [[.5, -.5, .5], [.5, -.5, -.5], [.5, .5, -.5], [.5, .5, .5], [1, 0, 0]],
      [[-.5, -.5, -.5], [-.5, -.5, .5], [-.5, .5, .5], [-.5, .5, -.5], [-1, 0, 0]],
      [[-.5, .5, .5], [.5, .5, .5], [.5, .5, -.5], [-.5, .5, -.5], [0, 1, 0]],
    ];
    const pos = [], nrm = [], idx = [];
    f.forEach((face) => {
      const base = pos.length / 3;
      for (let i = 0; i < 4; i++) { pos.push(...face[i]); nrm.push(...face[4]); }
      idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
    });
    return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), idx: new Uint16Array(idx) };
  }
  /* 低多边形蜜蜂（直立坐姿，面向 +z，y ∈ [0,~0.64]：腹部垂在身后下方、
   * 胸在上、头朝前——读作「坐在凳子上」而不是横躺的胶囊）。
   * part：0 腹部（金黑纹，纹路沿竖直的长轴）1 头胸（深色）
   * 2 翅膀（半透明，单独 index 段）3 皇冠（仅蜂王）。 */
  function beeMesh(isQueen) {
    const pos = [], nrm = [], part = [], idx = [];
    const sphere = (cx, cy, cz, rx, ry, rz, la, lo, pt) => {
      const base = pos.length / 3;
      for (let y = 0; y <= la; y++) {
        const th = (y / la) * Math.PI;
        for (let x = 0; x <= lo; x++) {
          const ph = (x / lo) * Math.PI * 2;
          const nx = Math.sin(th) * Math.cos(ph), ny = Math.cos(th), nz = Math.sin(th) * Math.sin(ph);
          pos.push(cx + nx * rx, cy + ny * ry, cz + nz * rz);
          nrm.push(nx, ny, nz);
          part.push(pt);
        }
      }
      for (let y = 0; y < la; y++) for (let x = 0; x < lo; x++) {
        const a = base + y * (lo + 1) + x, b = a + lo + 1;
        idx.push(a, b, a + 1, a + 1, b, b + 1);
      }
    };
    sphere(0, 0.17, -0.06, 0.145, 0.20, 0.145, 8, 10, 0);   // 腹部（竖长，垂在身后）
    sphere(0, 0.42, 0.05, 0.12, 0.115, 0.12, 7, 9, 1);      // 胸
    sphere(0, 0.55, 0.15, 0.088, 0.085, 0.088, 6, 8, 1);    // 头
    sphere(-0.042, 0.575, 0.218, 0.040, 0.046, 0.028, 4, 6, 1);  // 大眼睛（萌感的关键）
    sphere(0.042, 0.575, 0.218, 0.040, 0.046, 0.028, 4, 6, 1);
    const bodyEnd = idx.length;
    /* 触角：头顶两根小短棒 */
    const stick = (cx, cy, cz, sx, sy, sz, pt) => {
      const base = pos.length / 3;
      const vs = [];
      for (const zz of [-sz, sz]) for (const yy of [-sy, sy]) for (const xx of [-sx, sx]) vs.push([cx + xx, cy + yy, cz + zz]);
      const faces = [[0, 1, 3, 2, 0, 0, 1], [4, 6, 7, 5, 0, 0, -1], [1, 5, 7, 3, 1, 0, 0], [0, 2, 6, 4, -1, 0, 0], [2, 3, 7, 6, 0, 1, 0], [0, 4, 5, 1, 0, -1, 0]];
      faces.forEach((fc) => {
        for (let i = 0; i < 4; i++) { pos.push(...vs[fc[i]]); nrm.push(fc[4], fc[5], fc[6]); part.push(pt); }
        idx.push(base + 0, base + 1, base + 3, base + 0, base + 3, base + 2);
      });
    };
    stick(-0.055, 0.70, 0.13, 0.016, 0.11, 0.016, 1);
    stick(0.055, 0.70, 0.13, 0.016, 0.11, 0.016, 1);
    /* 翅膀：两片薄翼，绕翅根 (±0.085, 0.55) 拍打（顶点着色器动） */
    const wing = (sx) => {
      const base = pos.length / 3;
      pos.push(sx * 0.085, 0.55, 0.07, sx * 0.34, 0.64, 0.02, sx * 0.36, 0.64, -0.09, sx * 0.085, 0.55, -0.08);
      for (let i = 0; i < 4; i++) { nrm.push(sx * 0.25, 1, 0.1); part.push(2); }
      idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
    };
    wing(1); wing(-1);
    const wingIdx = idx.splice(bodyEnd);      // 翅膀单独一段
    if (isQueen) {
      const crownBox = (cx, cy, cz, sx, sy, sz) => stick(cx, cy, cz, sx, sy, sz, 3);
      crownBox(0, 0.665, 0.15, 0.10, 0.026, 0.10);          // 皇冠箍（头顶上方，别埋进头里）
      for (let i = 0; i < 3; i++) {
        const a = i * Math.PI * 2 / 3 + Math.PI / 6;
        crownBox(Math.cos(a) * 0.072, 0.715, 0.15 + Math.sin(a) * 0.072, 0.017, 0.038, 0.017);
      }
    }
    return {
      pos: new Float32Array(pos), nrm: new Float32Array(nrm), part: new Float32Array(part),
      idxBody: new Uint16Array(idx), idxWing: new Uint16Array(wingIdx),
    };
  }

  /* ---------- 着色器 ---------- */
  const MESH_VS = `#version 300 es
precision highp float;
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_nrm;
layout(location=2) in vec4 a_i0;   /* xyz=世界锚点 w=rotY */
layout(location=3) in vec4 a_i1;   /* xyz=三轴缩放 w=glow */
layout(location=4) in vec4 a_i2;   /* rgb=颜色 a=rim 强度 */
layout(location=5) in vec4 a_i3;   /* x=phase */
uniform mat4 u_vp;
uniform float u_time;
out vec3 v_n; out vec3 v_wp; out vec3 v_rgb; out float v_rim; out float v_glow; out float v_ph;
void main() {
  float ct = cos(a_i0.w), st = sin(a_i0.w);
  vec3 p = a_pos * a_i1.xyz;
  if (a_i1.w > 0.5) p *= 1.0 + 0.03 * sin(u_time * 3.0 + a_i3.x);
  p = vec3(p.x * ct - p.z * st, p.y, p.x * st + p.z * ct);
  vec3 wp = a_i0.xyz + p;
  v_n = vec3(a_nrm.x * ct - a_nrm.z * st, a_nrm.y, a_nrm.x * st + a_nrm.z * ct);
  v_wp = wp; v_rgb = a_i2.rgb; v_rim = a_i2.a; v_glow = a_i1.w; v_ph = a_i3.x;
  gl_Position = u_vp * vec4(wp, 1.0);
}`;
  const MESH_FS = `#version 300 es
precision highp float;
in vec3 v_n; in vec3 v_wp; in vec3 v_rgb; in float v_rim; in float v_glow; in float v_ph;
uniform vec3 u_eye; uniform float u_time; uniform vec3 u_accent;
out vec4 o_frag;
void main() {
  vec3 N = normalize(v_n);
  vec3 V = normalize(u_eye - v_wp);
  vec3 L1 = normalize(vec3(0.55, 0.95, 0.35));
  vec3 L2 = normalize(vec3(-0.62, 0.30, -0.50));
  float d1 = max(dot(N, L1), 0.0);
  d1 = d1 * d1 * 0.85 + d1 * 0.18;
  float d2 = max(dot(N, L2), 0.0) * 0.30;
  float spec = pow(max(dot(reflect(-L1, N), V), 0.0), 26.0) * 0.20;
  float rim = pow(1.0 - max(dot(N, V), 0.0), 2.8);
  vec3 col = v_rgb * (0.40 + d1 + d2) + vec3(spec);
  col += u_accent * rim * (0.08 + 0.62 * v_rim);
  if (v_glow > 0.5) col += v_rgb * (0.62 + 0.30 * sin(u_time * 2.6 + v_ph));
  o_frag = vec4(col, 1.0);
}`;
  const BEE_VS = `#version 300 es
precision highp float;
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_nrm;
layout(location=2) in float a_part;
layout(location=3) in vec4 a_i0;   /* xyz=世界锚点 w=rotY */
layout(location=4) in vec4 a_i1;   /* x=scale y=slump z=wingSpeed w=phase */
layout(location=5) in vec4 a_i2;   /* rgb=状态色 a=glow（>0.5 上下浮动） */
uniform mat4 u_vp;
uniform float u_time;
out vec3 v_n; out vec3 v_wp; out vec3 v_local; out float v_part; out vec3 v_tint; out float v_glow;
void main() {
  vec3 p = a_pos;
  if (a_part > 1.5 && a_part < 2.5) {
    float sx = sign(p.x);
    float ang = sin(u_time * max(a_i1.z, 4.0) + a_i1.w) * 0.5;
    float ca = cos(ang * sx), sa = sin(ang * sx);
    vec2 rel = vec2(p.x - sx * 0.085, p.y - 0.55);
    p.x = sx * 0.085 + rel.x * ca - rel.y * sa;
    p.y = 0.55 + rel.x * sa + rel.y * ca;
  }
  p *= a_i1.x;
  float cs = cos(a_i1.y), ss = sin(a_i1.y);
  vec2 rel2 = p.yz - vec2(0.38, 0.0);
  p.y = 0.38 + rel2.x * cs - rel2.y * ss;
  p.z = rel2.x * ss + rel2.y * cs;
  if (a_i2.a > 0.5) p.y += abs(sin(u_time * 3.2 + a_i1.w)) * 0.05;
  float ct = cos(a_i0.w), st = sin(a_i0.w);
  p = vec3(p.x * ct - p.z * st, p.y, p.x * st + p.z * ct);
  vec3 n = a_nrm;
  n = vec3(n.x * ct - n.z * st, n.y, n.x * st + n.z * ct);
  v_n = n; v_wp = a_i0.xyz + p; v_local = a_pos; v_part = a_part; v_tint = a_i2.rgb; v_glow = a_i2.a;
  gl_Position = u_vp * vec4(v_wp, 1.0);
}`;
  const BEE_FS = `#version 300 es
precision highp float;
in vec3 v_n; in vec3 v_wp; in vec3 v_local; in float v_part; in vec3 v_tint; in float v_glow;
uniform vec3 u_eye; uniform float u_time; uniform float u_wing;
out vec4 o_frag;
void main() {
  vec3 N = normalize(v_n);
  vec3 V = normalize(u_eye - v_wp);
  if (u_wing > 0.5) {
    float fres = pow(1.0 - max(dot(N, V), 0.0), 2.0);
    o_frag = vec4(vec3(0.55, 0.64, 0.80), 0.30 + 0.18 * fres);
    return;
  }
  vec3 L1 = normalize(vec3(0.55, 0.95, 0.35));
  vec3 L2 = normalize(vec3(-0.62, 0.30, -0.50));
  float d1 = max(dot(N, L1), 0.0);
  d1 = d1 * d1 * 0.85 + d1 * 0.18;
  float d2 = max(dot(N, L2), 0.0) * 0.28;
  float spec = pow(max(dot(reflect(-L1, N), V), 0.0), 22.0) * 0.25;
  vec3 col;
  if (v_part < 0.5) {
    float band = fract(v_local.y * 3.2);
    col = mix(vec3(1.0, 0.76, 0.22), vec3(0.15, 0.11, 0.06), smoothstep(0.36, 0.55, band));
    col += v_tint * v_glow * (0.40 + 0.22 * sin(u_time * 2.8));
  } else if (v_part < 1.5) {
    col = vec3(0.23, 0.17, 0.10);
  } else {
    col = vec3(0.97, 0.80, 0.30);   /* 皇冠 */
    spec *= 1.7;
  }
  float rim = pow(1.0 - max(dot(N, V), 0.0), 2.8);
  vec3 lit = col * (0.42 + d1 + d2) + vec3(spec) + v_tint * rim * (0.10 + 0.30 * v_glow);
  o_frag = vec4(lit, 1.0);
}`;
  const GROUND_VS = `#version 300 es
precision highp float;
layout(location=0) in vec2 a_xz;
uniform mat4 u_vp; uniform float u_y;
out vec2 v_xz;
void main() {
  v_xz = a_xz;
  gl_Position = u_vp * vec4(a_xz.x, u_y, a_xz.y, 1.0);
}`;
  const GROUND_FS = `#version 300 es
precision highp float;
in vec2 v_xz;
uniform vec3 u_accent; uniform float u_pitch;
out vec4 o_frag;
float hexDist(vec2 p) { p = abs(p); return max(dot(p, vec2(0.5, 0.8660254)), p.x); }
void main() {
  vec2 uv = vec2(v_xz.y, v_xz.x) / u_pitch;
  vec2 r = vec2(1.0, 1.7320508), h = r * 0.5;
  vec2 a = mod(uv, r) - h;
  vec2 b = mod(uv - h, r) - h;
  vec2 gv = dot(a, a) < dot(b, b) ? a : b;
  float edge = smoothstep(0.40, 0.497, hexDist(gv));
  float fade = 1.0 - smoothstep(5.0, 17.0, length(v_xz));
  float glow = exp(-length(v_xz) * 0.12) * 0.16;
  float alpha = edge * 0.20 * fade + glow * fade;
  o_frag = vec4(u_accent, alpha);
}`;
  const SHADOW_VS = `#version 300 es
precision highp float;
layout(location=0) in vec2 a_corner;
layout(location=1) in vec4 a_s0;   /* cx, cz, radius, y */
layout(location=2) in vec4 a_s1;   /* rgb + alpha */
uniform mat4 u_vp;
out vec2 v_uv; out vec4 v_col;
void main() {
  v_uv = a_corner;
  v_col = a_s1;
  vec3 p = vec3(a_s0.x + a_corner.x * a_s0.z, a_s0.w, a_s0.y + a_corner.y * a_s0.z);
  gl_Position = u_vp * vec4(p, 1.0);
}`;
  const SHADOW_FS = `#version 300 es
precision mediump float;
in vec2 v_uv; in vec4 v_col;
out vec4 o_frag;
void main() {
  float d = length(v_uv);
  float a = (1.0 - smoothstep(0.15, 1.0, d)) * v_col.a;
  o_frag = vec4(v_col.rgb, a);
}`;
  const POINT_VS = `#version 300 es
precision highp float;
layout(location=0) in vec3 a_p;
layout(location=1) in vec4 a_c;
layout(location=2) in float a_s;
uniform mat4 u_vp; uniform float u_pxscale;
out vec4 v_c;
void main() {
  gl_Position = u_vp * vec4(a_p, 1.0);
  gl_PointSize = clamp(a_s * u_pxscale / max(gl_Position.w, 0.2), 1.0, 56.0);
  v_c = a_c;
}`;
  const POINT_FS = `#version 300 es
precision mediump float;
in vec4 v_c;
out vec4 o_frag;
void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float a = smoothstep(1.0, 0.15, dot(q, q)) * v_c.a;
  o_frag = vec4(v_c.rgb, a);
}`;

  function compile(gl, vsSrc, fsSrc) {
    const mk = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src); gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) || "shader");
      return sh;
    };
    const p = gl.createProgram();
    gl.attachShader(p, mk(gl.VERTEX_SHADER, vsSrc));
    gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fsSrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) || "link");
    return p;
  }

  /* ---------- 办公室布局常量 ---------- */
  const DESK_TOP = 0.62;
  /* 阶段 → 工蜂姿势（slump=俯仰角，负=抬头干正经活）+ 翅膀频率 + 发光 */
  const POSE = {
    running: { slump: -0.30, wing: 26, glow: 1 },
    done: { slump: -0.14, wing: 8, glow: 0 },
    failed: { slump: 0.78, wing: 4, glow: 0 },
    timeout: { slump: 0.7, wing: 4, glow: 0 },
    cancelled: { slump: 0.62, wing: 5, glow: 0 },
    queued: { slump: 0.5, wing: 6, glow: 0 },
  };

  /* ---------- 场景 ---------- */
  function HiveScene(opts) {
    this.opts = opts;
    this.canvas = opts.canvas;
    this.overlay = opts.overlay;
    this.onCellActivate = opts.onCellActivate || function () {};
    this.cellRefresh = opts.cellRefresh || function () { return {}; };
    this.gl = this.canvas.getContext("webgl2", { antialias: true, alpha: true, premultipliedAlpha: false });
    if (!this.gl) throw new Error("no webgl2");
    const gl = this.gl;
    this.progMesh = compile(gl, MESH_VS, MESH_FS);
    this.progBee = compile(gl, BEE_VS, BEE_FS);
    this.progGround = compile(gl, GROUND_VS, GROUND_FS);
    this.progPoint = compile(gl, POINT_VS, POINT_FS);

    /* 实例缓冲：家具（16 float/实例）与蜜蜂（12 float/实例）各一套；
     * 蜂王、巡逻蜂各有独立小缓冲——实例属性是 VAO 状态，每次绘制前必须重绑指针。 */
    this.furnBuf = gl.createBuffer();
    this.beeBuf = gl.createBuffer();
    this.specBuf = gl.createBuffer();    // 蜂王（皇冠网格）
    this.patrolBuf = gl.createBuffer();  // 巡逻蜂（工蜂网格）

    const mkFurnVAO = (geo) => {
      const vao = gl.createVertexArray();
      gl.bindVertexArray(vao);
      const vb = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, vb);
      const inter = new Float32Array(geo.pos.length * 2 / 3 * 2);
      for (let i = 0, j = 0; i < geo.pos.length / 3; i++) {
        inter[j++] = geo.pos[i * 3]; inter[j++] = geo.pos[i * 3 + 1]; inter[j++] = geo.pos[i * 3 + 2];
        inter[j++] = geo.nrm[i * 3]; inter[j++] = geo.nrm[i * 3 + 1]; inter[j++] = geo.nrm[i * 3 + 2];
      }
      gl.bufferData(gl.ARRAY_BUFFER, inter, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.furnBuf);
      for (let k = 0; k < 4; k++) {
        gl.enableVertexAttribArray(2 + k);
        gl.vertexAttribPointer(2 + k, 4, gl.FLOAT, false, 64, k * 16);
        gl.vertexAttribDivisor(2 + k, 1);
      }
      const ib = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, geo.idx, gl.STATIC_DRAW);
      return { vao, n: geo.idx.length };
    };
    this.box = mkFurnVAO(boxMesh());

    const mkBeeVAO = (geo) => {
      const vaoBody = gl.createVertexArray(), vaoWing = gl.createVertexArray();
      const vb = gl.createBuffer(), nb = geo.part.length * 4;
      const mk = (vao, idx) => {
        gl.bindVertexArray(vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, vb);
        const inter = new Float32Array(nb / 4 * 7);
        for (let i = 0; i < geo.part.length; i++) {
          inter[i * 7] = geo.pos[i * 3]; inter[i * 7 + 1] = geo.pos[i * 3 + 1]; inter[i * 7 + 2] = geo.pos[i * 3 + 2];
          inter[i * 7 + 3] = geo.nrm[i * 3]; inter[i * 7 + 4] = geo.nrm[i * 3 + 1]; inter[i * 7 + 5] = geo.nrm[i * 3 + 2];
          inter[i * 7 + 6] = geo.part[i];
        }
        gl.bufferData(gl.ARRAY_BUFFER, inter, gl.STATIC_DRAW);
        gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
        gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 28, 12);
        gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 28, 24);
        const ib = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this.beeBuf);
        for (let k = 0; k < 3; k++) {
          gl.enableVertexAttribArray(3 + k);
          gl.vertexAttribPointer(3 + k, 4, gl.FLOAT, false, 48, k * 16);
          gl.vertexAttribDivisor(3 + k, 1);
        }
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
        gl.bindVertexArray(null);
        return vao;
      };
      return {
        body: mk(vaoBody, geo.idxBody), wing: mk(vaoWing, geo.idxWing),
        nBody: geo.idxBody.length, nWing: geo.idxWing.length,
      };
    };
    this.beeW = mkBeeVAO(beeMesh(false));
    this.beeQ = mkBeeVAO(beeMesh(true));

    /* 地面 VAO */
    const G = 24;
    this.vaoGround = gl.createVertexArray();
    gl.bindVertexArray(this.vaoGround);
    const gb = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, gb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-G, -G, G, -G, G, G, -G, G]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const gbi = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gbi);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);

    /* 粒子 VAO */
    this.vaoPoint = gl.createVertexArray();
    gl.bindVertexArray(this.vaoPoint);
    this.ptBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ptBuf);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 32, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 32, 12);
    gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 32, 28);

    /* 软阴影 VAO（贴地圆盘，径向渐隐） */
    this.progShadow = compile(gl, SHADOW_VS, SHADOW_FS);
    this.uShadow = {
      vp: gl.getUniformLocation(this.progShadow, "u_vp"),
      strength: gl.getUniformLocation(this.progShadow, "u_strength"),
    };
    this.vaoShadow = gl.createVertexArray();
    gl.bindVertexArray(this.vaoShadow);
    const cb = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, cb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, 1, 1, -1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.shadowBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.shadowBuf);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 32, 0);
    gl.vertexAttribDivisor(1, 1);
    gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 4, gl.FLOAT, false, 32, 16);
    gl.vertexAttribDivisor(2, 1);
    const sbi = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, sbi);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);
    gl.bindVertexArray(null);

    this.uMesh = {
      vp: gl.getUniformLocation(this.progMesh, "u_vp"),
      time: gl.getUniformLocation(this.progMesh, "u_time"),
      eye: gl.getUniformLocation(this.progMesh, "u_eye"),
      accent: gl.getUniformLocation(this.progMesh, "u_accent"),
    };
    this.uBee = {
      vp: gl.getUniformLocation(this.progBee, "u_vp"),
      time: gl.getUniformLocation(this.progBee, "u_time"),
      eye: gl.getUniformLocation(this.progBee, "u_eye"),
      wing: gl.getUniformLocation(this.progBee, "u_wing"),
    };
    this.uGround = {
      vp: gl.getUniformLocation(this.progGround, "u_vp"),
      y: gl.getUniformLocation(this.progGround, "u_y"),
      accent: gl.getUniformLocation(this.progGround, "u_accent"),
      pitch: gl.getUniformLocation(this.progGround, "u_pitch"),
    };
    this.uPoint = {
      vp: gl.getUniformLocation(this.progPoint, "u_vp"),
      px: gl.getUniformLocation(this.progPoint, "u_pxscale"),
    };

    /* 相机状态（G 后缀 = 阻尼目标） */
    this.cam = { yaw: 0.42, pitch: 0.66, dist: 16, ty: 0.75, yawG: 0.42, pitchG: 0.66, distG: 16, tyG: 0.75 };
    this.model = null;
    this.furn = [];        // 家具实例 {x,y,z,rot,sx,sy,sz,glow,rgb,rim,phase,born}
    this.bees = [];        // 工蜂实例 {x,y,z,rot,scale,slump,wing,phase,rgb,glow}
    this.queen = null;     // {x,y,z,rot,scale,...}
    this.cells3d = [];     // 可拾取工位 {minX..maxZ, bx,by,bz, rel, runId, inst}
    this.bounds = { hw: 5, depth: 8, queenZ: -10 };
    this.hoverIdx = -1;
    this.active = false;
    this.raf = 0; this.lastT = 0;
    this.lastInteract = 0;
    this.spawning = 0;
    this.dirty = true;
    this.cssW = 0; this.cssH = 0; this.dpr = 1;
    this.reduced = false;
    this.theme = null;

    this._bindSurface();
    this.readTheme();
    this.resize();

    this._tick = this.tick.bind(this);
    this._onVis = () => { this.dirty = true; };
    document.addEventListener("visibilitychange", this._onVis);
    try {
      this._mq = window.matchMedia("(prefers-reduced-motion: reduce)");
      this._onMq = () => { this.reduced = this._mq.matches; this.dirty = true; };
      this._mq.addEventListener("change", this._onMq);
      this.reduced = this._mq.matches;
    } catch (e) { /* 老 API 忽略 */ }
    /* 换肤/明暗跟随 */
    this._themeObs = new MutationObserver(() => { this.readTheme(); this.rebuildColors(); this.dirty = true; });
    this._themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-skin", "data-theme"] });
    window.__hive3d = this;   // 无头探针锚点
  }

  HiveScene.prototype._bindSurface = function () {
    const cv = this.canvas;
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(cv.parentElement || cv);
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    let drag = null;
    const pointers = new Map();
    cv.addEventListener("pointerdown", (e) => {
      try { cv.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件/已释放指针 */ }
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      this.lastInteract = performance.now();
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY, btn: e.button,
        moved: 0, downT: performance.now(), shift: e.shiftKey };
      cv.classList.add("is-dragging");
    });
    cv.addEventListener("pointermove", (e) => {
      const p = pointers.get(e.pointerId);
      if (!drag || drag.id !== e.pointerId || !p) { this.updateHover(e); return; }
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      p.x = e.clientX; p.y = e.clientY;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      this.lastInteract = performance.now();
      if (pointers.size === 2) {
        const arr = [...pointers.values()];
        const dNow = Math.hypot(arr[0].x - arr[1].x, arr[0].y - arr[1].y);
        if (drag.pinchD) this.zoomAt(dNow / drag.pinchD);
        drag.pinchD = dNow;
        this.cam.tyG = clamp(this.cam.tyG + dy * 0.012, 0.2, 8);
      } else if (drag.btn === 2 || drag.shift) {
        this.cam.yawG += dx * 0.004;
        this.cam.tyG = clamp(this.cam.tyG + dy * 0.010 * this.cam.dist * 0.06, 0.2, 8);
      } else {
        this.cam.yawG += dx * 0.0062;
        this.cam.pitchG = clamp(this.cam.pitchG + dy * 0.005, 0.12, 1.35);
      }
      this.dirty = true;
      this.updateHover(e, drag.moved > 6);
    });
    const endPtr = (e) => {
      pointers.delete(e.pointerId);
      cv.classList.remove("is-dragging");
      if (drag && drag.id === e.pointerId) drag = null;
    };
    cv.addEventListener("pointerup", (e) => {
      const d = drag && drag.id === e.pointerId ? drag : null;
      endPtr(e);
      /* 点按（位移 <6px 且 <500ms）→ 拾取开日志；拖动不触发 */
      if (d && e.button !== 2 && performance.now() - d.downT < 500 && d.moved < 6) {
        const hit = this.pick(e);
        if (hit) this.onCellActivate(hit.runId, hit.rel);
      }
    });
    cv.addEventListener("pointercancel", endPtr);
    cv.addEventListener("dblclick", (e) => { if (!this.pick(e)) { this.resetView(); this.lastInteract = 0; } });
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.lastInteract = performance.now();
      /* deltaY<0（上滚）= 拉近 = dist 变小 */
      this.zoomAt(Math.exp(e.deltaY * 0.0011));
    }, { passive: false });
    cv.addEventListener("pointerleave", () => { this.setHover(-1); });
  };

  HiveScene.prototype.zoomAt = function (f) {
    this.cam.distG = clamp(this.cam.distG * f, 4, 80);
    this.dirty = true;
  };

  HiveScene.prototype.resetView = function () {
    this.fitView();
    this.dirty = true;
  };

  /* 依办公室尺寸自动取景：推 dist 直到关键采样点全部落在 NDC 0.85 内 */
  HiveScene.prototype.fitView = function () {
    const b = this.bounds;
    const cx = (b.x0 + b.x1) / 2;
    const samples = [
      [b.x0, 0.4, -1.6], [b.x1, 0.4, -1.6],
      [b.x0, 0.4, 1.8], [b.x1, 0.4, 1.8],
      [(b.x0 + b.x1) / 2, 2.0, 0], [0, 1.2, 1.5],
    ];
    const yaw = 0.42;   // 取景按标准机位算：复位视角=回到确定的取景（不受拖转后的 yaw 影响）
    const aspect = (this.cssW || 800) / (this.cssH || 460);
    const fov = 42 * Math.PI / 180;
    const eyeAt = (d) => {
      const cp = Math.cos(0.66);
      return [cx + d * cp * Math.sin(yaw), 0.75 + d * Math.sin(0.66), d * cp * Math.cos(yaw)];
    };
    const fits = (d) => {
      const eye = eyeAt(d);
      const vp = mat4Mul(mat4Persp(fov, aspect, 0.5, 240), mat4LookAt(eye, [cx, 0.75, 0], [0, 1, 0]));
      let worst = 0;
      for (const p of samples) {
        const x = vp[0] * (p[0] - 0) + vp[4] * p[1] + vp[8] * p[2] + vp[12];
        const y2 = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
        const w = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
        if (w <= 0.01) return false;
        worst = Math.max(worst, Math.abs(x / w), Math.abs(y2 / w));
      }
      return worst <= 0.92;
    };
    let d = Math.max(10, (b.x1 - b.x0) * 0.55);
    for (let i = 0; i < 26 && !fits(d); i++) d *= 1.10;
    d *= 0.72;   // 采样点含队首/队尾空白端，视觉主体再推近一档（评审实拍：占比仅 13-24%）
    this.cam.dist = this.cam.distG = clamp(d, 5, 90);
    this.cam.pitch = this.cam.pitchG = 0.66;
    this.cam.yaw = this.cam.yawG = 0.42;
    this.cam.ty = this.cam.tyG = 0.75;
  };

  HiveScene.prototype.resize = function () {
    const host = this.canvas.parentElement || this.canvas;
    const w = Math.max(80, host.clientWidth), h = Math.max(80, host.clientHeight);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (this.canvas.width !== Math.round(w * this.dpr) || this.canvas.height !== Math.round(h * this.dpr)) {
      this.canvas.width = Math.round(w * this.dpr);
      this.canvas.height = Math.round(h * this.dpr);
    }
    this.cssW = w; this.cssH = h;
    this.dirty = true;
  };

  HiveScene.prototype.readTheme = function () {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, fb) => parseColor(cs.getPropertyValue(n), fb);
    this.theme = {
      accent: v("--accent", [0.43, 0.66, 1.0]),
      ok: v("--ok", [0.25, 0.75, 0.54]),
      bad: v("--bad", [0.94, 0.41, 0.41]),
      warn: v("--warn", [0.89, 0.70, 0.29]),
      muted: v("--muted", [0.6, 0.6, 0.62]),
      panel: v("--panel", [0.13, 0.14, 0.18]),
      panel2: v("--panel2", [0.17, 0.18, 0.23]),
      text: v("--text", [0.92, 0.92, 0.94]),
      bg: v("--bg", [0.08, 0.09, 0.12]),
    };
  };

  HiveScene.prototype.rebuildColors = function () {
    if (!this.model) return;
    this.buildOffice(this.model, false);
  };

  /* ---------- 数据同步（宿主每轮 renderHive 调一次） ---------- */
  HiveScene.prototype.sync = function (model) {
    this.model = model;
    this.readTheme();
    const sig = model.lanes.map((l) => l.name + ":" + l.count + "(" + l.cells.map((c) => c.status[0]).join("") + ")").join("|");
    const fresh = sig !== this._sig;
    this._sig = sig;
    /* 悬停跨同步保持：轮询重渲染不该踢掉用户正看着的气泡（重建后按 key 重定位） */
    const prev = this.hoverIdx >= 0 ? this.cells3d[this.hoverIdx] : null;
    this.setHover(-1);
    this.buildOffice(model, fresh);
    /* 徽章/芯片只在结构变化时重建（轮询空转重建会闪 + 清空悬停气泡） */
    if (fresh || !this.tip) this.buildOverlay(model);
    if (prev) {
      const again = this.cells3d.find((c2) => c2.kind === prev.kind && c2.rel === prev.rel &&
        (prev.kind !== "stage" || c2.stageIdx === prev.stageIdx));
      if (again) { this.setHover(this.cells3d.indexOf(again)); this.fillTip(again); }
    }
    if (fresh) this.fitView();
    this.dirty = true;
  };

  /* 气泡内容填充（悬停与同步后回填共用）：稿卡=步骤气泡，工位=阶段气泡 */
  HiveScene.prototype.fillTip = function (hit) {
    if (!this.tip || !hit) return false;
    const key = hit.kind === "stage" ? "g:" + hit.stageIdx : "s:" + hit.rel;
    const cell = hit.kind === "step" ? (this._cellInfo || {})[hit.rel] : null;
    const lane = hit.kind === "stage" && this.model ? this.model.lanes[hit.stageIdx] : null;
    const html = (cell && cell.tip) || (lane && lane.tip) || "";
    if (!html) return false;
    if (this._tipKey !== key || this.tip.innerHTML === "") {
      this.tip.innerHTML = html;
      this._tipKey = key;
      this._tipLast = 0;
    }
    return true;
  };

  /* 蜜蜂办公室搭建（对照参考图）：每个阶段=流水线上的一个工位，沿 X 轴一字排开
   * （左→右=执行顺序，微波浪错位）；工位=白桌+显示器(暗色代码屏)+LED灯条+
   * 办公椅+一只工蜂；阶段的步骤=桌面上的一排「稿卡」（状态色，可悬停/点按）；
   * 队首讲台上站着戴皇冠的蜂王（面向在干活的工位），一只巡逻蜂沿工位线巡视、
   * 有活时飞过去悬停督工；两端绿植、队尾白板。 */
  HiveScene.prototype.buildOffice = function (model, freshSpawn) {
    const T = this.theme, now = performance.now();
    const furn = [], bees = [], cells3d = [];
    this._cellInfo = {};
    this.shadows = [];   // 软阴影盘（白板/窗墙/工位各块都会 push，必须最先初始化）
    const STATION_DX = 3.15;
    const n = model.lanes.length;
    const stX = (i) => (i - (n - 1) / 2) * STATION_DX;
    const stZ = (i) => (i % 2 ? 0.5 : -0.5);
    const pushF = (x, y, z, rot, sx, sy, sz, rgb, glow, rim, born) => {
      furn.push({ x, y, z, rot: rot || 0, sx, sy, sz, glow: glow || 0, rgb, rim: rim || 0,
        phase: (furn.length * 0.71) % 6.28, born: born || 0 });
      return furn.length - 1;
    };
    const PLANT = mix3(T.ok, [0.35, 0.75, 0.35], 0.5);
    const plant = (px, pz, s) => {
      pushF(px, 0.07 * s, pz, 0, 0.3 * s, 0.14 * s, 0.3 * s, mix3(T.panel2, T.bg, 0.2), 0, 0.05, 0);
      pushF(px, 0.3 * s, pz, 0, 0.34 * s, 0.34 * s, 0.34 * s, PLANT, 0, 0, 0);
      pushF(px + 0.1 * s, 0.52 * s, pz - 0.05 * s, 0.5, 0.22 * s, 0.22 * s, 0.22 * s, mix3(PLANT, [1, 1, 1], 0.12), 0, 0, 0);
    };
    const CODEC = [mix3(T.ok, [1, 1, 1], 0.15), mix3(T.accent, [1, 1, 1], 0.25),
      mix3(T.text, T.panel, 0.25), mix3(T.warn, [1, 1, 1], 0.2), mix3(T.accent2 || T.accent, [1, 1, 1], 0.35)];
    model.lanes.forEach((lane, li) => {
      lane.idx = li;
      const x = stX(li), z0 = stZ(li);
      const born = freshSpawn ? now + 55 * li : 0;
      const hasRun = lane.active;
      const anyBad = lane.cells.some((c) => c.status === "failed" || c.status === "timeout" || c.status === "cancelled");
      const allDone = lane.count > 0 && lane.settled >= lane.count && !lane.cells.some((c) => c.status === "running" || c.status === "queued");
      const st = hasRun ? "running" : anyBad ? "failed" : allDone ? "done" : "queued";
      /* 桌：两块侧板 + 白桌面 */
      pushF(x - 0.98, 0.30, z0, 0, 0.07, 0.60, 0.96, mix3(T.panel, T.bg, 0.18), 0, 0.05, born);
      pushF(x + 0.98, 0.30, z0, 0, 0.07, 0.60, 0.96, mix3(T.panel, T.bg, 0.18), 0, 0.05, born);
      pushF(x, DESK_TOP, z0, 0, 2.1, 0.09, 1.0, mix3(T.panel, [1, 1, 1], 0.25), 0, 0.06, born);
      /* LED 灯条（桌沿）：干活=accent 脉动，完结=ok 常亮，排队=暗 */
      const ledC = st === "running" ? T.accent : st === "done" ? mix3(T.ok, T.panel, 0.2)
        : st === "failed" ? T.bad : mix3(T.panel2, T.bg, 0.3);
      const led = pushF(x, DESK_TOP - 0.06, z0 + 0.505, 0, 1.9, 0.07, 0.04, ledC,
        st === "running" || st === "failed" ? 1 : 0, hasRun ? 0.3 : 0, born);
      /* 显示器：立柱 + 深色边框 + 暗色代码屏（几条语法高亮小色条） */
      const monY = DESK_TOP + 0.09;
      pushF(x - 0.18, monY + 0.07, z0 - 0.30, 0, 0.30, 0.04, 0.16, mix3(T.panel, T.bg, 0.3), 0, 0, born);
      pushF(x - 0.18, monY + 0.22, z0 - 0.36, 0, 0.09, 0.30, 0.07, mix3(T.panel, T.bg, 0.3), 0, 0, born);
      pushF(x - 0.18, monY + 0.62, z0 - 0.36, 0, 1.06, 0.66, 0.06, mix3(T.panel, T.bg, 0.45), 0, 0.08, born);
      const scrC = st === "failed" ? mix3([0.06, 0.07, 0.10], T.bad, 0.12) : st === "running" ? mix3([0.06, 0.07, 0.10], T.accent, 0.10) : [0.06, 0.07, 0.10];
      pushF(x - 0.18, monY + 0.62, z0 - 0.325, 0, 0.99, 0.59, 0.014, scrC, 0, 0, born);
      const cw = [0.52, 0.36, 0.60, 0.30, 0.44];
      for (let k = 0; k < 5; k++) {
        pushF(x - 0.18 - 0.40 + cw[k] / 2, monY + 0.86 - k * 0.093, z0 - 0.315, 0,
          cw[k], 0.026, 0.01, CODEC[k % CODEC.length], 0, 0, born);
      }
      /* 键盘 + 鼠标 */
      pushF(x - 0.20, DESK_TOP + 0.055, z0 + 0.12, 0, 0.52, 0.035, 0.18, mix3(T.panel2, T.bg, 0.15), 0, 0, born);
      pushF(x + 0.22, DESK_TOP + 0.05, z0 + 0.13, 0, 0.10, 0.03, 0.14, mix3(T.panel2, T.bg, 0.15), 0, 0, born);
      /* 桌面小物：马克杯 / 文件摞（隔一个工位放一样，避免复制粘贴感） */
      if (li % 2 === 0) {
        pushF(x + 0.62, DESK_TOP + 0.055, z0 - 0.16, 0, 0.095, 0.10, 0.095, mix3(T.panel, T.text, 0.45), 0, 0.08, born);
      } else {
        pushF(x - 0.86, DESK_TOP + 0.03, z0 - 0.28, 0, 0.30, 0.022, 0.22, mix3(T.panel2, T.accent, 0.25), 0, 0, born);
        pushF(x - 0.85, DESK_TOP + 0.055, z0 - 0.27, 0, 0.26, 0.022, 0.20, mix3(T.panel2, T.text, 0.35), 0, 0, born);
      }
      /* 办公椅 + 工蜂（面向屏幕） */
      pushF(x + 0.05, 0.02, z0 + 0.62, 0, 0.48, 0.045, 0.48, mix3(T.panel2, T.bg, 0.35), 0, 0, born);
      pushF(x + 0.05, 0.24, z0 + 0.62, 0, 0.08, 0.42, 0.08, mix3(T.panel2, T.bg, 0.3), 0, 0, born);
      pushF(x + 0.05, 0.47, z0 + 0.62, 0, 0.52, 0.07, 0.48, mix3(T.panel2, T.accent, 0.10), 0, 0, born);
      pushF(x + 0.05, 0.72, z0 + 0.88, 0, 0.48, 0.36, 0.07, mix3(T.panel2, T.accent, 0.10), 0, 0, born);
      const pose = st === "running" ? POSE.running : st === "failed" ? POSE.failed
        : st === "done" ? POSE.done : POSE.queued;
      bees.push({
        x: x + 0.05, y: 0.52, z: z0 + 0.58, rot: Math.PI, scale: 1.26,
        slump: pose.slump, wing: pose.wing, phase: (li * 1.3) % 6.28,
        rgb: st === "running" ? T.accent : st === "failed" ? T.bad
          : st === "done" ? mix3(T.ok, T.panel, 0.5) : mix3(T.panel2, T.text, 0.2),
        glow: pose.glow, born,
      });
      /* 桌面上的稿卡：每个步骤一张（状态色，可悬停/点按） */
      lane.cells.forEach((c, ci) => {
        const cx = x - 0.78 + ci * 0.24;
        const cc = c.status === "running" ? T.accent : c.status === "done" ? T.ok
          : c.status === "failed" ? T.bad : c.status === "timeout" ? T.warn
          : c.status === "cancelled" ? T.muted : mix3(T.panel2, T.text, 0.2);
        const ci2 = pushF(cx, DESK_TOP + 0.062, z0 + 0.33, 0, 0.19, 0.045, 0.26, cc,
          c.status === "running" ? 1 : 0, 0, born);
        this._cellInfo[c.rel] = c;
        cells3d.push({
          kind: "step", minX: cx - 0.12, maxX: cx + 0.12, minY: DESK_TOP, maxY: DESK_TOP + 0.30,
          minZ: z0 + 0.18, maxZ: z0 + 0.48,
          bx: cx, by: DESK_TOP + 0.34, bz: z0 + 0.33,
          rel: c.rel, runId: model.runId, inst: ci2, stageIdx: li,
        });
      });
      lane._badgeAnchor = { x, y: monY + 1.12, z: z0 - 0.36 };
      lane._stageCellRel = (lane.cells.find((c) => c.status === "running") || lane.cells[lane.cells.length - 1] || {}).rel || "";
      cells3d.push({
        kind: "stage", minX: x - 1.15, maxX: x + 1.15, minY: 0, maxY: 1.5,
        minZ: z0 - 0.75, maxZ: z0 + 0.95,
        bx: x, by: 1.35, bz: z0 + 0.3,
        rel: lane._stageCellRel, runId: model.runId, inst: led, stageIdx: li,
      });
    });
    /* 队首：讲台 + 蜂王；队尾：白板；两端绿植 */
    const qx = stX(0) - 3.0;
    pushF(qx, 0, -0.2, 0, 1.5, 0.5, 1.5, mix3(T.panel, T.warn, 0.10), 0, 0.4,
      freshSpawn ? now + 55 * n : 0);
    this.queen = {
      x: qx, y: 0.5, z: -0.2, rot: Math.PI / 2, scale: 2.0, slump: 0.02, wing: 7,
      phase: 2.1, rgb: [1.0, 0.84, 0.40], glow: 0.32, born: freshSpawn ? now + 55 * n : 0,
    };
    this.patrol = { x: stX(0), y: 1.9, z: 1.8, rot: 0, scale: 1.3, slump: 0, wing: 30, phase: 4.2,
      rgb: [1.0, 0.79, 0.30], glow: 0.62, born: 0 };
    /* 工位 + 椅子的软阴影 */
    model.lanes.forEach((lane, li) => {
      this.shadows.push([stX(li), stZ(li), 1.5, 0.011]);
      this.shadows.push([stX(li) + 0.05, stZ(li) + 0.62, 0.55, 0.013]);
    });
    const wbX = stX(n - 1) + 2.6;
    pushF(wbX - 0.42, 0.5, -1.25, 0.6, 0.07, 1.0, 0.07, mix3(T.panel2, T.bg, 0.2), 0, 0, 0);
    pushF(wbX + 0.42, 0.5, -0.95, 0.6, 0.07, 1.0, 0.07, mix3(T.panel2, T.bg, 0.2), 0, 0, 0);
    pushF(wbX, 1.02, -1.1, 0.6, 1.5, 0.94, 0.07, mix3(T.panel, T.text, 0.55), 0, 0.06, 0);
    pushF(wbX - 0.3, 1.14, -1.045, 0.6, 0.5, 0.035, 0.012, mix3(T.accent, [1, 1, 1], 0.2), 0, 0, 0);
    pushF(wbX + 0.12, 1.02, -1.045, 0.6, 0.34, 0.035, 0.012, mix3(T.ok, [1, 1, 1], 0.2), 0, 0, 0);
    pushF(wbX + 0.4, 0.9, -1.045, 0.6, 0.22, 0.035, 0.012, mix3(T.warn, [1, 1, 1], 0.2), 0, 0, 0);
    this.shadows.push([wbX, -1.1, 0.9, 0.012]);
    plant(stX(0) - 1.7, 1.1, 1.15);
    plant(stX(n - 1) + 1.6, 1.2, 1.0);
    /* 背景窗墙：三扇亮窗（近白的透光板 + 深色窗棂条；贴地 + 接触阴影，
     * 明暗主题都读作「光源」而不是障碍物） */
    const wz = -2.9;
    const wx0 = stX(0) - 2.0, wx1 = stX(n - 1) + 2.0;
    const wspan = Math.max(6, wx1 - wx0);
    for (let wi = 0; wi < 3; wi++) {
      const wx = wx0 + wspan * (0.16 + wi * 0.34);
      /* 窗板自带发光（立面吃不到主光，不发亮就是一块中灰板） */
      pushF(wx, 0.95, wz, 0, wspan * 0.24, 1.7, 0.08, mix3([1, 1, 1], T.text, 0.10), 0.6, 0.04, 0);
      pushF(wx, 0.95, wz + 0.055, 0, 0.06, 1.7, 0.03, mix3(T.panel2, T.bg, 0.35), 0, 0, 0);
      pushF(wx, 1.84, wz + 0.02, 0, wspan * 0.24 + 0.06, 0.07, 0.10, mix3(T.panel2, T.bg, 0.35), 0, 0, 0);
      this.shadows.push([wx, wz + 0.4, wspan * 0.17, 0.012]);
    }
    /* 软阴影（贴地径向渐隐圆盘）：桌子/椅子/讲台/绿植 */
    this.shadows.push([qx, -0.2, 1.25, 0.011]);
    this.shadows.push([stX(0) - 1.7, 1.1, 0.5, 0.012]);
    this.shadows.push([stX(n - 1) + 1.6, 1.2, 0.42, 0.012]);
    this.bounds = { x0: qx - 2.2, x1: wbX + 1.2, zHalf: 3.4 };
    this.furn = furn;
    this.bees = bees;
    this.cells3d = cells3d;
    this.spawning = freshSpawn ? now + 55 * n + 36 * 8 + 500 : 0;
    this.uploadFurn(true);
    this.uploadBees(this.beeBuf, this.bees);
    this.uploadSpecial();
  };

  HiveScene.prototype.uploadFurn = function (forceSpawnScale) {
    const gl = this.gl, now = performance.now();
    const data = new Float32Array(this.furn.length * 16);
    let spawning = false;
    this.furn.forEach((it, i) => {
      let sc = 1;
      if (it.born) {
        const t2 = (now - it.born) / 430;
        if (t2 < 1) { sc = easeOut(Math.max(0, t2)); spawning = true; }
        else it.born = 0;
      }
      const o = i * 16;
      data[o] = it.x; data[o + 1] = it.y; data[o + 2] = it.z; data[o + 3] = it.rot;
      data[o + 4] = it.sx * sc; data[o + 5] = Math.max(0.001, it.sy * sc); data[o + 6] = it.sz * sc; data[o + 7] = it.glow;
      data[o + 8] = it.rgb[0]; data[o + 9] = it.rgb[1]; data[o + 10] = it.rgb[2];
      data[o + 11] = it.instHover ? 0.9 : it.rim;
      data[o + 12] = it.phase; data[o + 13] = 0; data[o + 14] = 0; data[o + 15] = 0;
    });
    gl.bindBuffer(gl.ARRAY_BUFFER, this.furnBuf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
    if (spawning) this.spawning = now + 60;
    else if (!spawning && this.spawning && now > this.spawning) this.spawning = 0;
  };

  HiveScene.prototype.uploadBees = function (buf, list) {
    const gl = this.gl, now = performance.now();
    const data = new Float32Array(list.length * 12);
    let spawning = false;
    list.forEach((b, i) => {
      let sc = 1;
      if (b.born) {
        const t2 = (now - b.born) / 430;
        if (t2 < 1) { sc = 0.25 + 0.75 * easeOut(Math.max(0, t2)); spawning = true; }
        else b.born = 0;
      }
      const o = i * 12;
      data[o] = b.x; data[o + 1] = b.y; data[o + 2] = b.z; data[o + 3] = b.rot;
      data[o + 4] = b.scale * sc; data[o + 5] = b.slump; data[o + 6] = b.wing; data[o + 7] = b.phase;
      data[o + 8] = b.rgb[0]; data[o + 9] = b.rgb[1]; data[o + 10] = b.rgb[2]; data[o + 11] = b.glow;
    });
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
    return spawning;
  };

  /* 蜂王走 specBuf（皇冠网格）；巡逻蜂走 patrolBuf（工蜂网格，无皇冠） */
  HiveScene.prototype.uploadSpecial = function () {
    this.uploadBees(this.specBuf, this.queen ? [this.queen] : []);
  };

  /* ---------- 投影标签（徽章/芯片/气泡） ---------- */
  HiveScene.prototype.buildOverlay = function (model) {
    const ov = this.overlay;
    ov.innerHTML = "";
    this.laneMeta = [];
    this.chips = [];
    model.lanes.forEach((lane) => {
      const el = document.createElement("div");
      el.className = "hg-badge" + (lane.active ? " hg-active" : "") + (lane.settled >= lane.count && lane.count ? " hg-done" : "");
      el.innerHTML =
        '<span class="hg-idx">' + String(lane.idx + 1).padStart(2, "0") + "</span>" +
        '<span class="hg-name"></span>' +
        (lane.count ? '<span class="hg-meta">×' + lane.count + "</span>" : "") +
        (lane.settled && lane.count ? '<span class="hg-prog">' + lane.settled + "/" + lane.count + "</span>" : "") +
        (lane.active ? '<i class="hg-dot"></i>' : "");
      el.querySelector(".hg-name").textContent = lane.name;   // textContent 防注入
      ov.appendChild(el);
      this.laneMeta.push({ el, lane });
    });
    model.lanes.forEach((lane) => {
      lane.cells.forEach((c) => {
        if (c.status !== "running") return;
        const el = document.createElement("div");
        el.className = "hg-chip";
        ov.appendChild(el);
        this.chips.push({ el, rel: c.rel, cell: c, last: "" });
      });
    });
    this.tip = document.createElement("div");
    this.tip.className = "hg-tip hidden";
    ov.appendChild(this.tip);
    this.tipCell = -1;
    this._tipLast = 0;
  };

  HiveScene.prototype.project = function (x, y, z, vp) {
    const w = vp[3] * x + vp[7] * y + vp[11] * z + vp[15];
    if (w <= 0.02) return null;
    const cx = (vp[0] * x + vp[4] * y + vp[8] * z + vp[12]) / w;
    const cy = (vp[1] * x + vp[5] * y + vp[9] * z + vp[13]) / w;
    return { x: (cx * 0.5 + 0.5) * this.cssW, y: (1 - (cy * 0.5 + 0.5)) * this.cssH, w };
  };

  HiveScene.prototype.updateOverlay = function (vp, now) {
    /* 徽章：随景深缩放 + 屏幕空间横向避让（同排工位投影 y 几乎相同，
     * 不避让会连成瀑布互相叠压）；缩太小时退化成只显示序号（hg-mini）。 */
    const items = [];
    this.laneMeta.forEach(({ el, lane }) => {
      const a = lane._badgeAnchor;
      const p = this.project(a.x, a.y, a.z, vp);
      if (!p) { el.style.display = "none"; return; }
      el.style.display = "";
      items.push({ el, p });
    });
    items.sort((a, b) => a.p.x - b.p.x);
    let lastRight = -1e9;
    items.forEach((it) => {
      const sc = clamp(13.5 / it.p.w, 0.58, 1.05);
      const mini = sc < 0.78;
      it.el.classList.toggle("hg-mini", mini);
      const wpx = (it.el.offsetWidth || 120) * sc;
      let x = it.p.x;
      /* 展开态才做横向避让；mini 序号胶囊只有 ~30px 宽，推挤反而会让序号
       * 漂离自家工位（评审实拍：01 漂到墙板缺口） */
      if (!mini) {
        x = Math.max(x - wpx / 2, lastRight + 8) + wpx / 2;
        lastRight = x + wpx / 2;
      }
      it.el.style.transform = "translate(" + x.toFixed(1) + "px," + it.p.y.toFixed(1) + "px) translate(-50%,-100%) scale(" + sc.toFixed(3) + ")";
    });
    this.chips.forEach((ch) => {
      const cd = this.cells3d.find((c) => c.rel === ch.rel);
      const p = cd ? this.project(cd.bx, cd.by, cd.bz, vp) : null;
      if (!p) { ch.el.style.display = "none"; return; }
      ch.el.style.display = "";
      ch.el.style.transform = "translate(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px) translate(-50%,-100%)";
      if (now - (ch._t || 0) > 500) {
        ch._t = now;
        const rf = this.cellRefresh(ch.rel) || {};
        const txt = (ch.cell.role || "") + (rf.elapsed ? " · " + rf.elapsed : "");
        if (txt !== ch.last) { ch.last = txt; ch.el.textContent = txt; }
      }
    });
    /* 悬停气泡：跟随 + 2Hz 刷新实时尾巴/思考（meta 行随 sync 静态重建） */
    if (this.tipCell >= 0 && this.cells3d[this.tipCell]) {
      const cd = this.cells3d[this.tipCell];
      const p = this.project(cd.bx, cd.by - 0.1, cd.bz, vp);
      if (!p) { this.tip.classList.add("hidden"); }
      else {
        this.tip.classList.remove("hidden");
        const tw = this.tip.offsetWidth || 240;
        const lx = clamp(p.x + 16, 6, Math.max(6, this.cssW - tw - 8));
        const ly = clamp(p.y - 14, 6, Math.max(6, this.cssH - 40));
        this.tip.style.transform = "translate(" + lx.toFixed(1) + "px," + ly.toFixed(1) + "px)";
        if (now - this._tipLast > 500) {
          this._tipLast = now;
          const rf = this.cellRefresh(cd.rel) || {};
          const tailEl = this.tip.querySelector(".hg-tip-tail");
          const thinkEl = this.tip.querySelector(".hg-tip-think");
          if (tailEl && rf.tail) tailEl.textContent = rf.tail;
          if (thinkEl) thinkEl.textContent = rf.think || "";
        }
      }
    }
  };

  /* ---------- 拾取：射线 vs 工位包围盒 ---------- */
  HiveScene.prototype.pick = function (e) {
    const rect = this.canvas.getBoundingClientRect();
    const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = 1 - ((e.clientY - rect.top) / rect.height) * 2;
    const eye = this._eye, tgt = this._tgt;
    if (!eye) return null;
    let fx = tgt[0] - eye[0], fy = tgt[1] - eye[1], fz = tgt[2] - eye[2];
    let l = Math.hypot(fx, fy, fz) || 1; fx /= l; fy /= l; fz /= l;
    let rx = -fz, ry = 0, rz = fx;              // right = f × up(0,1,0)
    l = Math.hypot(rx, ry, rz) || 1; rx /= l; rz /= l;
    const ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;
    const aspect = (this.cssW || 800) / (this.cssH || 460);
    const tf = Math.tan(21 * Math.PI / 180);
    let dx = fx + rx * ndcX * tf * aspect + ux * ndcY * tf;
    let dy = fy + ry * ndcX * tf * aspect + uy * ndcY * tf;
    let dz = fz + rz * ndcX * tf * aspect + uz * ndcY * tf;
    l = Math.hypot(dx, dy, dz) || 1; dx /= l; dy /= l; dz /= l;
    let best = null, bestT = 1e9, bestStage = null, bestStageT = 1e9;
    for (const c of this.cells3d) {
      let tmin = 0, tmax = 1e9;
      const axes = [[eye[0], dx, c.minX, c.maxX], [eye[1], dy, c.minY, c.maxY], [eye[2], dz, c.minZ, c.maxZ]];
      let ok = true;
      for (const [o, d, lo, hi] of axes) {
        if (Math.abs(d) < 1e-9) { if (o < lo || o > hi) { ok = false; break; } continue; }
        let t1 = (lo - o) / d, t2 = (hi - o) / d;
        if (t1 > t2) { const tt = t1; t1 = t2; t2 = tt; }
        tmin = Math.max(tmin, t1); tmax = Math.min(tmax, t2);
        if (tmin > tmax) { ok = false; break; }
      }
      if (!ok || tmax < 0) continue;
      const t = tmin > 0 ? tmin : tmax;
      /* 稿卡优先于工位：稿卡放在工位包围盒内部，最近命中会永远被工位抢走 */
      if (c.kind === "step") { if (t < bestT) { bestT = t; best = c; } }
      else if (t < bestStageT) { bestStageT = t; bestStage = c; }
    }
    return best || bestStage;
  };

  HiveScene.prototype.setHover = function (idx) {
    if (this.hoverIdx === idx) return;
    const prev = this.hoverIdx;
    this.hoverIdx = idx;
    if (prev >= 0 && this.furn[this.cells3d[prev] && this.cells3d[prev].inst]) {
      this.furn[this.cells3d[prev].inst].instHover = false;
    }
    if (idx >= 0 && this.furn[this.cells3d[idx].inst]) this.furn[this.cells3d[idx].inst].instHover = true;
    this.canvas.style.cursor = idx >= 0 ? "pointer" : "grab";
    if (this.tip) {
      if (idx < 0) { this.tip.classList.add("hidden"); this.tipCell = -1; }
      else { this.tipCell = idx; this.tip.classList.remove("hidden"); }
    }
    this.uploadFurn();
    this.dirty = true;
  };

  HiveScene.prototype.updateHover = function (e, forceOff) {
    if (forceOff) { this.setHover(-1); return; }
    const hit = this.pick(e);
    const idx = hit ? this.cells3d.indexOf(hit) : -1;
    if (idx >= 0) this.fillTip(hit);
    this.setHover(idx);
  };

  /* ---------- 帧循环 ---------- */
  HiveScene.prototype.setActive = function (on) {
    on = !!on;
    if (this.active === on) return;
    this.active = on;
    if (on) {
      this.resize();
      this.lastT = 0;
      if (!this.raf) this.raf = requestAnimationFrame(this._tick);
    } else if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
  };

  HiveScene.prototype.tick = function (now) {
    if (!this.active) { this.raf = 0; return; }
    this.raf = requestAnimationFrame(this._tick);
    if (document.hidden) return;
    if (!this.canvas.isConnected || this.canvas.offsetParent === null) return;  // 页签隐藏时零开销
    const dt = this.lastT ? Math.min(0.05, (now - this.lastT) / 1000) : 0.016;
    this.lastT = now;
    const c = this.cam;
    const k = Math.min(1, dt * 9);
    let moving = false;
    for (const key of ["yaw", "pitch", "dist", "ty"]) {
      const g = c[key + "G"], cur = c[key];
      const nv = cur + (g - cur) * k;
      if (Math.abs(g - nv) > 1e-4 * Math.max(1, Math.abs(g))) moving = true;
      c[key] = nv;
    }
    if (!this.reduced && performance.now() - this.lastInteract > 8000) {
      c.yawG += dt * 0.08; moving = true;
    }
    if (this.spawning || this.dirty || moving || !this.reduced) this.draw(now);
  };

  /* 巡逻蜂路径：有在干活的工位 → 绕它悬停督工；否则沿工位线前方来回巡视 */
  HiveScene.prototype.patrolPos = function (time) {
    const b = this.bounds;
    const active = this.model && this.model.lanes.find((l) => l.active);
    if (active) {
      const cd = this.cells3d.find((c) => c.kind === "stage" && c.stageIdx === active.idx);
      if (cd) {
        const ang = time * 1.1;
        return { x: cd.bx + Math.cos(ang) * 0.85, y: 1.75 + Math.sin(time * 2.2) * 0.08, z: cd.bz + Math.sin(ang) * 0.85,
          rot: ang + Math.PI / 2 };
      }
    }
    const span = (b.x1 - b.x0) / 2 - 1.5;
    const cx = (b.x0 + b.x1) / 2;
    const u = Math.sin(time * 0.35) * span;
    const dir = Math.cos(time * 0.35) >= 0 ? 1 : -1;
    return { x: cx + u, y: 1.85 + Math.sin(time * 2.4) * 0.12, z: 2.1 + Math.sin(time * 0.8) * 0.4,
      rot: dir > 0 ? Math.PI / 2 : -Math.PI / 2 };
  };

  HiveScene.prototype.draw = function (now) {
    const gl = this.gl;
    this.dirty = false;
    this._frames = (this._frames || 0) + 1;
    const c = this.cam;
    const aspect = this.cssW / this.cssH;
    const b = this.bounds;
    const cx = (b.x0 + b.x1) / 2;
    const ty = clamp(c.ty, 0.2, 8);
    const cp = Math.cos(c.pitch);
    const eye = [cx + c.dist * cp * Math.sin(c.yaw), ty + c.dist * Math.sin(c.pitch), c.dist * cp * Math.cos(c.yaw)];
    this._eye = eye; this._tgt = [cx, ty, 0];
    const vp = mat4Mul(mat4Persp(42 * Math.PI / 180, aspect, 0.5, 260), mat4LookAt(eye, this._tgt, [0, 1, 0]));
    this._vp = vp;
    const time = this.reduced ? 12.0 : now / 1000;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.disable(gl.CULL_FACE);
    gl.enable(gl.DEPTH_TEST);

    /* 地面：六边形暗纹地毯 */
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.useProgram(this.progGround);
    gl.uniformMatrix4fv(this.uGround.vp, false, vp);
    gl.uniform1f(this.uGround.y, 0);
    gl.uniform1f(this.uGround.pitch, 1.35);
    gl.uniform3fv(this.uGround.accent, mix3(this.theme.accent, this.theme.text, 0.30));
    gl.bindVertexArray(this.vaoGround);
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);

    /* 软阴影：家具落地感的关键 */
    if (this.shadows && this.shadows.length) {
      const sd = new Float32Array(this.shadows.length * 4);
      this.shadows.forEach((s, i) => { sd[i * 4] = s[0]; sd[i * 4 + 1] = s[1]; sd[i * 4 + 2] = s[2]; sd[i * 4 + 3] = s[3]; });
      gl.useProgram(this.progShadow);
      gl.uniformMatrix4fv(this.uShadow.vp, false, vp);
      gl.uniform1f(this.uShadow.strength, 0.30);
      gl.bindVertexArray(this.vaoShadow);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.shadowBuf);
      gl.bufferData(gl.ARRAY_BUFFER, sd, gl.DYNAMIC_DRAW);
      gl.drawElementsInstanced(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0, this.shadows.length);
    }
    gl.depthMask(true);
    gl.disable(gl.BLEND);

    /* 家具：工位 + 地毯 + 讲台 */
    if (this.spawning) this.uploadFurn(true);
    gl.useProgram(this.progMesh);
    gl.uniformMatrix4fv(this.uMesh.vp, false, vp);
    gl.uniform1f(this.uMesh.time, time);
    gl.uniform3fv(this.uMesh.eye, eye);
    gl.uniform3fv(this.uMesh.accent, this.theme.accent);
    gl.bindVertexArray(this.box.vao);
    gl.drawElementsInstanced(gl.TRIANGLES, this.box.n, gl.UNSIGNED_SHORT, 0, this.furn.length);

    /* 蜂王面向最近在干活的工位；没有就面向工位线（+x） */
    if (this.queen) {
      const active = this.model && this.model.lanes.find((l) => l.active);
      let target = Math.PI / 2;
      if (active) {
        const cd = this.cells3d.find((cc) => cc.kind === "stage" && cc.stageIdx === active.idx);
        if (cd) target = Math.atan2(cd.bx - this.queen.x, cd.bz - this.queen.z);
      }
      const dq = target - this.queen.rot;
      this.queen.rot += (dq - Math.round(dq / (Math.PI * 2)) * Math.PI * 2) * 0.06;
      this.uploadBees(this.specBuf, [this.queen]);
    }
    /* 巡逻蜂：每帧算位置（独立缓冲） */
    const pp = this.patrolPos(time);
    this.patrol.x = pp.x; this.patrol.y = pp.y; this.patrol.z = pp.z; this.patrol.rot = pp.rot;
    /* 拖尾：隔帧采样，约 0.55s 寿命（60fps 下 26 点） */
    this._trailN = (this._trailN || 0) + 1;
    if (!this.reduced && this._trailN % 2 === 0) {
      this._trail = [{ x: pp.x, y: pp.y, z: pp.z }].concat(this._trail || []).slice(0, 26);
    }
    if (!this.reduced) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.patrolBuf);
      const pd = new Float32Array(12);
      pd[0] = pp.x; pd[1] = pp.y; pd[2] = pp.z; pd[3] = pp.rot;
      pd[4] = this.patrol.scale; pd[5] = 0; pd[6] = this.patrol.wing; pd[7] = this.patrol.phase;
      pd[8] = this.patrol.rgb[0]; pd[9] = this.patrol.rgb[1]; pd[10] = this.patrol.rgb[2]; pd[11] = this.patrol.glow;
      gl.bufferData(gl.ARRAY_BUFFER, pd, gl.DYNAMIC_DRAW);
    }

    /* 蜜蜂：实例属性是 VAO 状态——每次绘制前重绑实例缓冲指针 */
    const bindBeeInst = (buf) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      for (let k = 0; k < 3; k++) {
        gl.enableVertexAttribArray(3 + k);
        gl.vertexAttribPointer(3 + k, 4, gl.FLOAT, false, 48, k * 16);
        gl.vertexAttribDivisor(3 + k, 1);
      }
    };
    const drawBee = (vao, buf, n, wingPass) => {
      gl.useProgram(this.progBee);
      gl.uniformMatrix4fv(this.uBee.vp, false, vp);
      gl.uniform1f(this.uBee.time, time);
      gl.uniform3fv(this.uBee.eye, eye);
      gl.uniform1f(this.uBee.wing, wingPass ? 1 : 0);
      gl.bindVertexArray(vao);
      bindBeeInst(buf);
      gl.drawElementsInstanced(gl.TRIANGLES, wingPass ? this.beeW.nWing : this.beeW.nBody,
        gl.UNSIGNED_SHORT, 0, n);
    };
    const wingsOn = () => {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
    };
    const wingsOff = () => {
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    };
    if (this.bees.length) {
      if (this.spawning || !this.reduced) this.uploadBees(this.beeBuf, this.bees);
      drawBee(this.beeW.body, this.beeBuf, this.bees.length, false);
      wingsOn(); drawBee(this.beeW.wing, this.beeBuf, this.bees.length, true); wingsOff();
    }
    if (this.queen) {
      drawBee(this.beeQ.body, this.specBuf, 1, false);
      wingsOn(); drawBee(this.beeQ.wing, this.specBuf, 1, true); wingsOff();
    }
    if (!this.reduced) {
      /* 巡逻蜂用工蜂网格（无皇冠） */
      drawBee(this.beeW.body, this.patrolBuf, 1, false);
      wingsOn(); drawBee(this.beeW.wing, this.patrolBuf, 1, true); wingsOff();
    }

    /* 环境浮尘 + 巡逻蜂拖尾 */
    const pts = [];
    if (!this.reduced) {
      const n = 22;
      for (let i = 0; i < n; i++) {
        const seed = i * 137.508;
        const xx = b.x0 + ((seed * 7.31) % (b.x1 - b.x0));
        const zz = -1.5 + ((seed * 3.77) % 3.4);
        const yy = 0.5 + ((time * (0.10 + (i % 4) * 0.04) + (i * 0.37)) % 1) * 1.9;
        pts.push(xx + Math.sin(time * 0.7 + seed) * 0.2, yy, zz,
          this.theme.accent[0], this.theme.accent[1], this.theme.accent[2], 0.18, 0.06);
      }
      this._trail.forEach((tp, i2) => {
        const f = 1 - i2 / 26;
        pts.push(tp.x, tp.y, tp.z, 1.0, 0.80, 0.32, f * 0.85, 0.09 + f * 0.10);
      });
    }
    if (pts.length) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.depthMask(false);
      gl.useProgram(this.progPoint);
      gl.uniformMatrix4fv(this.uPoint.vp, false, vp);
      gl.uniform1f(this.uPoint.px, this.canvas.height / (2 * Math.tan(21 * Math.PI / 180)));
      gl.bindVertexArray(this.vaoPoint);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.ptBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pts), gl.DYNAMIC_DRAW);
      gl.drawArrays(gl.POINTS, 0, pts.length / 8);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    }
    gl.bindVertexArray(null);
    this.updateOverlay(vp, now);
  };

  HiveScene.prototype.info = function () {
    return {
      glOk: !!this.gl, frames: this._frames || 0, cells: this.cells3d.length,
      lanes: this.model ? this.model.lanes.length : 0, bees: this.bees.length,
      yaw: this.cam.yaw, pitch: this.cam.pitch, dist: this.cam.dist,
      cssW: this.cssW, cssH: this.cssH,
    };
  };

  /* 供无头探针把工位投到屏幕坐标（模拟悬停/点击用） */
  HiveScene.prototype.projectCell = function (rel) {
    const cd = this.cells3d.find((c) => c.rel === rel);
    if (!cd || !this._vp) return null;
    const p = this.project(cd.bx, cd.by - 0.3, cd.bz, this._vp);
    return p ? { x: p.x, y: p.y, w: p.w } : null;
  };

  HiveScene.prototype.dispose = function () {
    this.setActive(false);
    try { this._ro.disconnect(); } catch (e) {}
    try { this._themeObs.disconnect(); } catch (e) {}
    try { document.removeEventListener("visibilitychange", this._onVis); } catch (e) {}
    try { if (this._mq && this._onMq) this._mq.removeEventListener("change", this._onMq); } catch (e) {}
    if (window.__hive3d === this) delete window.__hive3d;
  };

  return { create(opts) { try { return new HiveScene(opts); } catch (e) { try { console.warn("hive3d init failed:", e && e.message); } catch (e2) {} return null; } } };
})();
