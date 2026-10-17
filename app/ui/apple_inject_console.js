// CodeBee Apple 皮肤热注入 - 在浏览器控制台执行
// 1. F12 打开控制台
// 2. 粘贴下面代码，回车
// 3. 立即看到 Apple 风格效果（刷新后失效）

(function() {
  // 切换到 apple 皮肤
  document.documentElement.dataset.skin = 'apple';
  localStorage.setItem('orch.skin', 'apple');

  // 移除旧注入
  const old = document.getElementById('apple-inject');
  if (old) old.remove();

  // 注入 Apple 皮肤 CSS
  const style = document.createElement('style');
  style.id = 'apple-inject';
  style.textContent = `
    /* Apple 皮肤配色 */
    html[data-skin="apple"] {
      --bg: #f2f2f7;
      --sidebar: rgba(255, 255, 255, 0.72);
      --panel: rgba(255, 255, 255, 0.85);
      --panel2: #e5e5ea;
      --border: rgba(60, 60, 67, 0.12);
      --border-strong: rgba(60, 60, 67, 0.22);
      --text: #1c1c1e;
      --muted: #6e6e73;
      --accent: #007aff;
      --accent2: #5856d6;
      --ok: #34c759;
      --warn: #ff9f0a;
      --bad: #ff3b30;
    }

    /* 毛玻璃顶栏 */
    html[data-skin="apple"] .topbar {
      background: color-mix(in srgb, var(--bg) 72%, transparent) !important;
      backdrop-filter: blur(20px) saturate(180%) !important;
      -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
      border-bottom: 0.5px solid color-mix(in srgb, var(--border) 60%, transparent) !important;
    }

    /* 毛玻璃侧栏 */
    html[data-skin="apple"] #sidebar {
      background: var(--sidebar) !important;
      backdrop-filter: blur(20px) saturate(180%) !important;
      -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
      border-right: 0.5px solid color-mix(in srgb, var(--border) 50%, transparent) !important;
    }

    /* 卡片阴影纵深 */
    html[data-skin="apple"] .panel {
      border: none !important;
      border-radius: 16px !important;
      box-shadow: 0 0.5px 1px rgba(0,0,0,0.03), 0 2px 6px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.04) !important;
    }
    html[data-skin="apple"] main .panel.wide { box-shadow: none !important; }

    /* 大圆角按钮 */
    html[data-skin="apple"] button.primary,
    html[data-skin="apple"] button.ghost { border-radius: 12px !important; }

    /* 弹性过渡 */
    html[data-skin="apple"] button,
    html[data-skin="apple"] .pill,
    html[data-skin="apple"] .stask,
    html[data-skin="apple"] .item,
    html[data-skin="apple"] .sdir summary {
      transition-timing-function: cubic-bezier(0.25, 0.46, 0.45, 0.94) !important;
      transition-duration: 0.2s !important;
    }

    /* 列表项阴影 */
    html[data-skin="apple"] .item {
      border: none !important;
      border-radius: 14px !important;
      box-shadow: 0 0.5px 1px rgba(0,0,0,0.03), 0 1px 4px rgba(0,0,0,0.04) !important;
    }

    /* 排版柔化 */
    html[data-skin="apple"] h2,
    html[data-skin="apple"] .page-title,
    html[data-skin="apple"] .brand-name {
      font-weight: 600 !important;
      letter-spacing: -0.01em !important;
    }

    /* 项目树 Apple 式展开动画 */
    html[data-skin="apple"] .sdir .chev {
      transition: transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94) !important;
    }
    html[data-skin="apple"] .sdir > .dirbody {
      display: grid;
      grid-template-rows: 0fr;
      transition: grid-template-rows 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94) !important;
    }
    html[data-skin="apple"] .sdir[open] > .dirbody {
      grid-template-rows: 1fr;
    }
    html[data-skin="apple"] .sdir > .dirbody > * {
      overflow: hidden;
      opacity: 0;
      transform: translateY(-4px);
      transition: opacity 0.2s ease, transform 0.2s ease;
    }
    html[data-skin="apple"] .sdir[open] > .dirbody > * {
      opacity: 1;
      transform: translateY(0);
    }
    html[data-skin="apple"] .sdir[open] > .dirbody > *:nth-child(1) { transition-delay: 0.05s; }
    html[data-skin="apple"] .sdir[open] > .dirbody > *:nth-child(2) { transition-delay: 0.1s; }
    html[data-skin="apple"] .sdir[open] > .dirbody > *:nth-child(3) { transition-delay: 0.15s; }
    html[data-skin="apple"] .sdir[open] > .dirbody > *:nth-child(4) { transition-delay: 0.2s; }
    html[data-skin="apple"] .sdir[open] > .dirbody > *:nth-child(5) { transition-delay: 0.25s; }

    /* 字号优化 */
    html[data-skin="apple"] h2 { font-size: 18px !important; }
    html[data-skin="apple"] .hero { font-size: 26px !important; }
    html[data-skin="apple"] .page-title { font-size: 17px !important; }
    html[data-skin="apple"] .side-label { font-size: 12px !important; }
    html[data-skin="apple"] .sdir summary .cnt { font-size: 11px !important; }
    html[data-skin="apple"] .stask .tm { font-size: 11.5px !important; }
  `;
  document.head.appendChild(style);

  console.log('%c✓ Apple 皮肤已注入', 'color: #007aff; font-weight: bold; font-size: 14px;');
  console.log('当前皮肤:', document.documentElement.dataset.skin);
  console.log('当前主题:', document.documentElement.dataset.theme);
  console.log('点击项目树箭头看展开动画');
})();
