# CodeBee 发布检查清单

- [ ] `VERSION`、根 `package.json`、`desktop/package.json` 一致。
- [ ] CHANGELOG 顶部包含该版本，README 的发布摘要同步。
- [ ] `python scripts/release_gate.py` 通过；`npm pack --dry-run` 未包含测试、缓存、数据或凭据。
- [ ] 桌面 Windows/macOS/Linux 原生构建、smoke、安装后升级测试通过。
- [ ] tag 为 `v<版本>`，与 VERSION 完全匹配。
- [ ] GitHub Release 标注 preview/prerelease 状态，上传 SHA256/SHA512 校验和及构建 manifest。
- [ ] npm provenance 发布后核对 `npm view codebee version` 与 tarball 内容。
- [ ] 签名/公证状态已由发布人确认；未签名时保留 SmartScreen/Gatekeeper 提示【需确认】。
- [ ] 官网 `/robots.txt`、`/sitemap.xml`、`/docs/`、`/compare/`、`/feedback/` 可访问；搜索引擎提交需手工完成【需确认】。
