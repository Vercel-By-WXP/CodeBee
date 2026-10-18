# Our Free Model 预置供应商

CodeBee 启动时会自动创建一个名为 **Our Free Model** 的内置供应商。它连接
`dsh-our-free-model` 的 standalone 本地网关，使用 OpenAI 兼容协议，并在服务可达时
后台获取 `/v1/models`。

## 启动 standalone

CodeBee 启动时会自动查找并启动 standalone。源码目录可以放在 CodeBee 同级的
`dsh-our-free-model` / `dsh-our-free-model-main`，或通过
`OUR_FREE_MODEL_ROOT`（也兼容 `OFM_STANDALONE_ROOT`）指定。

如果需要单独运行或调试，也可以在 `dsh-our-free-model` 源码根目录运行：

```powershell
npm run start:standalone
```

standalone 2.5.1 要求 Node.js `^22.19.0` 或 `>=24.0.0`。

standalone 默认使用 `~/.our-free-model`，需要自定义目录时可以运行：

```powershell
npm run start:standalone -- --data-dir F:\ofm-data --port 18900
```

如果使用了自定义数据目录，CodeBee 进程也要设置同一个 `OFM_HOME`：

```powershell
$env:OFM_HOME = 'F:\ofm-data'
```

如果 CodeBee 使用 `TUTTI_DATA` 运行隔离实例且没有设置 `OFM_HOME`，standalone
数据会自动放在 `<TUTTI_DATA>\our-free-model`，不会与默认用户目录或其他实例混用。

服务会把 API Key 保存到 standalone 数据目录的 `settings.json` 的 `forwardKey` 字段。
CodeBee 只在内存中读取这个 Key，不会把真实值写入自己的 `data/models.json`。

## CodeBee 中的行为

- 首次启动自动创建预置供应商，重复启动不会重复创建。
- standalone 未启动或 Key 不可读时，供应商显示为“未启动”并自动停用，不影响其他供应商。
- standalone 端口从 `settings.json` 的 `standalonePort` 读取；端点强制为本机回环地址。
- 用户可以修改默认模型、难度模型和启停状态；内置端点与 Key 由 standalone 管理。
- 删除内置供应商后，CodeBee 会记住该操作，不会在每次启动时重新创建。
- CodeBee 自己启动的 standalone 会在 CodeBee 退出时自动关闭；如果启动前已经有
  standalone 在运行，CodeBee 会复用它，并在退出时保留该外部实例。
- standalone 启动日志写入其数据目录的 `codebee-standalone.log`。找不到源码、Node
  版本不满足要求或启动失败时，CodeBee 仍会继续启动，供应商会显示对应状态。

standalone 的账号渠道、额度和上游隐私策略仍由其自身项目负责。CodeBee 只托管它
的 Node 子进程生命周期，不接管其数据目录或真实 API Key。
