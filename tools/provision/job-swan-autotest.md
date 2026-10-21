# swan-autotest Jenkins 任务定义(.167 平台机备料,Linux v2,2026-09-21)

> 用途:v3 双机拓扑(167=平台机跑 master+执行,176=构建机)下重建 Jenkins 测试执行任务
> swan-autotest。Windows 版定义见 git 历史(20c85a7),本版按 Linux 口径重写。
> 前置:部署计划 v3 §6 完成(Jenkins master 在 167,HTTP_PORT=8082,JENKINS_USER=autotest,
> git 插件装好,.env.local 的 JENKINS_* 五键已更新);§4 .venv 就绪;§8 adb+plugdev 就绪。
> 本定义按平台触发侧代码契约重建(dashboard/jenkins_client.py + server.py try_jenkins_dispatch)。
> **执行在 master(167)本地:本任务不加节点标签/禁止钉 build-176**(176 不装 adb、不接手机)。
> 任务结果以平台 pipeline_jobs/runs 为准,构建号只是派发回执。

## 1. 触发契约(平台发什么,逐键对齐)

| 项 | 值 | 出处 |
|---|---|---|
| 目标 job 名 | `swan-autotest`(平台 env `JENKINS_TEST_JOB` 可覆盖,**严禁配成打包 job**——防环) | server.py:137 |
| 请求 | `POST {JENKINS_BASE_URL}/job/swan-autotest/buildWithParameters` | jenkins_client.py trigger() |
| 编码 | form 表单(`application/x-www-form-urlencoded; charset=utf-8`) | 同上 |
| 参数 | **仅 `JOB_ID` 一个键**,值 = pipeline_jobs.job_id 原值 | server.py:141 |
| 鉴权 | Basic(`JENKINS_USER:JENKINS_API_TOKEN`,API token 免 crumb) | jenkins_client.py _auth_header |
| JENKINS_BASE_URL | `http://127.0.0.1:8082`(167 本机,master 同机;PUBLIC_URL=http://192.168.12.167:8082 供页面跳转) | .env.local |
| 成功判据 | HTTP 201 + 响应头 `Location: .../queue/item/<id>/` | trigger() |
| 派发后 | 平台轮询队列 3 次 x 1s 取 build 号,回填任务行 `jenkins_job`/`jenkins_build` | server.py try_jenkins_dispatch |
| 失败行为 | 201 前任何失败 → 任务留 pending(result_json.jenkins_trigger_failed),本地兜底通道轮询执行 | server.py:135 |
| 已知残留风险 | 201 之后平台进程崩溃 → 任务永远 pending(轮询通道按设计跳过 Jenkins 派发的任务),需人工重触发 | 流水线执行档案 规则14 |

因此本任务必须满足:**参数化,且唯一参数名为 `JOB_ID`**;构建体只做定向认领执行,不回调任何触发。

> 为什么 JOB_ID 必须显式定义:平台只传 JOB_ID。若 job 未参数化,Shell 里 `$JOB_ID` 展开为空,
> agent `--job-id ""` 会退回轮询分支,误领任意 pending 任务——契约被静默破坏。

## 2. 环境变量契约

构建步骤**不需要手工 export 任何环境变量**:

- agent 启动即自读 `/opt/uiautotest/.env.local`(_load_dotenv_local,已存在的外层环境变量优先):
  SWAN_PHONE(测试侧)、JENKINS_*(平台侧)、GITLAB_BOT 等;
- pytest 子进程环境由 agent 统一注入:PLATFORM / DASHBOARD_URL / DASHBOARD_RUN_ID /
  PREFLIGHT=1 / EVIDENCE=1 / DEVICE_POOL=1 / SWAN_APK_ID(任务指定包体时);
- DASHBOARD_URL 缺省已是 `http://192.168.12.167:8080`(2026-09-21 随 Linux v2 改,原 166),
  agent 与 pytest 同机直连,无需设置。(可选:Shell 里 export DASHBOARD_URL=http://127.0.0.1:8080
  走本机回环,非必需。)
- Python 一律 `/opt/uiautotest/.venv/bin/python`(Ubuntu 24.04 PEP 668 锁系统 pip,venv 显式
  路径根治"依赖装进别的用户 site 看不见"类坑)。

服务运行依赖核对见 device-pool.md;agent 依赖清单同。

## 3. 建任务(createItem)

任务定义仓库文件:`tools/provision/job-swan-autotest.xml`。
在 167 上执行(ssh autotest@192.168.12.167):

```bash
TOKEN=$(grep '^JENKINS_API_TOKEN=' /opt/uiautotest/.env.local | cut -d= -f2)
curl -s -u "admin:$TOKEN" -X POST -H "Content-Type: application/xml" \
  --data-binary "@/opt/uiautotest/tools/provision/job-swan-autotest.xml" \
  "http://127.0.0.1:8082/createItem?name=swan-autotest"
# 回显为空即成功(HTTP 200);重名会 400
curl -s -o /dev/null -w '%{http_code}\n' -u "admin:$TOKEN" \
  "http://127.0.0.1:8082/job/swan-autotest/api/json"   # 200
```

与 swan-build-android 的差异:
- `JOB_ID` 一个字符串参数(默认空);
- **执行并发构建 = 允许**(concurrentBuild=true),多个冒烟触发可并行入构建;
- **不加节点标签**——构建落在 master(167)本地;钉到 build-176 会在无 adb 的构建机上空跑失败;
- 无 SCM(NullSCM),Shell `cd /opt/uiautotest` 落仓根,依赖该目录的 .env.local 与 scripts。

并发说明:单手机环境下两个构建并行时,后到的 pytest 会因设备池无空闲走 env_blocked
(环境问题按 bug 处理,不计用例失败)。若不接受这种噪音,把 concurrentBuild 改为 false
让构建在 Jenkins 排队串行——已拍板维持 true(部署计划 §8)。

## 4. 任务自检(不占设备、不产生任务/run)

Build with Parameters 手工触发,`JOB_ID` 填不存在的值(如 `selftest-20260921`):

- 期望:构建**绿**,控制台出现
  `[pipeline-agent ...] job selftest-20260921 不可认领(不存在/已被认领/非 pending),按空跑退出`;
- 不产生任何 pipeline_jobs 行、不跑 pytest、不碰 adb。

构建红常见原因:① `/opt/uiautotest/.venv/bin/python` 不存在 → v3 §4 没做;
② ModuleNotFoundError → venv 依赖漏装(`.venv/bin/pip install -r requirements.txt`);
③ 仓路径不对(布局约定 `/opt/uiautotest`,两机一致)。

## 5. 防环三闸门(重建后逐条确认)

1. 平台 `.env.local` 的 `JENKINS_TEST_JOB` 只允许指测试 job(缺省即 swan-autotest,不设也对;
   **绝不能设成 swan-build-android**);
2. 本构建体只跑 `pipeline_agent --job-id`,不回调 trigger / trigger_smoke(防环由结构保证);
3. 打包→冒烟联动(M5)若日后启用:swan-build-android(176)批处理在打包成功后调
   `.venv/bin/python scripts/trigger_smoke.py --suite SMKE --apk-id <apk_id> || echo "[WARN] smoke trigger failed"`
   (失败只告警不影响构建结果;176 侧 .env.local 的 DASHBOARD_URL 指向 167)。
   该改造属 swan-build-android job(打包模块所有),是否启用由主会话拍板;联动语义见
   scripts/trigger_smoke.py 文档头与流水线执行档案规则 16。

## 6. 平台侧收尾核对

- dashboard(systemd unit uiautotest-dashboard.service)就位后,`curl -s http://127.0.0.1:8080/api/apks`
  有数据(=DB 恢复)或空列表(=待重灌),两种都算通过(v3 §0 分界);
- 用一条真实任务实测派发链路:见 pipeline-verify.md 第 5-6 步;
- 任务行能看到 jenkins_build 号,页面 Jenkins 日志按钮可跳转(JENKINS_PUBLIC_URL 生效)。
