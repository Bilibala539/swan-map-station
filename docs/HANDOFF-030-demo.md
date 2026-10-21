# 交接文档：swan-map-web 0.3.0 demo（给于淼侧阅读）

> 写给：于淼，或于淼电脑上的 Claude 会话。读完本文即可接手，无需其他上下文。
> 写于 2026-09-20，王端侧。

## 一、这个项目现在是什么

Swan 功能地图工作站（swan-map-web），版本 0.2.0 Demo：
- **技术栈**：React 18 + Vite + TS 前端（`frontend/`），纯 Python 标准库静态服务（`server/serve.py`，8090 端口）
- **数据流**：`tools/build_data.py` 把源文档（功能地图.md、states-matrix.md、BUG 库、探索结果）编译成 `data/versions/v2/*.json`，前端 `api.ts` 拉取渲染。数据与产物分离，更新数据不用重建前端
- **已有页面**：Dashboard / Workbench（状态矩阵+语义树+跳转关系）/ GraphView / EvidenceLibrary / Defects / KnowledgeView / Changelog
- **权威需求文档**：`REQUIREMENTS.md`（R1-R8，含裁决记录），务必先读

## 二、这次要做什么（0.3.0 demo 目标）

用户原话痛点：前端太丑、不知道页面都是什么、交互差、**新功能加进去后风格与其他页面割裂**。

目标四件事：
1. **视觉**：好看——参照动效站（React Bits / Aceternity / Uiverse / motionsites / Showreel / anime.js）
2. **信息架构**：每页三问——给谁看？回答什么问题？看完下一步动作是什么？
3. **交互**：顺畅——统一过渡/反馈/空态模式
4. **不割裂（最重要）**：先立**设计系统单一真相源**（tokens.css + 组件模式 + 交互模式），所有页面包括新功能只消费它。墨刀 DEMO 不 iframe 嵌入，用主站设计系统重写同化

## 三、新功能：测试用例人审工作台

墨刀 AI 生成的「测试用例人审工作台 · 两道门审核」HTML demo（65KB，Tailwind 单文件），用户认可其产品设计。要做的：**数据模型化**（两道门审核流程的数据结构进 build_data 管线）+ **用主站设计系统重写**为站内页面 TestCaseReview。墨刀原件在王端 D:\tmp\modao-testcase-demo.html，在线预览：https://modao.cc/agent-py/workspace/6aae57c31795c0ce4a1afee2/review-workbench.html/home.html

## 四、执行模式与当前状态

**分工**：王端本地开发（本地副本 D:\tangoWord\默认工作空间\swan-map-web）→ scp 推回于淼机 F:\SwanApp-Toolchain\apps\swan-map-web → 于淼侧验收+部署。

**计划与进度**：看 `docs/plans/INDEX.md` → `PLAN-030-demo.md`（含任务分解、判据、交接段）。每次会话结束更新交接段。

**已完成**：项目副本已拉到王端本地（排除 node_modules 和 data/assets 大文件）；PLAN 已批准；T1 待启动。

## 五、红线约束（违反即回滚）

1. **R6.5**：每轮 UI 改动必须无头浏览器自测后再交付（用户训诫）
2. **R4.1-R4.4**：交互纪律回归清单（功能点选中→默认本模块采证页；"全部采证页"开关常驻；切换功能点重置页面选择；无采证显示原因）
3. **R8.7 不变式**：深链参数语义不变；数据 JSON API 零破坏
4. **R6.1 迁移安全**：整站自包含纯相对路径（唯一新依赖 anime.js 需打包进产物）
5. 编号终身制（R5.1，编译器注册表保证）

## 六、验收判据（Converged，全过才算完成）

见 PLAN-030-demo.md「Converged 判据」节——五条机器可验证判据，含一致性硬判据（新页面与主站共享同一套 CSS custom properties）。

## 七、部署速查

```powershell
# 本地跑（于淼机）
cd F:\SwanApp-Toolchain\apps\swan-map-web\frontend ; npm install ; npm run build ; cd ..
python server/serve.py --host 0.0.0.0 --port 8090
# 访问 http://127.0.0.1:8090
```

详细部署：DEPLOY.md。审查对照：review-0.2.0.md（上轮审查范式可复用）。
