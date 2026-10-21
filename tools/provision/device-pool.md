# 设备池恢复清单(.167 平台机备料,Linux v2,2026-09-21)

> 用途:v3 双机拓扑下恢复 167 的设备池与执行链路运行条件,不允许现场摸索。
> Windows 版清单见 git 历史(20c85a7);**仅 167**——176 构建机不装 adb、不接手机(部署计划 §5)。
> 前置:v3 §1-§4 完成(系统基线/SSH/Git 仓 /opt/uiautotest/.venv);
> dashboard unit uiautotest-dashboard.service 已起(8080 监听,v3 §7)。
> 设备事实:荣耀 JDY-AN00,serial **AKFDCP4718425500**;手机 app 登录态 13555555555
> 存在手机本地,不受换机影响;受保护账号 18946151218 **永远不碰、不登出**。
> DB 恢复则 devices 表历史行直接复用;本清单按"表空了怎么重建"写(空库重建情形,v3 §0)。

## 1. adb 落位与设备权限(udev/plugdev)

Linux 无 Windows 驱动问题:驱动章节整体作废,只剩 **udev 规则 + 组权限** 两件事。
conftest 设备注册/领取(`qa_platform/device_pool.py`)以 `["adb","devices","-l"]` 裸命令调 adb,
只认 PATH(apt 装的 adb 在 /usr/bin,天然在 PATH)。

```bash
sudo apt install adb android-sdk-platform-tools-common
# android-sdk-platform-tools-common 自带 /lib/udev/rules.d/51-android.rules,含华为 vendor 12d1
# (荣耀 JDY-AN00 即华为系)——无需手写规则
sudo usermod -aG plugdev autotest    # 规则按 plugdev 组放行;重新登录后生效
sudo udevadm control --reload-rules && sudo udevadm trigger   # 或直接重插 USB
adb version && adb devices -l        # autotest 身份验证
```

排错速查:
- `adb devices` 显示 **no permissions** → plugdev 组未生效(重新登录)或 udev 规则缺失;
- 列表**空** → `dmesg | tail` 看 USB 枚举,换线/换口;
- 显示 **unauthorized** → 手机 RSA 弹窗没点(见 §2);
- 显示 **offline** → 换线/重启手机。

## 2. adb server 归属与 RSA 授权(新口径)

旧 Windows 的 SYSTEM/admin 会话分裂问题在 Linux 下**天然消失**:dashboard 服务、Jenkins、
交互 ssh 全部以 **autotest 同一用户**运行(v3 §6 JENKINS_USER=autotest)→ 同一个 adb
server(5037),不存在跨账户 server 抢占。

- **归属保障**:建议 dashboard unit 加一行 `ExecStartPost=/usr/bin/adb start-server`
  (v3 §8 口径:dashboard 开机先起 server,密钥落 `~autotest/.android/adbkey`)。
  若 Jenkins 先起 server(其 HOME=/var/lib/jenkins),密钥落错家——功能不受损(同用户同
  server 共用),但归属口径乱,按 unit 顺序规避。
- **RSA 授权**:首次连接手机屏上弹"允许 USB 调试",勾**一律允许**再确定——授权发生在
  手机侧,与电脑无桌面无关;误点拒绝 → 手机端撤销 USB 调试授权 → 重插重来。
- **跨身份验证**(替代旧 Windows schtasks 一次性任务法):常规无需(全同用户);
  root ssh 下想模拟 autotest 视角:`sudo -u autotest adb devices -l`。

## 3. 手机侧设置(与 Windows 版一致,重装后手机不用动,查一遍即可)

1. 开发者选项:设置 → 关于手机 → 版本号连点 7 次解锁;
2. **USB 调试**开;
3. **"仅充电模式下允许 ADB 调试"开**(荣耀特有开关,必开);
4. "充电时屏幕不休眠"(保持唤醒)开——无人值守 nightly 必需;
5. 首次连接 RSA 弹窗:勾"一律允许"再确定。

纪律:自动化登录态是 13555555555(OTP 白名单内测试账号);**任何登出/注销操作禁止**
(手机上另有受保护的 18946151218,自动化永不触达)。

## 4. devices 表注册(表空重建 / DB 恢复时的核对)

**正常路径零手工**:conftest 每次会话开始自动调 `register_local_devices()` 把 adb 在线
设备全部注册进平台池——首次冒烟即自愈,什么都不用做。

手动重建(空库情形,或想让平台页提前看到设备):POST `/api/devices/register`,payload 与
代码逐键一致:

```bash
SERIAL=AKFDCP4718425500
MODEL=JDY-AN00          # 以 adb devices -l 的 model: 字段为准
HOST=$(hostname)        # 代码已加 HOSTNAME/socket.gethostname 兜底(COMPUTERNAME 是 Windows 变量)
curl -s -X POST http://127.0.0.1:8080/api/devices/register \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d "{\"device_id\":\"$SERIAL\",\"name\":\"$MODEL\",\"platform\":\"flutter\",\"model\":\"$MODEL\",\"metadata\":{\"host\":\"$HOST\"}}"
```

