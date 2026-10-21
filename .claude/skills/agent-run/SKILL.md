---
name: agent-run
description: 启动 agent 团队（agent teams teammate 形态）的多模式入口。模式一启动 Swan 测试平台全部 7 个 teammate（6 个模块开发 + 1 个产品设计师）并汇报就绪状态。用法 /agent-run [模式名]，无参数时默认列出可用模式供选择。
---

# agent-run：Agent 团队启动器

按用户指定的模式启动对应 agent 团队。**无参数调用时**，不要猜测模式，先列出下表可用模式让用户选择。

## 可用模式

| 模式 | 名称 | 说明 |
|---|---|---|
| `平台模式`（`swan-platform`） | Swan 测试平台全量启动 | 启动 7 个 teammate（6 个模块开发 + 1 个产品设计师），见下文详细流程 |
| （更多模式待用户后续定义，定义后在此表追加，并在下方补充对应流程章节） | | |

---

## 启用前提（不满足则先补齐，再启动）

1. 用户级 `~/.claude/settings.json` 的 `env` 块含 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`（2026-09-17 已配置）
2. 本会话是**交互式会话**，且在该开关存在之后启动（开关在 CLI 启动时装配，会话中途改 settings 不生效）
3. 7 个定义文件在用户级 `C:\Users\menglz.SWANSPEAKERS\.claude\agents\`，文件名即中文名，各文件 `tools:` 已含 `Agent`（teammate 才能自开子 agent）

## 形态自检（每轮启动必做，先派 1 人验证）

先派名单中第一个 teammate，等它回报后立即 `ListAgents` 检查其状态：

- 状态为 **idle**（常驻待命）→ teammate 形态成立，继续派其余成员
- 状态为 **completed**（一次性跑完）→ 本会话不满足 teammate 条件（会话早于开关启动，Agent 工具无 `name` 参数）。**停止继续派出**，告知用户：重启会话后重跑 `/agent-run 平台模式`

---

## 模式一：Swan 测试平台全量启动（`swan-platform`）

### 团队名单

| 中文名（subagent_type / name） | 原英文名 | 职责一句话 |
|---|---|---|
| 打包构建 | swan-build-jenkins | .176 构建环境(/opt/build-env)+ build_swan_apk.py 打包脚本,衔接 Jenkins 打包 job(master@.167:8082,agent@.176) |
| 用例管理 | swan-case-mgmt | 用例域全栈：case_defs/test_suites/suite_cases/modules 表与 CRUD、用例页+套件页、sync_cases.py 同步 |
| 流水线执行 | swan-pipeline | pipeline_jobs、触发通道、pipeline_agent.py 执行 agent、设备池调度 |
| 流水线调度 | swan-pipeline-ui | pipeline_configs 配置/CRUD/next_run_at、调度器、流水线页签 |
| 包管理 | swan-pkg-manager | APK 登记/列表/关联执行 API、包管理 tab、register_apk.py |
| 执行与证据 | swan-runs-evidence | runs/cases 表、三视角查询、禅道建单、pytest 上报、证据清理 |
| 产品设计师 | swan-designer | 维护 docs/平台UI设计规范.md（竞品对标/新页签设计/防 AI 味审查）；只设计不改代码 |

### 启动流程

1. **幂等检查**：先 `ListAgents`。已在列表中的成员不重复派，只补缺失的；汇报中注明"已存在 X 人 / 本次新派 Y 人"。
2. **形态自检**：按上文"形态自检"先派第一个（打包构建），确认 idle 常驻后再继续。
3. **并行派出**：用 Agent 工具一次性并行派出其余成员，`name` 与 `subagent_type` 均用中文名。初始 prompt 模板：

   ```
   你是 Swan 测试平台「<职责模块名>」开发 teammate，属于测试平台团队。

   启动流程：
   1. 读 E:\swan-map-station\CLAUDE.md（项目约定，硬性规则必读）
   2. 读 E:\swan-map-station\docs\agent团队名册.md 中与你模块相关的段落
   3. 读 E:\swan-map-station\docs\agent模块档案\<你的中文名>.md——前任 teammate 留下的业务档案；发现与代码不符时以代码为准，并顺手修正档案
   4. 档案不存在时（首次启动）：通读你负责的代码，按"数据表/API 路由/页面与 JS 前缀/脚本与守护进程/业务规则与坑/模块边界/行号锚点/现状与未尽"结构生成档案落盘（≤150 行，中文紧凑）
   5. 熟悉你的模块上下文（按你定义文件中"你负责的范围"与"代码地图"）
   6. 读完后，最终回复严格用以下格式：

   [就绪]
   职责：<一句话>
   已读文件：<列表>
   待命

   不要开始任何开发任务，读完上下文汇报就绪即可。此后保持待命，等主会话派任务。
   ```

4. **收就绪回报**：等全部成员的 `[就绪]`（或超时统计失败者）。
5. **下发令牌**：全部就绪后，向每个 teammate 各发一条 SendMessage：

   > 团队就位。后续我会用标准任务格式派活。收到任务后：
   > 1. 按需加载上下文（CLAUDE.md 必读，其余按任务加载）
   > 2. 每完成一个关键节点主动发"[进度]"消息，不要等到最后才汇报
   > 3. 完工后用"[完成]"或"[失败]"格式向我回报
   > 4. 发现超出职责范围的事，不要擅自做，回告我重新分派

6. **汇报用户**：简报表逐行列出：中文名、职责、状态（就绪/失败）。并说明：这是常驻 teammate 团队（窗口底部 agent 面板可见、成员间可互发消息、每个成员可自开子 agent），并附"怎么用"示例。

### 运行面(.167 平台机 / .176 打包机)在本模式中的角色(teammate 一律不碰运行面)

- **本机 = 开发面**：6 个开发 teammate 只改本地仓库代码，自测用临时端口+临时 db；产品设计师只产出 docs/、不碰代码；硬性纪律（见 E:\swan-map-station\docs\agent团队名册.md）：不 ssh 运行面(.167/.176)、不杀进程、不动 systemd、不提交 git——部署与提交由主会话统一做
- **运行面日常操作由各机小领导承担（2026-09-22 起的两级编排）**：.167/.176 各有常驻小领导 m167-leader / m176-leader（tmux `leader` 会话内交互式 claude，bypassPermissions，可自起本地 teammate）。派活：scp 任务文件到 `~/leader/inbox/` + `tmux send-keys`；收结果：读 `~/leader/outbox/`。章程（职责范围+硬性边界）在各机 `~/leader/LEADER.md`。本地 teammate 纪律不变——运行面工作经主会话转派给小领导，teammate 不得自行 ssh
- **.167 平台部署仍由主会话执行**：验收后走部署节奏 `commit → push → ssh autotest@192.168.12.167 git -C /opt/uiautotest pull --rebase → sudo systemctl restart uiautotest-dashboard → curl 验证 8080`；小领导对平台库严格只读、不重启服务、不改 /opt/uiautotest
- **.176 打包机**：小领导可维护 laya judge 服务（~/laya-judge，端口 8089）与 NVIDIA 驱动；硬边界：绝不碰 Jenkins（进程//opt/build-env/systemd 单元）、不重启机器
- **(已废,随 .166 退网).166 的 Claude 分调度**：已被上述小领导机制替代

### 纪律

- teammate 随会话结束而消亡（不跨会话存活，/resume 不恢复）；重启后需重新 `/agent-run` 拉起
- teammate 可自开 subagent（定义 tools 已含 Agent），**不能再开 teammate**（禁止嵌套团队）
- teammate 未就绪前不派开发任务
- 任何成员回报超职责事项时，由主会话重新分派，不得转发越界指令
- 模块业务档案（docs/agent模块档案/<中文名>.md）归各模块 teammate 维护：首次启动生成，此后改模块代码时同步更新——teammate 随会话消亡，知识靠档案传承
