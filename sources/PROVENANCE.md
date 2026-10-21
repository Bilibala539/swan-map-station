# sources/ —— 编译器输入源文件（非派生物）

## cases.source.json → 编译产物 cases.json

| 项 | 值 |
|---|---|
| schema | cases/0.3 |
| 条数 | 198（定义门 9 + 脚本门 189） |
| 产生日期 | 2026-09-20（PLAN-030-demo「全真实用例库上线」） |
| 消费方 | 前端 用例人审视图（TestCaseReview.tsx，`loadCases`） |

### 来源（两道门）

- **定义门 9 条**：于淼用例文档 `core/wifi_test_cases.md`
- **脚本门 189 条**：`hivi-app-e2e/tests/` 全部 33 个测试文件的真实 pytest 函数（2026-09-20 于淼机拉取；函数体真实，超长截断处标注原文位置）

### ⚠️ 血缘警示

中间产物曾存于 `D:/tmp/{all-tests.txt, e2e-*.py}`，**2026-09-20 复查时已散失**（tmp 目录被清）。
本机现存两份互为备份：`data/versions/0.2.0/cases.json`（编译产物）与 `delivery-030/`（0.3.0 交付快照），
加上本文件共三份。**本文件是管线内的唯一源**。

### 重建路径（若三份皆失）

1. `git.hivi.com` 拉取 `hivi-app-e2e` 仓（账号 @yumiao 可达）→ 重跑 pytest 函数枚举
2. 再访于淼机取用例文档
3. 优先级/通道字段为演示映射（启发式），重建时需按 cases.json note 声明的规则复现

### 纪律

- 修改用例数据 = 修改本文件 + 重跑 `python tools/build_data.py`（产物随版本目录更新）
- 本文件属于 F 盘工具链长期沉淀，禁止放入 tmp/缓存目录