核对:`curl -s http://127.0.0.1:8080/api/devices` 的 `items` 里该设备
`status=online_idle`、`platform=flutter`、`metadata.host`=167 主机名。

幂等性(重复执行安全):offline → 复活为 online_idle 并清占用;busy/disabled 设备
状态与占用**原样不动**,不会误伤执行中任务。

数据口径:
- DB 恢复 → 旧设备行还在,serial 相同直接复用,last_assigned_at 历史保留(公平分配延续);
- 空库 → 按上法重建,公平分配"最久未用优先"从头计时(单设备无感)。

## 5. 领取机制自检(acquire/release 往返)

conftest 启用设备池的四个条件(conftest.py:115-121,缺一即静默不启用):

```
PLATFORM ∈ {flutter, android}  且  DASHBOARD_URL 非空
且  DEVICE_POOL 不在 {0,false,off}  且  ANDROID_DEVICE_SERIAL 未设置
```

agent 执行链自动满足全部四个;手动跑 pytest 前先自查这四个环境变量。

API 往返演练(不跑 pytest,验证平台侧调度语义):

```bash
RUN=pool-verify-$(date +%Y%m%d%H%M%S)
DEV=$(curl -s -X POST http://127.0.0.1:8080/api/devices/acquire \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d "{\"run_id\":\"$RUN\",\"platform\":\"flutter\",\"prefer\":[]}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["device_id"])')
echo "$DEV"    # 期望 serial;平台页该设备转 busy(current_run=$RUN)
curl -s -X POST "http://127.0.0.1:8080/api/devices/$DEV/release" \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d "{\"run_id\":\"$RUN\"}"
# 再 GET /api/devices:回 online_idle,current_run 清空,last_assigned_at 已刷新
```

- acquire 返回 **503** = 无空闲设备(全部 busy/offline/disabled)→ conftest 里
  `pytest.exit(3)` → 任务判 env_blocked(环境问题按 bug 处理,不计用例失败)。
- 语义速记:busy→idle 拒绝(占用保护);busy→offline/disabled 允许(拔线自愈/人工下线,
  清占用);busy 超 4h 且无 idle 可领时平台自愈回收;release 前 adb 复查,失联设备置
  offline,重插后 register 复活。另有 running 任务 1h 超时兜底(RUNNING_TIMEOUT_HOURS,
  server 端),与设备 4h 自愈相互独立。

## 6. pipeline_agent 依赖清单(autotest 视角)

| 依赖 | 要求 | 验证 |
|---|---|---|
| 代码仓 | /opt/uiautotest(v3 §3 克隆) | 目录存在 |
| Python | /opt/uiautotest/.venv(PEP 668 一律 venv;systemd/Jenkins/交互统一此路径,不用系统 python3 跑依赖代码) | `.venv/bin/python -c "import pytest,cv2,paddleocr,yaml"` |
| agent 本体 | 零三方依赖(urllib),但跑 pytest 走同一 venv | `.venv/bin/python scripts/pipeline_agent.py --help` |
| .env.local | /opt/uiautotest/.env.local(SWAN_PHONE=13555555555 等测试侧键;agent 自读) | 文件存在 |
| adb | apt 版,/usr/bin(SYSTEM/PATH 分裂问题已消失) | 本文 §1 |
| 日志目录 | output/logs 可写(agent 把 pytest 输出重定向到 output/logs/pipeline_pipe-<job_id>.log) | 冒烟后文件生成,属主 autotest |
| pytest cache | 仓根 .pytest_cache 可写(--lf 失败重跑依赖) | 冒烟后目录生成 |
| PaddleOCR 模型 | 缓存按用户目录(~autotest 下);首跑会现下载(慢非卡) | 冒烟日志无下载超时 |

**167 不放常驻轮询 PipelineAgent**:Jenkins 定向通道(--job-id)是 167 的执行方式。
轮询兜底 agent 仍在 1BG-IT-01796 本机(Windows 计划任务),其 DASHBOARD_URL 需改指
`http://192.168.12.167:8080` 才能兜到新平台——本机配置不属 167 备料,是否保留兜底由主会话定。

## 7. nightly 载体(v3 定为 systemd timer,仅 167)

两份 unit 文件(主会话放置;`--legacy-trigger` **必须带**——新版脚本缺省动作是 bootstrap
建配置,不是直触发):

`/etc/systemd/system/nightly-pipeline-trigger.service`:

```ini
[Unit]
Description=Swan nightly pipeline trigger (legacy channel)
After=network-online.target

[Service]
Type=oneshot
User=autotest
WorkingDirectory=/opt/uiautotest
ExecStart=/opt/uiautotest/.venv/bin/python scripts/nightly_trigger.py --legacy-trigger
```

`/etc/systemd/system/nightly-pipeline-trigger.timer`:

```ini
[Unit]
Description=Swan nightly trigger at 01:33 daily

[Timer]
OnCalendar=*-*-* 01:33:00

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nightly-pipeline-trigger.timer
systemctl list-timers nightly-pipeline-trigger.timer   # NEXT 列应显示明日 01:33
```

M5 调度器切换(pipeline_scheduler.py + bootstrap/--enable)待用户确认,不在本次范围;
切换时本 timer disable 即回滚。
