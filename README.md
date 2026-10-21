# swan-map-station —— Swan 服务端两件套

> 平台部分 2026-10-13 自 UIAutotest 自动化工程剥离并入(方案:`docs/测试平台分离准备.md`);main 主线 = 工作站原项目(王端侧 0.3.0 交付,交接见 `CLAUDE-HANDOFF.md`)+ 平台资产整合。

## 一句话

**功能地图工作站(:8090)+ 测试平台(:8080)**,共用唯一库 `.167:/opt/uiautotest/output/dashboard.sqlite3` 的 `case_defs` 表——用例正文/人审/执行历史一份数据,两个入口展示与增删改查。

## 结构

| 路径 | 内容 |
|---|---|
| `dashboard/` | 测试平台 :8080——server.py(纯标准库)、page.html、jenkins_client、zentao_client、case_api_client、tests/、vendor/ |
| `server/` | 功能地图工作站 :8090 后端 serve.py(共库运行版,md5 1081a5f;部署于 .167 /opt/app/server/) |
| `frontend/` | 工作站前端 React 源码(tokens/motion/DS 组件/Dashboard·Workbench·图谱/用例人审两道门页);dist 构建产物不入仓 |
| `qa_platform/` | 设备池(device_pool)+ preflight |
| `scripts/` | 打包(build_swan_apk)、流水线(pipeline_agent/scheduler、dispatch_worker)、包管理(register_apk)、用例同步(sync_cases)、巡检(patrol/)、OTP 网关(otp_gateway/)等 |
| `docs/` | 平台文档 + agent模块档案(9 个模块蓝图)+ 竞品截图/设计稿 |
| `tools/provision/` | Jenkins/设备池/监控装机与 job 定义 |
| `.claude/agents/` | 平台模块 agent 定义(用例管理/包管理/打包构建/流水线×2/执行与证据/产品设计师) |

## 运行面

- **.167(平台机)**:/opt/uiautotest(测试平台部署 + output/dashboard.sqlite3 + .env.local 凭据)、/opt/app(工作站,systemd `swan-map-station.service`,User=autotest)
- **.176(构建机)**:Jenkins + Android SDK/Flutter(打包链路)
- 凭据只在 .167 `/opt/uiautotest/.env.local`,不进仓库

## 启动与部署

```bash
# 测试平台(.167 /opt/uiautotest/dashboard):python3 server.py(端口 8080)
# 工作站(.167 /opt/app/server):systemd swan-map-station.service(端口 8090)
# 改 server/serve.py 后部署:
scp server/serve.py 192.168.12.167:/tmp/serve.py
ssh 192.168.12.167 "sudo cp /tmp/serve.py /opt/app/server/serve.py && sudo chown autotest:autotest /opt/app/server/serve.py && sudo systemctl restart swan-map-station"
```

## 自动化侧对接

pytest 用例(自动化仓 UIAutotest)经 `reporter/` 上报执行结果到 :8080;用例文件头 `# CASE-ID: aw-xxx` / `# LINKED-CASE: REG-0NN` 标记由 `scripts/sync_cases.py` 扫描入库——标记行在自动化仓维护,本仓负责扫描同步。
