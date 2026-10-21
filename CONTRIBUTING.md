# 贡献指南

感谢参与 CodeBee。欢迎提交文档、适配器和功能改进。

## 开发环境

1. 安装 Python 3.8+，克隆仓库并进入目录。
2. 运行时保持纯 Python 标准库；开发工具依赖只用于本地检查。

## 代码风格

- 遵循 PEP 8，使用清晰的命名和小函数。
- 公共接口写简短 docstring，注释解释原因而不是重复代码。
- 子进程使用参数列表，不拼接 shell 字符串。
- 修改前先阅读相邻模块，保持兼容。

## 提交前检查

- 提交前至少运行受影响模块的检查命令和 Python 语法检查。

## PR 流程

1. 从 main 开始修改，保持提交小而聚焦。
2. PR 描述说明问题、实现方式、验证命令和潜在破坏性变更。
3. 关联对应 Issue；没有 Issue 时说明背景。
4. CI 通过后等待维护者评审，按反馈更新。

## 添加 CLI 适配器

1. 从仓库根目录运行 `python app/main.py scaffold-adapter --name "Example CLI" --executable example-cli`。该命令生成 `app/core/adapters/` 下的起始文件和 `tests/test_adapter_<name>.py`；示例参数仍需按供应商文档核实。
2. 在 `catalog.py`/`registry.py` 登记稳定 CLI ID、检测命令、能力和无头调用信息；检查 `manager.py` 对安装检测、绑定与健康状态的处理。
3. 按当前 runner 兼容契约实现 argv/输入通道、超时、退出码和供应商输出解析。命令必须作为参数列表传递，不要把用户输入拼入 shell 字符串；错误映射使用 `error_codes.py` 的稳定错误码。现有 CLI 的 catalog ID、配置与结果字段需保持兼容。
4. 用临时 fake CLI 覆盖成功、非零退出、CLI 缺失、认证失败、超时、混合编码/流式输出和降级；更新 README 的自动编排/仅检测分类，并在 `CLI_COMPATIBILITY.md` 记录真实验收范围。mock 测试不能标记真实 CLI 版本为已验证。
5. 提交前运行相关 unittest、`python scripts/architecture_check.py --changed`、`python tools/static_check.py` 和 `node --check app/ui/app.js`（仅在修改 UI 时）。完整发版需通过 `python scripts/release_gate.py`。

接入脚手架当前是生成式起点，不是可直接注册并运行的统一 adapter protocol；为已有 CLI 加分支时不要改动其他供应商的参数模板或输出语义。

小步提交、清晰描述和可复现检查都很有帮助。欢迎先开 Issue 讨论方向。
