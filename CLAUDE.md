# swan-map-station 平台仓 AI 约定

> 本仓所有 AI 助手开工前必读。变更慎重。
> 铁律:所有输出一律中文。

## 项目一句话

Swan 服务端两件套:**功能地图工作站(:8090)+ 测试平台(:8080)**,共库(`.167:/opt/uiautotest/output/dashboard.sqlite3` 的 `case_defs` 表)——一库两服务,展示与 CRUD 同一张表。来源:UIAutotest 自动化工程 2026-10-13 剥离(方案 `docs/测试平台分离准备.md`)。

## 硬性纪律(任何 agent 必须遵守)

- **凭据**只在 .167 `/opt/uiautotest/.env.local`,不进仓库/文档/记忆/打印(只 grep 键名不读值)
- **运行面**(.167/.176)绝不 kill 全部进程,只杀指定 PID;破坏性操作备份先行
- **模块 agent 不部署不提交 git**——主会话(orchestrator)统一做
- 部署模式:本地改 → scp /tmp → sudo cp → systemctl restart → md5 核对;`/opt/app` 属主 autotest(jks 操作需 sudo)
- 表结构唯一 owner = dashboard/server.py(本仓);工作站只消费稳定字段(structured 内部结构变更须同步 server/serve.py 还原逻辑)

## 模块与 agent

| 模块 | 代码 | agent 定义 |
|---|---|---|
| 用例域(case_defs/suites/modules) | dashboard/server.py、scripts/sync_cases.py | `.claude/agents/用例管理.md` |
| 包管理 | dashboard/server.py(apks)、scripts/register_apk.py | `.claude/agents/包管理.md` |
| 打包构建 | scripts/build_swan_apk.py、Jenkins | `.claude/agents/打包构建.md` |
| 流水线调度 | scripts/pipeline_scheduler.py | `.claude/agents/流水线调度.md` |
| 流水线执行/设备池 | scripts/pipeline_agent.py、qa_platform/ | `.claude/agents/流水线执行.md` |
| 执行与证据 | dashboard(server runs/cases)、pytest 上报协议 | `.claude/agents/执行与证据.md` |
| 监控/巡检 | scripts/patrol/、tools/ | 蓝图 `docs/agent模块档案/` |

模块蓝图(锚点式业务档案)在 `docs/agent模块档案/`,agent 实体定义在 `.claude/agents/`(项目级)。

## 用例域纪律

- **共库唯一数据源**:case_defs 表;工作站(CASE_PRIMARY_VER=0.3.0 走库,demo 版旧文件只读)+ 测试平台直连;方案与遗留待办:`docs/用例域整合计划.md`
- **文字用例**:工作站编号(`launch-NEG-001` 式);正文 `structured`(机器读)+ `content`(人读),人审 `review_meta`
- **自动化用例(auto)**:case_id = `aw-<py文件主干>`;**py 文件在自动化仓(UIAutotest)**,文件头标记 `# CASE-ID: aw-xxx` + `# LINKED-CASE: REG-0NN,REG-0NN`(逗号多值);本仓 `scripts/sync_cases.py` 扫描入库(改挂接 = 改自动化仓标记再重跑 sync)
- pytest 执行结果按 CASE-ID 聚合上报(自动化仓 `reporter/` → :8080)
- 旧 76 条 REG-xxx 保留作挂接锚点

## 运行时经验

| 场景 | 正确做法 |
|---|---|
| 平台登录 | 测试平台无账号体系(API 免登录白名单 MACHINE_EXEMPT);工作站人审需 reviewer 名 |
| 数据库并发 | SQLite WAL + busy_timeout=15000;低频人工写足够,量大迁 PostgreSQL 只改连接层 |
| 中文 case_id | URL 路径须 quote/unquote(aw-中文编号);server.py 三处提取已处理 |
| sync_cases.py | `--dry-run` 先校验再实跑;文字用例默认跳过(权威源在工作站);`--annotate` 改的是自动化仓文件 |

## 编码规则

- 异常默认抛出;仅 ADB 外部命令/teardown/资源释放允许 try-catch;禁 catch-all 吞异常
- 日志:`logger = logging.getLogger(__name__)`,`✅ 成功 / ❌ 失败` 格式
- 服务端代码保持零第三方依赖(server.py/serve.py 纯标准库);新增依赖须拍板
