# Codebee 手机连接服务

此目录只负责 `app.codebeeagent.com`，不部署根域名官网。Cloudflare Worker
和 Durable Objects 提供配对、设备隔离、HTTP/SSE 中转；电脑端的
`app/core/phone_relay.py` 主动建立 TLS WebSocket，不需要用户自己的
Cloudflare 账号、域名或入站端口。

## 使用

1. 在包含此功能的 Codebee 中打开「手机连接」，点击「生成配对码」。
2. 手机扫描二维码，点击「连接我的电脑」。也可打开
   `https://app.codebeeagent.com`，粘贴连接码或配对链接。
3. 手机进入自己的电脑工作台；电脑必须开机联网并保持 Codebee 运行。
4. 本机「撤销全部手机」清空配对会话；「关闭远程连接」撤销并禁用连接。

二维码凭据在 URL fragment 中，前端读取后从地址栏移除；随机 128 位配对码
5 分钟有效且仅能消费一次。手机使用 Secure/HttpOnly/SameSite=Strict cookie，
会话有效期 30 天，每台电脑最多 16 个会话。电脑身份凭据单独保存在用户数据目录
`phone-relay.json`，不与其他用户共享。不要拷贝此文件给其他电脑。

中转服务终止 TLS，能够处理经过服务的任务和文件内容；此版本不是端到端加密。
它不保存任务/文件正文，持久存储仅含配对码散列、会话散列、到期时间和限流计数。
远程端不能调用本机配对管理接口。保持现有设备控制权锁；手机可以控制 Codebee
及其已有的本机 Agent/浏览器能力，此服务不是操作系统全桌面远控。

## 开发与验证

需要 Node.js 22+（部署工具）；用户电脑仍只需要既有 Python 3.8+。

```powershell
cd cloud/phone-relay
npm ci
npm test
npm run dev
```

另一个终端在仓库根目录运行：

```powershell
$env:CODEBEE_RELAY_TEST_URL='http://127.0.0.1:18791'
python -m unittest tests.test_phone_relay tests.test_remote -v
node cloud/phone-relay/tests/browser.mjs
```

测试只使用独立临时数据目录与 18891/18892/18893 端口，不连接真实本机用户数据。
集成检查包括两台电脑隔离、一次性码并发、cookie 设备调包、Origin、控制权冲突、
SSE 流、电脑重启、离线落地页、撤销和注销。浏览器检查在 Windows Edge 中验证
390px 页面、配对跳转、HttpOnly、远程管理拒绝、本机弹窗。

临时连接器测试可设置 `CODEBEE_RELAY_URL=http://127.0.0.1:18791`；正式默认地址
固定为 HTTPS。`--no-phone-relay` 仅停止本次启动的自动恢复，不撤销已配对手机。
远程连接默认关闭，只有本机生成配对码后才启用；下次启动自动恢复。

## 部署

```powershell
npx wrangler login
npx wrangler whoami
npx wrangler deploy --dry-run
npm run deploy
```

`wrangler.jsonc` 限定 `app.codebeeagent.com` 自定义域名，关闭 workers.dev 地址。
请先确认 Cloudflare 中该子域名没有其他服务。首次部署会创建两个 SQLite Durable
Object namespace；Cloudflare 套餐限额与计费按账号实际设置执行。

传输限制：请求体 16 MiB；每台电脑最多 24 个并发请求；响应分块确认防止无界缓冲；
流空闲 120 秒超时。连接器每 20 秒心跳，故障最多间隔 30 秒重试。
服务升级会断开现有 WebSocket，连接器自动重连。无需更新根域名 DNS。

发布桌面版/npm 新版本时应包含 `app/core/phone_relay.py`、`relay_ws.py` 与主程序/UI
修改；部署云服务不会自动升级现有用户电脑上的 Codebee。
