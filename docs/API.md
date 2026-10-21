# Swan 功能地图工作站 · 用例人审 API 文档

> 版本：1.1（2026-10-13） · 适用站点版本：0.3.0+（兼容 0.2.0） · 服务实现：`server/serve.py`
>
> 本文档面向调用方开发者（开发机 192.168.12.229 及后续集成方）。
>
> **v1.1 变更**：新增多版本查询（`?v=` / body `version`）与 `GET /api/versions`；数据模型更新至 0.3.0；持久化改为按版本隔离；标注 CORS 收紧与 script 可空。

---

## 1. 概览

用例人审 API 为「两道门人审工作台」提供服务端持久化能力：用例查询、门禁留痕、单条/批量裁决、审计日志。

| 项 | 值 |
|---|---|
| 基础地址 | `http://192.168.12.167:8090`（内网/开发机）· `http://127.0.0.1:8090`（本机） |
| 协议 | HTTP/1.1，UTF-8；支持 HEAD（健康检查）与媒体 Range（mp4 拖动） |
| 认证 | **无**（内网可信边界）。公网暴露前必须加反向代理鉴权层 |
| 多版本 | 站点可共存多个数据轮（如 0.3.0 + 0.2.0），请求级切换，见 §1.1 |
| 内容类型 | 请求 `application/json`（上限 **1MB**）；响应 `application/json; charset=utf-8` |
| CORS | **未开放**——浏览器跨源调用会被拦截；curl / 服务端到服务端调用不受影响 |
| 并发 | 服务端写锁保护，可安全并发调用 |
| 审计 | 全部写操作追加式留痕（`data/review/audit.jsonl`），**不可撤销** |

### 1.1 多版本查询（0.3.1 起）

- **GET** 接口均支持 `?v=<版本号>` 按版本取数（如 `/api/review/cases?v=0.2.0`）；版本号须真实存在，否则静默回退当前版本（`data/current.json` 指针）。
- **POST** 人审接口在 body 中加可选字段 `"version"`；省略时写入**当前版本**。
- 用 `GET /api/versions` 获取可用版本目录（§3.1）。

> 集成建议：POST 显式携带 `version`，避免版本切换瞬间写串轮次。

### 核心业务规则（服务端强制）

1. **两道门纪律**：一条用例必须先经「门一（用例定义）」与「门二（编译脚本）」审阅留痕，才允许 `pass=true` 入库；违反返回 `409`。
2. **批量同批次**：一次批量裁决的用例必须属于同一 `batch`；跨批次返回 `409`。
3. **操作人必填**：所有 POST 请求必须携带 `reviewer`（非空字符串），写入审计日志。

---

## 2. 快速开始（60 秒走完一条用例）

```bash
BASE=http://192.168.12.167:8090/api/review
R="审核员姓名"

# ① 审阅两道门（顺序任意）
curl -X POST "$BASE/gate" -H "Content-Type: application/json" \
  -d '{"caseId":"login-LOG-001","gate":"definition","reviewer":"'"$R"'","version":"0.3.0"}'

curl -X POST "$BASE/gate" -H "Content-Type: application/json" \
  -d '{"caseId":"login-LOG-001","gate":"script","reviewer":"'"$R"'","version":"0.3.0"}'

# ② 两门已过 → 提交入库
curl -X POST "$BASE/decision" -H "Content-Type: application/json" \
  -d '{"caseId":"login-LOG-001","pass":true,"reviewer":"'"$R"'","version":"0.3.0"}'
# → {"ok":true,"decided":["login-LOG-001"],"status":"stored"}
```

Python 示例（requests）：

