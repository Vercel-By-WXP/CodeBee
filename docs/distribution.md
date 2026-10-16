# CodeBee 分发与官网方案（任务 28-37）

## 已核对的现状

- 根 npm 包 `package.json` 与 `desktop/package.json` 当前均为 `0.1.116`。
- 单一版本源为根目录 `VERSION`；`python scripts/sync_version.py` 检查两者一致，`--sync` 从 VERSION 回写。
- `cloud/phone-relay/package.json` 是独立的 Cloudflare Worker（当前 `1.0.0`），保持独立版本线，不随 CodeBee 桌面/npm 自动升级。
- npm 已有 `bin/tutti.js`，运行时探测 Python 3.8+，不含 `postinstall`，不会在安装时执行网络或写入用户配置；`files` 白名单限制归档内容。
- 桌面端已有 GitHub Release 自更新：先校验 Release manifest 的 SHA-256，再下载对应平台安装包；版本升级不删除用户数据。桌面 About 面板标记“桌面预览版”。
- 桌面 CI 在 `v*` tag 构建 Windows/macOS/Linux；现在同步生成 `SHA256SUMS.txt` 与 `SHA512SUMS.txt`。

## 版本发布闸门

1. 修改 `VERSION`、`CHANGELOG.md`，运行 `python scripts/sync_version.py --sync`。
2. 运行 `python scripts/release_gate.py` 和桌面测试；确认工作树中没有未完成的代理制品。
3. 推送匹配的 `v<版本>` tag。`npm-publish.yml` 校验 tag 与 VERSION 后使用 npm provenance 发布；`desktop-preview.yml` 构建预览 Release 并上传校验和。
4. 发布后核对 `npm view codebee version`、GitHub Release 资产和官网 `/sitemap.xml`。npm/GitHub/Cloudflare 实际可达性在当前环境未代为验证【需确认】。

## 桌面预览、签名和反馈

预览版安装包、下载页和 About 面板都应明确写“预览版”，统一反馈入口为
[GitHub Issues](https://github.com/Vercel-By-WXP/CodeBee/issues)。当前没有匿名使用统计，后续如接入必须在设置中明示同意、默认关闭、提供关闭和删除入口；收集字段、保存位置和保留时间需要发布前确认【需确认】。

Windows 代码签名证书、macOS Developer ID/公证凭据不是仓库内容。没有这些 secret 时，Release 必须保留 SmartScreen/Gatekeeper 提示说明，不能宣称已签名【需确认】。

## 官网 SEO 与内容

Cloudflare Worker 静态资产现在提供 `/robots.txt`、`/sitemap.xml`、`/docs/`、`/compare/`、`/feedback/`。中英文入口页补充了 canonical、description、Open Graph 和包含关键词的 title。首页演示建议使用 30 秒 GIF/视频：目标输入 → CLI 路由 → 跨厂商评审 → 最终报告，放在根官网首页首屏下方；当前 relay 只是手机连接页，演示媒体和真实案例尚未提供【需确认】。

Google Search Console 与 Bing Webmaster Tools 需要站点所有权验证后手工提交 sitemap，不能在仓库内代为完成【需确认】。

## 渠道优先级

| 优先级 | 渠道 | 建议 |
| --- | --- | --- |
| P0 | npm / npx | 已有入口；用 provenance、VERSION 闸门和 README 同步，优先维护。 |
| P0 | GitHub Releases | 现有桌面工作流；保留预览标记、SHA256/SHA512、三平台构建和升级回退。 |
| P1 | Homebrew Cask | macOS 稳定版后提交 tap/cask，使用 Release ZIP 的 sha256；预览版不进入主 cask。 |
| P1 | winget | 稳定版后提交 manifest，填写 installer URL、签名状态、升级规则；未签名预览不建议合并。 |
| P2 | PyPI | 当前项目是标准库应用而非 Python 库；可做薄 CLI wheel，但需决定命令/数据布局，避免与 npm 入口分裂【需确认】。 |
| P2 | Docker | 适合无桌面、沙箱/CI 模式；必须显式挂载数据目录、限制网络和不要暴露生产端口 8765，镜像体积与第三方 CLI 安装策略需评估【需确认】。 |

## README/npm 对齐

npm 归档包含主 README、CHANGELOG、LICENSE、`app` 与 `bin`；发布前以 `npm pack --json` 检查归档，禁止把测试、缓存和用户数据带入包。官网文档页链接回仓库 QUICKSTART，避免维护两份互相漂移的完整手册。
