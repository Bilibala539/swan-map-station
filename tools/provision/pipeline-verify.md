# 流水线/设备池装后验证单(.167 平台机备料,Linux v2,2026-09-21)

> 用途:167 装好后对"设备→领取→执行→证据"全链路逐项验证;对应部署计划 v3 §10
> 第 6 条(`adb devices(167)见设备;平台触发一次冒烟(167 本地执行),证据落库七项核对过`)
> 与第 7 条(nightly timer)的展开。全部通过才宣告设备池与执行链路恢复完成。
> 每步记录通过证据(job_id/build 号/run_id)到部署计划 §10。Windows 版见 git 历史(20c85a7)。
> 执行身份:ssh autotest@192.168.12.167;平台 API 全部走 167 本机 http://127.0.0.1:8080。
> 前置:Jenkins master 就绪+swan-autotest job 已建(job-swan-autotest.md);
> device-pool.md §1-§3 完成;dashboard unit 起、8080 监听;
> `curl -s http://127.0.0.1:8080/api/apks` 有数据(=DB 恢复)或空列表(=待重灌,均算过线)。
> 真机注意:第 5 步起会真实操作手机(13555555555 登录态);若冒烟链路涉及 OTP 登录,
> 先确认 otp-gateway 已按对应清单恢复——不属本清单范围。

## 1. adb 层

```bash
ssh autotest@192.168.12.167 "adb devices -l"
```

期望:列表含 `AKFDCP4718425500`,第二列 `device`(不是 offline/unauthorized/no permissions)。
失败排查:device-pool.md §1(udev/plugdev)/§2(RSA)/§3(手机设置)。

## 2. 平台设备表

```bash
curl -s http://127.0.0.1:8080/api/devices | python3 -m json.tool
```

期望:items 里该设备 `status=online_idle`、`platform=flutter`、`metadata.host`=167 主机名。
不在列表 → 按 device-pool.md §4 手动 register(顺带验证注册接口)。

## 3. 领取/归还往返(平台调度语义)

跑 device-pool.md §5 的 acquire→release 往返,逐项确认:
201 返回 serial → 设备转 busy(current_run=pool-verify-*)→ release 后回
online_idle、current_run 清空、last_assigned_at 刷新。

## 4. Jenkins job 自检(空跑,不占设备)