```python
import requests
BASE = "http://192.168.12.167:8090/api/review"
V = "0.3.0"

requests.post(f"{BASE}/gate", json={"caseId": "login-LOG-001", "gate": "definition", "reviewer": "张三", "version": V})
requests.post(f"{BASE}/gate", json={"caseId": "login-LOG-001", "gate": "script",     "reviewer": "张三", "version": V})
r = requests.post(f"{BASE}/decision", json={"caseId": "login-LOG-001", "pass": True, "reviewer": "张三", "version": V})
print(r.json())   # {'ok': True, 'decided': ['login-LOG-001'], 'status': 'stored'}
```

---

## 3. 端点参考

### 3.1 GET /api/versions — 可用版本目录（v1.1 新增）

**请求参数**：无

**响应字段**（200）：

| 字段 | 类型 | 说明 |
|---|---|---|
| current | string | 当前版本（`data/current.json` 指针） |
| versions | object[] | 每轮：`version` · `current`(bool) · `date`(构建日期) · `demo`(bool) · `cases`(用例数) · `apk`(APK 徽标) |

**示例**：

```bash
curl http://192.168.12.167:8090/api/versions
```

```json
{
  "current": "0.3.0",
  "versions": [
    { "version": "0.3.0", "current": true,  "date": "2026-10-10", "demo": false, "cases": 1060, "apk": "5.3.2 (build 34) dev" },
    { "version": "0.2.0", "current": false, "date": "2026-09-10", "demo": true,  "cases": 198,  "apk": "5.3.0 (build 32) debug-test" }
  ]
}
```

---

### 3.2 GET /api/review/cases — 全量用例（含人审状态）

返回指定版本的全部用例，**已合并**服务端决策与门禁状态。前端/集成方一次拉取即可工作，无需自行合并。

**请求参数**：

| 参数 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| v | query | string | 否 | 版本号；缺省=当前版本；非法值回退当前版本 |

**响应字段**（200）：

| 字段 | 类型 | 说明 |
|---|---|---|
| schema | string | 当前固定 `cases/0.3` |
| policy / reviewer / note | string | 门禁策略、审核角色、数据溯源说明 |
| cases | TestCase[] | 用例数组，见 §6.1；服务端额外合并 `status`/`reason`/`note`/`reviewer`/`decidedAt`/`gates` |

**示例**：

```bash
curl "http://192.168.12.167:8090/api/review/cases?v=0.3.0"
```

```json
{
  "schema": "cases/0.3",
  "cases": [
    {
      "id": "login-LOG-001",
      "title": "未登录启动显示登录页",
      "batch": "B-LOG-01",
      "featureId": "M02-F01",
      "status": "stored",
      "reviewer": "审核员",
      "decidedAt": "2026-10-13T01:23:45",
      "gates": { "definition": true, "script": true },
      "definition": { "precondition": "…", "steps": [ /* … */ ], "overallExpect": "…", "cleanup": "…", "ai": { /* … */ } },
      "script": null
    }
  ]
}
```

---

### 3.3 GET /api/review/state — 决策表 / 门禁表 / 审计日志

**请求参数**：`?v=<版本>`（可选，同 §3.2）

**响应字段**（200）：

| 字段 | 类型 | 说明 |
|---|---|---|
| version | string | 本状态归属版本 |
| cases | object | 决策表：`{用例ID: {status, reason, note, reviewer, decidedAt}}` |
| gates | object | 门禁表：`{用例ID: {definition: bool, script: bool}}` |
| audit | Audit[] | 审计日志尾部 **80** 条（时间升序，含全部版本） |

**Audit 条目结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| time | string | ISO 时间（秒级） |
| version | string | 该操作归属版本 |
| actor | string | 操作人（=请求的 reviewer） |
| action | string | `gate-view` · `approve` · `reject` |
| caseId | string | 用例编号 |
| detail | string | 人类可读明细（如「驳回（定义错误）；备注…」） |

---

### 3.4 POST /api/review/gate — 门禁审阅留痕

记录「该审核人已查看该用例的该门内容」。重复调用无害（幂等置 true）。

