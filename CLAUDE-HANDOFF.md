# 给于淼电脑上的 Claude：swan-map-web 0.3.0 交付说明

> 你好。这是王端侧完成的 0.3.0「好看·易懂·不割裂」demo 全量交付。读完本文件即可接手。
> 完整计划与十轮迭代记录：`docs/plans/INDEX.md` → `PLAN-030-demo.md`（含 Converged 判据全部通过记录）。
> 给人看的交接：`docs/HANDOFF-030-demo.md`。

## 一、这轮交付了什么

1. **全站视觉系统四层**：token（`src/styles/tokens.css`）→ 动效模式（`src/styles/motion.css`）→ DS 组件（`src/ds/`，12 件）→ 页面方向规范（PLAN 附录）。双主题（暗=夜空 AI 背景图 / 亮=晴空），主题切换带「景深对焦」过场。
2. **页面重做**：Dashboard（液态玻璃 hero+逐字入场+联动主视图——点模块行风险卡切该模块视图）、Workbench（控制台+画布单卡布局+树面板折叠«»）、图谱（d3-force 力导向 ×2 模式 + treemap 社区图×邻接高亮）。
3. **新功能页：用例人审（`?view=cases`）**——墨刀「测试用例人审工作台·两道门」完整同化：
   - **两道门状态机（关键逻辑，勿改坏）**：每条用例过两门（门一=用例定义审阅 / 门二=编译脚本审阅），切 Tab 即审阅记录 `viewed[id]`；**「提交审核结论」按钮被两门锁**（都看过才解锁）→ 提交=入库；驳回任意时刻。
   - 门内结构：门一=8 字段结构化块+5 列步骤表（步骤号/业务动作/目标定位/测试数据/步骤预期）+总体预期+AI 溯源（PRD/知识卡/置信度）+审核动作指引三条；门二=三审核要点 check（断言完整性/定位唯一性/数据隔离——数据隔离按通道动态）+真实脚本。
   - 批量=同批次同原因+确认声明；审计日志不可撤销。
4. **真实数据 198 条**：定义门 9（你的 core/wifi_test_cases.md）+ 脚本门 189（hivi-app-e2e/tests 全部真实 pytest 函数，33 文件全量拉取，函数体真实）。数据在 `data/versions/0.2.0/cases.json`，来源与真实性分级见其 `note` 字段。

## 二、部署（在你机器上）

```powershell
cd F:\SwanApp-Toolchain\apps\swan-map-web\frontend
npm install          # 新依赖：animejs / d3-force / d3-hierarchy（lock 已按内网源写好）
npm run build
cd ..
python server\serve.py --host 0.0.0.0 --port 8090
# 验证：http://127.0.0.1:8090/ 与 http://127.0.0.1:8090/?view=cases
```

注意：build 若报 node OOM，先 `$env:NODE_OPTIONS="--max-old-space-size=4096"`。

## 三、代码地图（改哪里找哪里）

| 要改什么 | 文件 |
|---|---|
| 颜色/字阶/间距/主题 | `frontend/src/styles/tokens.css`（双主题 token：--grad-data/--spot-glow 按主题整套换） |
| 动效/玻璃材质/滚动条/两门内容样式 | `frontend/src/styles/motion.css` |
| 素材组件（CountUp/Stepper/Toast/SmartImage…） | `frontend/src/ds/`（页面只从这里拿，禁止绕过直写样式值） |
| 两道门页 | `frontend/src/components/TestCaseReview.tsx` |
| 用例数据 | `data/versions/0.2.0/cases.json`（真实管线接入后由 tools/build_data.py 编译生成） |
| 新样式指南/自测靶 | `?view=styleguide` |

## 四、红线（REQUIREMENTS.md 摘要，违反即回滚）

- **R4.1-R4.4 交互纪律**：功能点选中→默认本模块采证页；「本模块/全部」开关常驻不可消失；切换功能点重置页面选择；无采证模块显示原因说明。
- **R6.5**：每轮 UI 改动必须无头浏览器自测后再交付（qa-shots/ 内有 40+ 张过程证据可作范式）。
- **R8.7 不变式**：深链参数语义不变（?view/module/feature/tab/page/mode/theme）；数据 JSON API 零破坏。
- **R8.1 变更**：维持「不引组件库」，仅放开 animejs/d3-force/d3-hierarchy 三个单项（均自包含打包，无 CDN）。
- 媒体文件更新必须**换文件名**（bg-night.png 而非 hero-dark.png 就是这个原因——浏览器强缓存）。

## 五、建议的下一步

1. 用例数据接真实管线：`tools/build_data.py` 加 cases 源（当前 json 为快照，人审状态是内存级 demo 态）。
2. 审计日志落盘（当前刷新重置）。
3. 版本对比（R2.2 二期挂账）与墨刀「版本对比可视化」原型对接。
4. PLAN-030-demo.md 交接段有完整遗留清单。

## 六、过程重要教训（防止重蹈）

- 两道门语义来自墨刀 DEMO 源码 state（activeTab+viewed+按钮锁定），**不是**用例归属属性——对接 DEMO 先读源码状态机再建模。
- 大图（745 社区）不用力导向圆点云（密恐），用 treemap 空间填充——形式=数据规模的函数。
- 视觉改动一律从 token/定义处下手全站生效，禁止逐块贴素材。

— 王端侧 Claude，2026-09-20
