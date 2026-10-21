# Swan 平台 Agent 团队名册

> 用途:会话重启(/resume)或新会话时,主会话(调度者)读本文件即可恢复团队全貌。
> 角色定义文件在 `C:\Users\menglz.SWANSPEAKERS\.claude\agents\`;开发任务进度详见 `docs/测试套件管理计划.md`(总体计划 v2)。
> **拓扑备忘(2026-09-22)**:.166 已退网;现 .167=平台机(dashboard :8080、Jenkins master :8082、Prometheus/Grafana/otp-gateway 同机)、.176=打包机(Jenkins remoting agent);本文历史记录中的 .166 均为旧口径。
> **两级编排(2026-09-22 起)**:.167/.176 各有常驻小领导(m167-leader/m176-leader,tmux `leader` 会话交互式 claude),承担运行面日常操作(诊断/日志/服务维护/laya judge);派活 scp 到 `~/leader/inbox/`+send-keys,结果读 `~/leader/outbox/`,章程在 `~/leader/LEADER.md`。本地 teammate 纪律不变:不 ssh 运行面,运行面工作经主会话转派;部署与 .176 的 Jenkins 仍主会话专属(小领导章程禁碰 Jenkins)。POC 产物在 laya-poc 分支 `poc-laya/`。

## 协作模式

主会话 = 调度者:接收用户任务 → 派给对应模块 agent → 验收(diff 审查+冒烟)→ 统一提交 git → 部署 .167 → 向用户汇报。
模块 agent:只改自己模块,不部署、不提交 git,自测用临时端口+临时 db。

## 团队成员

| 代号 | 职责 | 状态 | 备注 |
|---|---|---|---|
| 调度者(主会话) | 任务接收/分配/汇总/验收/提交/部署/播报 | 常驻 | 巡检 cron 每 23 分钟(会话内) |
| 用例管理 agent | case_defs/test_suites/suite_cases/modules CRUD、用例页+套件页、sync_cases.py（2026-09-18 并入原套件管理） | 待命(可唤醒) | 修复过列宽拖拽×2、列重叠;单列宽度=可见列宽之和的方案已验证 |
| **jenkins打包模块开发** | .176 构建环境(/opt/build-env)+ scripts/build_swan_apk.py | ✅ 环境与脚本已交付(a690bc5);register_apk.py 缺 apk_id 的 bug 已修(branch 兜底+--apk-id 参数) | 构建环境:C:\build-env\+env.ps1(JDK17 Temurin/Android SDK 34+36/Flutter 3.47.4 南京大学镜像,flutter doctor 通过);**阻塞:auto_tester 权限未生效,真打包未验**;注意 .166 的 python 是 WindowsApps 占位符,远程跑脚本用 py -3 |
| **平台侧流水线开发** | pipeline_configs + 调度器 + 流水线页面 | ✅ 已交付(1118a66+aff7db7,已部署) | 已完成:套件→case_filter 聚合、web 平台支持、门禁按平台裁剪、rc3 补 run 收尾(执行会话/流水线状态一致) |
| **平台侧用例模块开发** | 测试套件管理 M4 + 用例页 6 条优化 + 规范对齐 | ✅ 已交付部署(34fcedd+aff7db7,2026-09-16) | 套件 CRUD/成员管理/modules/优先级徽章/编辑抽屉/**批量导入+CSV 模板下载**(aff7db7);批量导入已扩至 .xlsx/.csv/.md 三格式+SheetJS vendor(e38c357);未尽:批量操作条、toast 组件、账号体系、字段化模板编辑、脑图视图 |
| **平台侧产品设计师** | 全网检索同类测试平台交互与 UI,产出 docs/平台UI设计规范.md;所有开发 agent 页面开发以规范为唯一依据 | ✅ 规范已交付(2026-09-16);2026-09-18 起常驻 /agent-run 团队 | 只设计不改代码;按 P0/P1 建议清单驱动各页签改造;定义文件 ~/.claude/agents/产品设计师.md |

## 关键约定(全团队硬性纪律)

1. 凭据(auto_tester 密码/禅道账密)只存 .167 `/opt/uiautotest/.env.local`,不进 git/代码/文档
2. agent 不部署(不 ssh 运行面(.167/.176)杀进程/不动 systemd)、不提交 git——主会话统一做
3. 运行面(.167/.176)上绝不 kill 全部进程,只杀指定 PID
4. 提单(禅道)必须人工确认;企微机器人外发先征得用户同意
5. 冒烟:临时端口 180xx + 临时 db,测完删;JS 改动 node --check + `$()` 引用交叉核对
6. 部署节奏:commit → pull --rebase(合并 git 上他人更新,有冲突先解决再推) → push → `ssh autotest@192.168.12.167 git -C /opt/uiautotest pull --rebase` → `sudo systemctl restart uiautotest-dashboard` → curl 验证 8080

## 交接记录

### 2026-09-20 平台冲刺（/agent-run 全员会话）

- **团队形态**：/agent-run 平台模式拉起 7 teammate 常驻（swan-build-jenkins/swan-case-mgmt/swan-pipeline/swan-pipeline-ui/swan-pkg-manager/swan-runs-evidence/swan-designer）；agent-run skill 见 .claude/skills/agent-run/
- **阶段一 7 项小修全部完成**：apk_id→SWAN_APK_ID 透传、running 超时抢回（RUNNING_TIMEOUT_HOURS 默认 1h 可环境变量覆盖）、拔线设备 offline 收敛（release 复核/busy→offline/register 复活）、delete_case_def 清理 suite_cases、page.html:208 过时 hint、包管理 P0（弹窗收起+全站 Toast/mono 组件）、下包页设计（docs/下包页设计.md，服别=server_type 列口径）
- **M3 Jenkins 基础设施已装（主会话操作 .166）**：Jenkins LTS 2.568.3 于 .166:8082（计划任务 JenkinsLTS，SYSTEM+BootTrigger，JENKINS_HOME=C:\build-env\jenkins\home，war+C:\build-env\jdk-21）；管理员+API Token 已配，凭据只在 .166 .env.local（JENKINS_BASE_URL/JENKINS_USER/JENKINS_API_TOKEN/JENKINS_ADMIN_PASSWORD）；防火墙 8082 已放行；job×2 已建：swan-audio-build（BRANCH/COMMIT/SERVER_TYPE→build_swan_apk.py）、swan-autotest（JOB_ID→pipeline_agent --job-id）
- **新能力定案**：apks 表加 server_type（formal=正式服/test=测试服，存量默认 test）+ build_time 列；新增 GET /api/apks/<id>/download 下载接口；build_swan_apk.py 加 --server-type/--build_time payload
- **进行中（2026-09-20 全部转终态）**：Jenkins 平台侧集成 ✅（jenkins_client.py/pipeline_jobs 加列/try_jenkins_dispatch/claim 双通道/pipeline_agent --job-id 定向认领）、下包页前端 ✅（新页签"下载安装包"：服别胶囊/双卡最新包/7 列表/二维码弹窗 160+260px/搜索分支过滤/vendor 本地 qrcodejs）、runs 超时收尾 ✅（reaper+孤儿 runs 清扫）、nightly 套件化 ✅（nightly_trigger.py 重写五模式，三道闸切换）、YAML 用例映射 ✅（sync_cases linked_case_id 显式映射）、竞品采集 ✅（docs/竞品截图/）、M5 打包→冒烟联动 ✅（trigger_smoke.py+case-paths 端点）、M6 禅道 testtask 回写 ✅（手动按钮+幂等，REST v1 已探测：v1 无用例级端点→汇总写 desc）
- **主会话部署（2026-09-20 已全部完成）**：统一提交 5d8b5f2 → push → .166 pull → 重启 8080 → .166 数据准备（sync_cases 165 条、nightly bootstrap SUITE-NIGHTLY-REGRESSION、冒烟套件 SMKE、swan-audio-build job 追加 trigger_smoke 批处理行、.env.local 补 ZENTAO_PROJECT_ID/ZENTAO_PRODUCT_ID 占位注释）
- **待用户**：① auto_tester GitLab Reporter 权限 + 仓库 Clone 地址（M1 真打包）② 禅道 project id/product id（M6 首单验证）③ nightly run-now 报备后确认切换（--enable + 禁旧计划任务）
- **回退方式**：.166 Jenkins = schtasks /end + /delete JenkinsLTS；JENKINS_HOME 可整体删除重建；nightly 回滚 = nightly_trigger --disable + 重启用旧计划任务

### 2026-09-20 冲刺收尾（部署 + E2E 验证）

- **部署与 .166 数据准备全部落盘**（细节见上一节主会话部署行）；8080 已验证新功能在线（下载页/zenTaoSync/vendor qrcode.js/apks API 含 server_type/build_time）
- **E2E：四次 run-now 全部走通 Jenkins 真链路**（swan-autotest build #1-#4：派发→定向认领→jenkins_build 回填→退出码语义）；#1-#3 卡 pytest 依赖（**SYSTEM 会话看不到 admin 的 Roaming 用户 site，pip "already satisfied" 跳过机器级安装**——用 `pip install --ignore-installed` 机器级装齐后 `py -3 -s -c "import ..."` 验证）；#4 命中设备门禁：**测试机 AKFDCP4718425500 USB 物理离线**（adb kill-server 后总线仍无设备，PnP 无 Android/MTP 设备，需人工插回/开机），env_blocked 收尾完全符合设计（pytest.exit(3)→agent 补 error 收尾+summary，不重跑不计失败；runs 留 error、jobs 留 env_blocked，server.py:772 对账逻辑本就如此设计）
- **用例级执行验证待设备恢复后重跑一次 run-now 即可**（链路其余环节已全部实测通过）；本地 12 条 mock 用例（test_swan_login_aw.py）经仓库 venv 实测 12 passed
- .166 C:/Users/admin 下本会话 12 个临时探测脚本已清理（更早会话遗留 18 个未动）

### 2026-09-18 团队重划 + 业务档案机制(接手者从这里开始)

- **团队重划**:原"套件管理"agent 并入"用例管理"(消除用例页双 ownership 与 modules/suites[] 交叉耦合),现 6 个开发 agent + 1 产品设计师;名单见 E:\UIAutotest\.claude\skills\agent-run\SKILL.md,定义文件在 ~/.claude/agents/
- **业务档案机制**:docs/agent模块档案/<中文名>.md 一模块一份,teammate 首次启动通读代码生成、此后维护(改代码同步更新),跨会话知识靠档案传承;初版 6 份已于 2026-09-18 由各模块 agent 生成(66-93 行,行号已实读校准)
- **档案编写时发现的跨模块待办**(未修,等派活):① pipeline_agent.py 未把 job.apk_id 传成 SWAN_APK_ID,流水线指定包落不到执行会话(包管理档案 §7/§8) ② page.html:208 hint"套件功能未上线"过时 ③ delete_case_def 不清理 suite_cases 留孤儿(SQLite FK 未启用,用例管理档案 §6)
- 产品设计师 2026-09-18 起常驻 /agent-run 团队(定义文件 ~/.claude/agents/产品设计师.md,只设计不改代码,tools 无 Bash)

### 2026-09-17 里程碑进度总览

对照 `docs/测试套件管理计划.md` v2 的 M1-M6:

| 里程碑 | 状态 |
|---|---|
| M1 打包链路 | 🟡 约90%:环境+build_swan_apk.py 已交付;**真打包被 auto_tester 权限卡**(需 Owner 加进 swan-audio Reporter) |
| M2 流水线配置+调度 | ✅ 完成 |
| M3 Jenkins 接入 | ⬜ 未开始——**下一个大块**(.166:8082 装 Jenkins、2 个参数化 job、jenkins_client.py、状态同步) |
| M4 测试套件管理 | ✅ 完成(超前) |
| M5 套件×触发矩阵 | 🟡 一期完成(套件→case_filter 聚合+web 平台+真机 E2E 已验);二期待:打包→冒烟联动、nightly 套件化、YAML 驱动用例映射 |
| M6 禅道单回写 | ⬜ 可选,最后评估 |

执行层现状:M3 前 PipelineAgent(本机 1BG-IT-01796)顶替 Jenkins 接活,配置/套件层已留切换口。
其他进行中:平台侧产品设计师已常驻 agent-run 团队(docs/平台UI设计规范.md 已交付);文档产出以桌面《Swan测试平台全流程详解.md》为唯一使用文档(旧介绍文档已删,976828a)。

---
(以下为历史交接记录)

- 2026-09-16 平台侧流水线开发交付:commit 1118a66(服务已重启生效,`/api/pipeline-configs` 200;PipelineScheduler 计划任务 Running,日志 logs/pipeline_scheduler.log)。未尽:套件下拉未填充(等 M4)、include_build 只存配置未接执行层、调度器停机跨分钟不补跑
- 2026-09-16 待办:① 催项目 Owner 把 auto_tester 加进 swan-audio(Reporter,直接成员)② 用户提供仓库准确 Clone 地址 ③ 权限通后 jenkins打包模块开发 联调真打包,随后 M3 Jenkins 接入(.166:8082)