**请求体**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| caseId | string | ✓ | 须存在于目标版本用例库 | 用例编号，如 `login-LOG-001` |
| gate | string | ✓ | 枚举：`definition` · `script` | 门一=用例定义，门二=编译脚本 |
| reviewer | string | ✓ | 非空 | 操作人 |
| version | string | 否 | 须为真实存在的版本 | 缺省=当前版本 |

**成功响应**（200）：

```json
{ "ok": true, "gates": { "definition": true, "script": false } }
```

**错误**：

| 状态 | 场景 |
|---|---|
| 400 | `reviewer` 缺失 / `caseId` 不存在或 `gate` 非法（消息 `caseId/gate 非法`） |

---

### 3.5 POST /api/review/decision — 单条裁决

**请求体**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| caseId | string | ✓ | 须存在 | 用例编号 |
| pass | bool | ✓ | — | `true`=入库（stored），`false`=驳回（rejected） |
| reason | string | 驳回时建议填 | UI 枚举：`定义错误` · `步骤不可执行` · `预期错误` · `数据标签缺失` | 驳回原因（服务端不校验，前端 UI 强制） |
| note | string | 否 | ≤200 字 | 备注 |
| reviewer | string | ✓ | 非空 | 操作人 |
| version | string | 否 | 真实版本号 | 缺省=当前版本 |

**业务规则**：
- `pass=true` 时服务端校验两道门：任一门未留痕 → **409**（先调 §3.4）
- 重复裁决允许，**最后一次为准**（审计仍逐条留痕）

**成功响应**（200）：

```json
{ "ok": true, "decided": ["login-LOG-001"], "status": "stored" }
```

**错误响应**（409 示例）：

```json
{ "error": "两道门未审阅完毕: ['login-LOG-002']" }
```

---

### 3.6 POST /api/review/batch — 批量裁决

**请求体**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| caseIds | string[] | ✓ | 每项须存在；**全部同 `batch`** | 用例编号数组 |
| pass / reason / note / reviewer / version | — | 同 §3.5 | — | 对每条用例生效 |

**业务规则**：
- 跨批次 → **409**（`批量仅支持同批次（当前 N 个批次）`）
- `pass=true` 时逐条校验两道门，任一条未过 → **409** 整批拒绝（不部分生效）

**成功响应**（200）：

```json
{ "ok": true, "decided": ["login-LOG-002","login-LOG-003"], "status": "rejected" }
```

---

### 3.7 站点其它只读端点（简表）

以下均支持 `?v=<版本>` 按版本取数：

| 端点 | 返回 |
|---|---|
| GET `/api/version` | 版本元数据（badge：源码仓、APK、数据轮次、构建日期） |
| GET `/api/modules` | 模块清单 |
| GET `/api/feature/{id}` | 功能点详情（如 `M02-F09`；不存在→404） |
| GET `/api/zone/{id}` | 功能区状态矩阵（不存在→404） |
| GET `/api/defects` · `/api/changelog` | 缺陷候选 · 变更记录 |
| GET `/data/versions/{v}/…` | 版本数据与证据资产透传（JSON 禁缓存，媒体缓存 1h + Range） |

---

## 4. 错误码参考

所有错误返回统一结构：`{"error": "人类可读消息"}`（HTTP 状态码语义化）。

| HTTP | 触发场景 | 处置 |
|---|---|---|
| 400 | body 非 JSON / 超 1MB / 字段缺失非法（reviewer 空、caseId 不存在、gate 非法） | 修正请求后重试 |
| 404 | 路径错误；`/api/feature/{id}`、`/api/zone/{id}` 编号不存在 | 核对路径与编号 |
| 409 | 业务冲突：两道门未审阅即 pass；跨批次批量 | 按消息补门阅 / 拆分批次 |

> 注：409 的 `error` 消息含具体未过门用例 ID 列表（截取前 5 条），可直接驱动工作流回退。
> 浏览器跨源（CORS）调用一律被拦——错误表现为浏览器 Console 报 CORS，而非本表状态码。

---

