// CodeBee Apple 皮肤热注入脚本
// 使用方法：在浏览器控制台（F12）粘贴执行，立即看到 Apple 风格效果
// 刷新页面后失效（因为服务器还在缓存旧 CSS）

(function() {
  // 1. 切换到 apple 皮肤（如果 CSS 已加载会自动生效）
  document.documentElement.dataset.skin = 'apple';
  localStorage.setItem('orch.skin', 'apple');

  // 2. 注入 Apple 风格 CSS（绕过服务器缓存）
  const appleCSS = `
    /* Apple 皮肤配色 */
    html[data-skin="apple"] {
      --bg: #000000;
      --sidebar: #1c1c1e;
      --panel: #1c1c1e;
      --panel2: #2c2c2e;
      --border: rgba(255, 255, 255, .08);
      --border-strong: rgba(255, 255, 255, .16);
      --text: #f5f5f7;
      --muted: #98989d;
      --accent: #0a84ff;
      --accent2: #64d2ff;
      --ok: #30d158;
      --warn: #ffd60a;
      --bad: #ff453a;
    }
    html[data-skin="apple"][data-theme="light"] {
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
    html[data-skin="apple"] .item {
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
  `;

  // 移除旧的注入（如果有）
  const old = document.getElementById('apple-hot-inject');
  if (old) old.remove();

  // 注入新样式
  const style = document.createElement('style');
  style.id = 'apple-hot-inject';
  style.textContent = appleCSS;
  document.head.appendChild(style);

  console.log('%c✓ Apple 皮肤已注入', 'color: #007aff; font-weight: bold; font-size: 14px;');
  console.log('当前皮肤:', document.documentElement.dataset.skin);
  console.log('当前主题:', document.documentElement.dataset.theme);
  console.log('刷新页面后失效，需重新执行此脚本');
})();
