# OTP Gateway

这是给自动化账号使用的验证码平台。配置开发提供的验证码查询接口后，自动化工程读取手机号验证码时，平台会主动向开发接口请求最新验证码，并写入网页历史记录。

## 启动

```powershell
$env:OTP_INGEST_TOKEN = "可选：开启写入鉴权时设置"
$env:OTP_READ_TOKEN = "替换为自动化读取密钥"
$env:OTP_ALLOWED_PHONES = "13777777777,13888888888"
$env:OTP_READ_AUTH_REQUIRED = "false"
$env:OTP_INGEST_AUTH_REQUIRED = "false"
$env:CAPTCHA_SOURCE_URL = "http://<开发测试服地址>/api/user/captcha/mockCode"
$env:CAPTCHA_SOURCE_TIMEOUT_SECONDS = "5"
python -m scripts.otp_gateway.server --host 0.0.0.0 --port 18080
```

建议在 Linux 虚拟机前使用 Nginx 提供 HTTPS，并通过环境变量或密钥管理服务注入 Token。数据库默认位于 `output/otp_gateway/otp.sqlite3`，可以使用 `OTP_DB` 修改。
正式环境建议设置 `OTP_ALLOWED_PHONES`，用逗号分隔，只允许授权的自动化手机号写入验证码。
设置 `CAPTCHA_SOURCE_URL` 后，平台会请求 `GET <CAPTCHA_SOURCE_URL>?tel=<手机号>`。当前已配置为 `https://testnetwork.hivi.com/api/user/captcha/mockCode`。支持开发接口直接返回验证码文本，以及 `{"mockCode":"123456"}` 或嵌套在 `data` / `result` 内的 JSON；若返回 `success:false`，平台会将开发接口的 `message` 透传为 `502` 原因。

systemd 模板位于 `swan-otp-gateway.service`。建议部署到 Swan 专属路径 `/opt/swan-otp-gateway/app`，将环境文件放在 `/etc/swan-otp-gateway/env`，数据放在 `/var/lib/swan-otp-gateway`，服务名使用 `swan-otp-gateway`。

## 主动获取验证码

```bash
curl 'http://<开发测试服地址>/api/user/captcha/mockCode?tel=13777777777'
```

开发提供该接口后，不再需要向验证码平台推送验证码。平台由自动化读取动作触发调用开发接口。

网页（`/`）顶部提供手动取码：输入手机号点「取码」，平台立即请求开发接口拉取该号码当前验证码并入库上屏。注意验证码有时效，过期/已使用后源接口会拒绝（报"验证码错误"，实为无有效码），需在发码后的有效窗口内取。

## 手动全部刷新

`POST /api/test/otp/refresh`：服务端同步对白名单逐号实时取码入库，返回逐号结果（`inserted`/`refreshed`/`unchanged`/`no_valid_code`/`error`）。网页「全部刷新」按钮即调此接口。

## 记录保留期

记录只保留 `OTP_RETENTION_DAYS` 天（默认 5，`0`=不清理）：服务启动时清理一次，之后每小时最多清理一次。清理日志形如 `OTP retention: purged N record(s) older than 5d`。

## 读取验证码

按手机号查询时，平台会主动请求开发接口获取该手机号验证码，再返回最新一条未过期且未消费的验证码。内网默认免鉴权：

```bash
curl 'https://otp.example.test/api/test/otp/latest?phone=13777777777'
```

没有可用验证码时返回 `404`。成功响应包含完整 `phone`、`id`、`code`、`expireAt` 等字段。

网页列表接口保留并返回最近记录（包括有效、过期和已消费状态），不会自动删除历史记录：

```bash
curl 'https://otp.example.test/api/test/otp/recent?limit=100'
```

浏览器打开服务根地址即可查看动态页面，页面每两秒自动刷新：

```text
http://192.168.12.167:18080/
```

## 消费验证码

```bash
curl -X POST 'https://otp.example.test/api/test/otp/consume' \\
  -H 'Content-Type: application/json' \\
  -d '{"id":1}'
```

验证码五分钟后变为过期状态，消费后变为已消费状态，但记录不会自动删除。默认所有 API 和网页对内网开放，不需要 `Authorization`。如需启用写入鉴权，将 `OTP_INGEST_AUTH_REQUIRED` 设为 `true` 并配置 `OTP_INGEST_TOKEN`；如需启用读取/消费鉴权，将 `OTP_READ_AUTH_REQUIRED` 设为 `true` 并配置 `OTP_READ_TOKEN`。正式环境建议配置 HTTPS、IP 白名单和访问审计。
