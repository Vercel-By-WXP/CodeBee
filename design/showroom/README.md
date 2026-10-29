# design/showroom —— 设计参考展厅（非产品）

> ⚠️ **这不是运行中的 CodeBee 产品，也不连接任何后端。**
> 它是一套**纯静态 HTML/CSS 视觉稿**，只在定视觉语言阶段用，页面里的榜单/竞品/作品数据都是写死的样例。

## 它是什么
- `index.html`：一个 iframe 预览壳，左侧导航切换 `pages/*.html` 六页（欢迎/执行台/创作台/成稿台/分析台/设置）。
- `tokens.css` / `base.css` / `components.css`：设计系统三件套（中性灰底 + 蓝紫渐变 + 1px 描边 + 圆角 8/12/16）。
- `pages/*.html`：六页静态稿，内容写死，无 `fetch`、无 `/api` 调用。

## 真产品在哪
- 真实、连后端的页面在 **`app/ui/`**（`welcome/` `novel/` `dossier/` `radar/` `hive/`），各自 `*.js` 调 `/api/*` 拉真实数据。
- 真实的设计系统资产（与展厅同款 CSS）在 **`app/ui/v2/`** 三件套，由 `app/ui/theme-v2.css` 桥接加载。
- 启动真产品：`python app/main.py`（或 `--profile novel|dossier|hive`），默认 `GET /` 进欢迎页选形态。

## 历史
改版走「demo 先行」：先在这里定视觉语言，再照同一套 CSS 写真页面。真页面已落地后，本目录只作视觉存档/参考，**不要再把它当成产品去验收**。本地预览端口曾用 `18802`；该静态服务应与真实预览（`18803` 起的应用）区分清楚。
