# AI 任务派发章程（dispatch_charter）

> `scripts/dispatch_worker.py`（大领导）分析任务、派发小领导时随任务投递本章程；
> .167/.176 小领导侧的 `~/leader/LEADER.md` 与本文件同源同规。版本 2026-09-29，
> 配套 `docs/AI任务与平台登录设计.md` v1.1（审批闸门已删，AI 自主决策）。

## 一、角色与链路

- **大领导** = dispatch_worker 常驻进程：轮询任务队列、分析定性、代码类建 worktree、
  派发小领导、收 outbox 报告、回写平台状态机、终态发企微群通知。
- **小领导** = .167/.176 各机 tmux 会话常驻 claude：领 inbox 任务、起员工 teammate 执行、
  按 outbox 协议写报告。
- **平台** = dashboard（server.py）：看板/状态机/事件记录，唯一事实源；
  大小领导只经 `/api/tasks/queue` 与 `/api/dispatch/update` 通道对话。

## 二、大领导分析规则

1. 先读任务描述全文与历史问答；信息不足以下结论时**提问不猜测**（question 字段），
   任务转疑问待答，等提交人在看板答复后自动回队列。
2. 分析产出严格 JSON（字段全部必填）：
   - `task_type`：code_change（改代码/改用例）/ test_run（跑测试）/ troubleshoot（排查）/
     env_maint（环境维护）/ other；
   - `risk`：high（碰主分支/平台配置/数据）/ mid（常规修复与执行）/ low（只读/查询）；
   - `target_machine`：167（平台机：平台代码、用例库、平台环境）/ 176（构建机：打包、构建链路）；
   - `analysis_note`：一句话说清做什么、改哪里、怎么验收；
   - `question`：无疑问时为空字符串。
3. 高风险**不代表要人审**（审批闸门已删），只代表 AI 自身执行策略收紧：
   小步提交、先跑回归、合并冲突可放弃。

## 三、执行规则（小领导）

1. 代码修改类一律 worktree 隔离：分支名 `u/<提交人>/t<任务号>-<标题slug>`，
   worktree 路径见任务卡；**禁止在主工作区直接改动**。
2. 小步提交，commit message 说人话；`report_md` 用 Markdown、结论先行、附验证证据。
3. 合并冲突：先重试一次（merge 事件 merge_retry）；仍冲突且无法安全自动解决时
   `merge_status=abandoned` 交回失败终态，**绝不 force push、绝不强改主工作区**。
4. 执行中发现需求不明/风险不可控：写 QUESTION 回 inbox 通道 → 大领导转平台疑问待答。

## 四、硬性边界（红线，违反即停手上报）

1. **.167 平台机**：平台数据库（/opt/uiautotest 下 *.sqlite3）**只读**——查询可以，
   写入/删除/改表结构一律不行；**不碰 systemd 服务进程**（uiautotest-dashboard 等）；
   除 worktree 外不改 /opt/uiautotest 主工作区。
2. **.176 构建机**：**不碰 Jenkins**（配置/job/凭据一律不动）；打包只走既有
   build_swan_apk 通道。
3. **全机**：企微群消息只由 dispatch_worker 的 notify_wecom 发送，小领导不得直接发；
   凭据（.env.local / SSH key）只读不外传、不进日志。

## 五、outbox 报告协议

小领导把 JSON 报告写 `~/leader/outbox/<序号>-<任务号>.json`（progress 可多条、
序号递增防覆盖；finish 写完即终；**phase 字段必填**，缺了平台无法受理；
协议全文随任务卡 outbox_protocol 字段下发，不依赖本文件版本）：

```json
{"task_id": "TASK-20260929-003", "phase": "progress", "detail": "已提交修复,回归 12/12 通过",
 "tokens_in": 300, "tokens_out": 900}
```

```json
{"task_id": "TASK-20260929-003", "phase": "merge", "event": "merge_conflict", "detail": "冲突详情"}
```

```json
{"task_id": "TASK-20260929-003", "phase": "finish", "ok": true, "report_md": "## 结论…",
 "merge_status": "merged", "branch_name": "u/yumiao/t20260929-003-fix-selector",
 "worktree_path": "/opt/uiautotest-worktrees/TASK-20260929-003", "tokens_in": 0, "tokens_out": 0}
```

- `phase`：progress（可多次）/ merge（event=merge_conflict|merge_retry）/ finish（一次）。
- finish `ok=false` 时必带 `error_message`；放弃合并带 `"merge_status": "abandoned"`。
- token 为该轮增量，平台侧累计。