## 5. 持久化布局（服务端）

| 文件 | 内容 | 生命周期 |
|---|---|---|
| `data/review/decisions-<版本>.json` | **按版本隔离**的决策表 + 门禁表（原子写） | 独立于编译产物，重编译不丢；版本间互不干扰 |
| `data/review/decisions.json` → `decisions-legacy.bak.json` | v1.0 旧单文件，首次访问自动按用例归属拆分迁移并备份 | 只读遗物 |
| `data/review/audit.jsonl` | 审计日志（追加式，条目含 `version`） | 永久，不可撤销 |
| `data/versions/{v}/cases.json` | 编译产物（构建时合并对应版本 decisions） | 随编译刷新 |

---

## 6. 数据模型（0.3.0 · schema `cases/0.3`）

### 6.1 TestCase（核心字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 编号终身制（0.3.0 为 `<域>-<系列>-<序号>`，如 `login-LOG-001`），跨版本稳定 |
| formerId | string | 旧编号映射（如 `M13-NEG-010`），0.2.0 → 0.3.0 迁移溯源 |
| title | string | 中文题名 |
| featureId | string \| number | 关联功能点（如 `M02-F01`）；个别历史数据为数值型 pit 编号（如 `14.4`） |
| pitId | string | 关联缺陷坑位（如 `M13-P18（关联 M13-P22）`） |
| priority / level | string | `P0-P2` · `L1-L3` |
| batch | string | 批次（同批次约束的判定依据） |
| channel | string | `positive` · `negative` |
| dimension | string | 用例维度（如 `边界`） |
| dataLabel | string | 固定 `TEST-SYNTHETIC`（合成数据标签） |
| baseline | string | 考古基线（如 `MAP-SWAN-002（5.3.2-b34 = App dev@deb9be6 / …）`） |
| status | string | `pending` → `stored` / `rejected`（服务端裁决写入） |
| moduleL1 / moduleL2 | string | 模块两级归属 |
| definition | object | 门一：`precondition` · `steps[]`（no/action/locator/data/expect）· `overallExpect` · `cleanup` · `ai` 溯源 |
| script | object \| **null** | 门二：`compiler`/`hash`/`file`/`code`；**可为 null**（未编译挂载——0.3.0 当前 1060 条全部待挂载） |
| submitter / submitTime / estimate / sla | — | 提交人 · 时间 · 预估 · 时效 |
| gates | object | 两道门留痕（服务端合并返回） |
| reason / note / reviewer / decidedAt | string | 裁决产物（服务端合并返回） |

> 与 v1.0 文档差异：移除 `deadlineHours`/`titleEn`（0.3.0 不再输出）；新增 `baseline`/`pitId`/`formerId`/`dimension`/`dataLabel`；`script` 由必为对象改为**可空**。

---

## 7. 集成注意事项

1. **调用顺序**：`gate ×2 → decision`。跳步会 409；前端工作台已内置该流，外部集成需自行遵守。
2. **幂等性**：`gate` 幂等；`decision` 以最后一次为准（审计全留痕）。
3. **显式带版本**：POST body 建议始终携带 `version`——缺省语义是"当前版本"，站点切换版本指针的瞬间可能写串轮次。
4. **CORS**：未开放跨源。.229 若做浏览器页面直调本 API，需同源反代或走服务端中转；脚本/后端调用不受影响。
5. **健康检查**：`HEAD /` 返回 200 与 Content-Length，适合探活；不要用 GET 拉首页做探活（浪费带宽）。
6. **轮询**：状态变化可通过 `GET /api/review/state` 轮询；建议 ≥5s 间隔，接口无限流但请克制。
7. **服务启动**：默认只绑 `127.0.0.1`；局域网公开需显式 `--host 0.0.0.0`（.167 生产即此配置，systemd 托管）。
8. **示例主机**：文档统一用 `192.168.12.167:8090`；本机调试替换为 `127.0.0.1:8090`。
