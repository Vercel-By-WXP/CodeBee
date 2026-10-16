# CLI 能力探测

`app.core.cli_discovery`（可选接入启动流程）维护 CLI 描述表，按可执行文件
扫描 PATH，并以参数列表执行 `--version`/`--help`，记录版本、stdin 和输出格式
等能力。所有探测均有超时且失败只产生诊断，不阻塞服务启动。结果可缓存到
`data/cli_capabilities.json`，Web UI 读取缓存展示“已检测到/未安装及安装提示”。

模型调用记录使用 `app.core.model_family` 的实际返回模型名，并映射到 OpenAI、
Anthropic、Alibaba 等模型族；评审和用量统计应优先使用该字段而非 CLI 名称。