Jenkins 页(http://192.168.12.167:8082)Build with Parameters:`JOB_ID=selftest-<日期>`;
或命令行:

```bash
TOKEN=$(grep '^JENKINS_API_TOKEN=' /opt/uiautotest/.env.local | cut -d= -f2)
curl -s -X POST -u "admin:$TOKEN" \
  -d "JOB_ID=selftest-$(date +%Y%m%d)" \
  "http://127.0.0.1:8082/job/swan-autotest/buildWithParameters" -w '%{http_code}\n'   # 201
```

期望:构建**绿**、约十几秒;控制台末尾
`job selftest-<日期> 不可认领(不存在/已被认领/非 pending),按空跑退出`;
平台无新增任务行、无 run。失败排查:job-swan-autotest.md §4 三条常见原因。

## 5. 触发一次冒烟(真链路入口)

```bash
/opt/uiautotest/.venv/bin/python /opt/uiautotest/scripts/trigger_smoke.py --suite SMKE
```

期望输出三行关键信息:
- `case_filter=...`——套件成员聚合(YAML 成员应已是
  `tests/flutter/test_case_driver.py::TestCaseDriver::test_yaml_<名>` node id 形态);
- `冒烟任务已派发 Jenkins: job=job-<...> jenkins=swan-autotest build=<M>`——派发成功;
- 退出码 0。

失败排查:
- "冒烟套件不存在" → SMKE 不在:DB 恢复则应在;空库重建情形需先重灌用例+重建套件
  (用例管理域,见部署计划 v3 §0);
- "已入 Jenkins 队列...build 号待分配" → 队列轮询 3 秒内未出 build 号,属正常分支,
  Jenkins 页确认构建最终起来了即可;
- "留 pending 本地兜底(Jenkins 派发失败:...)" → .env.local 的 JENKINS_* 五键没配好
  (回 job-swan-autotest.md 前置);此时任务留 pending,排查完人工重触发或由本机
  兜底 agent(1BG-IT-01796,DASHBOARD_URL 已改指 167 时)接走,再走一次本步。

## 6. 执行与证据落库(逐项打勾)

记下第 5 步输出的 job_id,依次核对:

| # | 检查点 | 期望 | 看哪里 |
|---|---|---|---|
| 6.1 | Jenkins 构建 <M> 终态 | 绿;控制台含 `claimed job=<job_id> source=build-downstream` 与 pytest 输出 | Jenkins 控制台(PUBLIC_URL 跳转) |
| 6.2 | 任务行终态 | `status=succeeded`,`jenkins_job=swan-autotest`,`jenkins_build=<M>` | 平台"流水线"页 / `GET /api/pipeline/jobs` |
| 6.3 | 执行会话(run) | `GET /api/runs/pipe-<job_id>` 终态非 running;平台"最近执行"有该 run | 平台执行页 |
| 6.4 | 用例行 | `GET /api/runs/pipe-<job_id>/cases` 有行(冒烟套件成员数) | 平台执行页 |
| 6.5 | pytest 日志 | `/opt/uiautotest/output/logs/pipeline_pipe-<job_id>.log` 存在,含 `===== attempt 1 =====`,属主 autotest | 167 文件系统 |
| 6.6 | 证据 | 步骤截图按执行与证据模块口径可查(平台证据索引) | 平台 run 详情 |
| 6.7 | 设备归还 | `GET /api/devices` 该设备回 online_idle,current_run 清空 | 平台页 |

任一步红:6.1 看 Jenkins 控制台;6.2/6.3 看 167 `logs/jenkins_dispatch.log` 与
`logs/pipeline_reaper.log`;6.5 看日志文件内容本身。

## 7. env_blocked 通道抽查(建议做一次,验证拔线语义闭环)

把设备人工下线后再触发一次冒烟:

```bash
curl -s -X POST http://127.0.0.1:8080/api/devices/AKFDCP4718425500/status \
  -H 'Content-Type: application/json; charset=utf-8' -d '{"status":"disabled"}'
/opt/uiautotest/.venv/bin/python /opt/uiautotest/scripts/trigger_smoke.py --suite SMKE   # 触发第二次
```

期望:pytest 无设备可领 `pytest.exit(3)` → 任务 `env_blocked`(**不是 failed**,不计失败),
run 有 error 收尾不留悬挂;Jenkins 构建 <M2> 仍绿(agent 正常回报)。恢复:

```bash
curl -s -X POST http://127.0.0.1:8080/api/devices/AKFDCP4718425500/status \
  -H 'Content-Type: application/json; charset=utf-8' -d '{"status":"online_idle"}'
# 或重跑 register(offline/disabled→idle 复活语义以 device-pool.md §4 为准)
```

恢复后 `GET /api/devices` 确认 online_idle。拔线重排 trio(release 时 adb 复查置 offline /
register 复活 / busy 超时自愈)的日常行为不在此逐验,语义见 device-pool.md §5。

## 8. nightly timer(v3 §10 第 7 条)

```bash
systemctl list-timers nightly-pipeline-trigger.timer
# NEXT 列 = 明日 01:33;LAST 列为空属正常(未跑过)
systemctl status nightly-pipeline-trigger.service    # enabled/静态态确认
journalctl -u nightly-pipeline-trigger.service       # 最近一次触发输出(如有)
```

真跑一轮验证会实际触发全量 driver 冒烟(时长可观),酌情手动 `sudo systemctl start
nightly-pipeline-trigger.service` 一次并回看 journalctl;触发链路本身第 5 步已验。

## 9. 收尾

第 5/6 步的 job_id、build 号、run_id 回填部署计划 v3 §10 第 6 条;nightly timer 状态回填
第 7 条;第 7 步的 env_blocked job_id 一并记(语义闭环证据)。全部通过后宣告设备池/
执行链路恢复。
