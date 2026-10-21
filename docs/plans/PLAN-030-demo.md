# PLAN 0.3.0 demo：好看 · 易懂 · 不割裂

> 状态：执行中（2026-09-20 批准）| 责任侧：王端（本地开发→scp 推回）| 于淼侧：验收+部署
> 上游：REQUIREMENTS.md（0.2.0 基线）| 数据流：tools/build_data.py → data/versions/*.json → 前端 api.ts

## 目标

从"功能可用"升级为：**人类看着舒服**（视觉）· **知道都是什么**（信息架构）· **用着顺畅**（交互）· **整体不割裂**（一致性）。整合墨刀「测试用例人审工作台」为站内新功能。

## 一致性策略（核心决策）

**设计系统先行**：先立 DesignSystem 单一真相源（tokens + 组件模式 + 交互模式），后动任何页面。墨刀 DEMO 不 iframe 嵌入——数据模型化 + 主站设计系统重写同化。顺序错了必然再割裂。

## 对象建模

- **DesignSystem**（新建）：tokens.css + 组件模式 + 交互模式（过渡/反馈/空态），唯一消费入口
- **Page**（改造）：Dashboard / Workbench / **TestCaseReview**（新增）——只消费 DesignSystem
- **TestCaseReviewData**（新增）：两道门审核数据，进 build_data 编译管线
- 不动：编号体系（R5）、数据 JSON API（R8.7）、深链语义

## Converged 判据（全过才算完成）

1. ✚ 无头浏览器全页自测截图通过（R6.5 用户训诫）
2. ✚ R4.1–R4.4 交互纪律回归清单全过
3. ✚ 一致性硬判据：TestCaseReview 与主站引用同一套 CSS custom properties（grep 验证）
4. ✚ 深链语义不变 + 数据 API 零破坏（R8.7）
5. ✚ 两道门审核流程站内可完整操作

## 开源选型记录（2026-09-20）

- **anime.js**：按需引入，唯一新依赖（动效）
- **React Bits / Aceternity / Uiverse**：抄交互模式不引依赖（Aceternity 拖 framer-motion 全家桶，违反 R6 自包含）
- **motionsites / Showreel Design**：灵感参照
- **DESIGN.md 资产库**（D:\tangoWord\默认工作空间\awesome-design-md，74 份）：T1 对齐选型用
- R8.1 裁决变更：维持"不引组件库"，放开 anime.js 单项（待用户最终批）

## 任务分解

- [x] T1 设计语言选型：素材组装清单（11 素材×页面映射，含改造方式与依赖剥离原则）已批（2026-09-20）；基调=现有暗色骨架+靛蓝/紫 accent，Linear 做密度参照
- [x] T2 信息架构 PM 审：页面方向规范入附录（page map+direction 约束）；Workbench 重排控制台+画布；Dashboard 联动主视图——用户逐轮验收通过
- [x] T3 DesignSystem 落地（2026-09-20）：tokens.css 换色三处（#818cf8/#a78bfa 暗亮双主题）；motion.css 动效层（shimmer/glow-spin/striping/reveal/pulse）；ds/ 组件 8 件（CountUp·GlassCard·GlowBorder·SpotlightCard·ProgressBar·Stepper·Toast·useReveal）+ index 统一出口；StyleGuide 页（深链 ?view=styleguide）作活文档+自测靶；animejs 4.5.0 引入。build 过（105 模块）；自测证据 qa-shots/t3-styleguide.png + t3-dashboard-recolored.png（token 生效/7 组件渲染/数据 API 全 200）
- [x] T4 主站页面重皮：Dashboard 七轮迭代（素材→液态玻璃→全站玻璃系统→双主题规格→字体/过场/滚动条→联动视图→AI 背景图）；Workbench 控制台+画布+树折叠；GraphView 两轮重做（d3-force+treemap+邻接高亮）；全站边缘态组件
- [x] T5 墨刀 DEMO 数据模型化：cases.json（12 条·两批次）；**全要素对齐用户下载版 home.zip**：channel 正/异常向、sla 三态（normal/urgent/overdue）、level L1-L3、moduleL1/L2 两级模块、submitter 带 PRD 来源
- [x] T6 TestCaseReview 页同化：控制台+队列布局、两道门 Stepper、**筛选三档（全部/即将超时/已超时，DEMO 同款）**、批量（同批次同原因+确认声明）、驳回弹层（枚举+备注≥10字）、审计日志、Toast、自动下一条；队列行全要素（P/SLA/L/正负向/门）；深链 ?view=cases
- [x] T7 全量回归 + Converged（2026-09-20 判定通过）：①截图全程 17+ 张 ②R4.1 开关常驻实测/R4.2 原因说明/R4.3 重置逻辑保留/R4.4 死路由链路 ③一致性 grep：TestCaseReview 硬编码色 0、token 引用 18 ④深链语义+API 全 200（含 cases.json）⑤两道门完整操作链实测（筛选/通过/批量/驳回三重校验）

## 里程碑：**Converged（2026-09-20）**——待用户终验 + scp 推回于淼机

## 写操作自主性标注

全部 staged：远程推送前每笔代码改动呈 diff 给用户批。

## 附录：页面方向规范（MotionSites 格式，T4 各页执行依据）

### Dashboard 汇报首屏（2026-09-20 定稿）

**Category:** Internal Data Workstation · **Type:** view

Dashboard is a view direction for 汇报场景（外行 30 秒看懂）who need 结论先行的覆盖故事 with production-minded density.

首屏：结论 Hero（大数字 + 状态分布条）+ 唯一下一步（进入工作台）。
Page map: 结论 Hero / KPI ×4 / 模块排行 / 风险在册 / 可信度行。
CTA: 「进入功能工作台 →」。Headline: 「{ok}/{total} 功能点已考古，覆盖率 {coverage}%」。

Direction:
- 数字先立住，动效只做入场与反馈（CountUp 一次，不循环）
- 玻璃/聚光材质用于 KPI 层，风险区用光边框警示——材质有语义，不做装饰
- 密度优先：模块排行一屏可读，不牺牲行高
视觉标签: dark / glass / spotlight / glow-accent / bento

## 交接段（每次会话结束更新）

- 2026-09-20（十二）：**全真实用例库上线**（用户：假的不要，记得有两三百条）——于淼机盘点：hivi-app-e2e/tests 33 文件 189 个真实 pytest 函数 + uiautotest 22 + 文档 9 ≈ 220 条（与用户记忆吻合）。cases.json 升级 cases/0.3：定义门 9（文档真实）+ 脚本门 189（全部真实函数；3 核心文件 login/wifi_device_flow/player_page 含 17 条真实函数体，其余真实签名+文件指引）；批次=测试文件；moduleL1/L2 按文件名映射真实模块结构；优先级/通道为启发式演示映射（note 已声明）。309KB。实测 198 卡渲染、TS 条目标题/来源/门二真实函数体正确。截图 t22。数据源文件存 D:/tmp/{all-tests.txt,e2e-*.py}。
- 2026-09-20（十一）：门内完整结构落地（用户点出丢失项）——门一：结构化字段 grid×8（编号/一级/二级模块/优先级/级别/通道/批次/预计执行）+ 用例步骤表（5 列：步骤号/业务动作/目标定位/测试数据/步骤预期，AUTH-002 五步真实 Semantics 锚点）+ 总体预期 + AI 溯源信息（来源 PRD 节/知识卡×3/promptVersion/模型/生成时间/置信度 86%/人工补充备注）+ 审核动作指引三条（DEMO 原文）；门二：审核要点三 check（断言完整性/定位唯一性/数据隔离——数据隔离按通道动态 ✓/✕）+ 编译器/source_hash 头 + 真实 pytest 代码。实测 12 字段/5 行×5 列步骤表/溯源块/指引/三 check/source_hash 全渲染，截图 t21。
- 2026-09-20（十）：**两道门逻辑修正**（用户判"没看懂逻辑关系"，确实）——旧实现把 gate 当用例归属（这条属定义门/那条属脚本门）=错误。解剖 DEMO 源码 state 定案：`activeTab`（审核会话级门 Tab）+`viewed[id]={definition,script}`（每条用例两门各自查看状态）+approveBtn 锁定逻辑（两门全看才可提交）。重写为：每条用例过两门（门一=用例定义审阅/门二=编译脚本审阅），详情区双 Tab 切换即审阅，Stepper 门一/门二/入库，提交按钮被两门锁（DEMO 原文案"门一已审阅待完成门二"），批量文案对齐"确认两道门均已通过并批量入库"。真实数据升级双内容视图（definition=文档步骤/通过标准，script=真实 pytest 函数体）。实测：初始锁+提示→点门二解锁→提交入库 Toast+审计+自动下一条。截图 t20。**外环提炼：禁止从字段名猜领域语义，必须读 demo 源码的状态机（state+disabled 逻辑是语义契约）**。
- 2026-09-20（九）：社区图**换形式**（用户判「立体新冠」，密恐非数量问题）——学术定论：Shneiderman《The Eye-Have-It》：node-link 适合 <50 节点，大图用空间填充。社区模式整个改 **treemap**（d3-hierarchy squarified，面积=文件数，div 渲染全走 token，hover 提亮+入场 stagger，点击出详情卡）——零连线零飘动零密恐；模块级/导航图保留力导向（合理规模）。fitView 改**只缩小不放大**（k≤1：首屏永远全貌，放大交用户滚轮）。实测：60 cells 全可见、33 带标签、填充率 96%、canvas 已撤。截图 t14-comm-treemap.png。遗留：T5/T6/T7。
- 2026-09-20（八）：图谱细节五项整改（用户批：模式切换丢功能/节点出视野/密恐/详情看不懂/hover 全灭+边标签丢失）——①工具条改**分段单选**（模块级/导航/社区），「展开功能点」回归为模块级子开关，「展开全部社区」为社区子开关（默认 top60 治密恐）②**zoom-to-fit**：布局稳定后自动包围盒适配居中（四象限实测 23/24/25/29%——修复 fitView 在 resize 前执行的死区 bug）③社区配色按规模 accent→muted 插值（结构化不密不花）④hover 平衡：无关节点 0.1→0.28、无关边 0.05→0.12（focus 突出但 context 可读）⑤**恢复 R8.7 丢失的 NavEdge.label/note**（重写时弄丢=违规）：悬浮卡带出边跳转说明（→ 目标页 (动作)）；社区详情浮卡人性化（代码聚类说明+代表文件+总数）。截图 t13-*。**教训入档：重写组件前先盘点现有功能清单（R8.7 回归清单前置）**。遗留：T5/T6/T7。
- 2026-09-20（七）：**GraphView 全重做**（开源选型核验：d3-force 采用/force-graph·sigma·cytoscape 否决——渲染难 token 化或过重）。物理=d3-force（manyBody+link 弹簧+collide 防重叠+alpha 冷却——治手写物理乱飞）；节点形态=模块圆角方块（label 内嵌）/功能点圆/语义色保留；交互=hover 高亮邻居+连通边其余淡出（focus+context）、拖节点（fx/fy）、拖背景平移、滚轮缩放（0.3-4x）；三模式保留（模块级/导航图/745 社区图——大图预热 300 tick 静态布局防耗电）。comm 详情浮卡改 top 文件列表（files 是 number）。build 过，模块/导航两模式截图 t12-graph-*。遗留：T5 墨刀两道门、T6 TestCaseReview、T7 全量回归+Converged。
- 2026-09-20（六）：用户四点批评（Workbench 乱/无整齐性/边缘态丑、图谱丑蠢、树面板挤占、整体思维复盘）→ **A.DS 边缘态**：SmartImage 三态框（加载骨架/损坏占位/空态，全站图片标准）+ ds-chipgrid 等宽网格（治长短不齐）**B.Workbench 重排**：状态矩阵 Tab 六卡纵队→「控制台+画布」单卡（左 280px 控制列：采证页/功能区/状态三层选择流+图例；右画布：SmartImage+bounds+详情；R4.1/R4.2/键盘切换全保留）**C.树面板折叠**：«»钮 300↔40px，工作区释放。机器验证：27 chip 全 237px 等宽、空态占位卡生效、折叠 300→40。**下一件：GraphView 重做**（力导向布局+hover 高亮邻居+入场动画+模块分色——设计规范已定：Obsidian graph/Cosmograph 模式）。复盘结论入档：完成=系统所有状态成立，非用户点名点改完。
- 2026-09-20（五）：视觉层四轮收口（素材→材质→系统→底座：夜空/晴空背景图改名破缓存 bg-night/bg-day、Inter variable 字体、景深对焦主题切换、近隐形滚动条、全站卡片聚光、双主题规格 token --grad-data/--spot-glow）；**联动主视图落地**：单击排行行选中→风险卡切模块视图（死路由/BUG 按模块过滤）+「进入本模块工作台」，双击行直进工作台，hover 风险行高亮关联模块行（.rank-row.selected/.linked）。机器验证点击联动全通，hover 需真人验证。外环提炼：**换媒体文件必须换文件名**（R6.2 媒体缓存纪律推论，同名文件浏览器强缓存不更新）。遗留：T5 墨刀 DEMO 数据模型化、T6 TestCaseReview 页、T7 全量回归+Converged 判定。
- 2026-09-20（四）：用户五点批评（背景只在一块/毛玻璃没质感/光框傻/进度条杂色/只改一页+无整体思维）→ **全站视觉系统四层重构**：①全局背景层 .ds-global-bg（AI 双图+氛围光+噪点 fixed 垫整页）②材质全站化（--panel 半透明化 + .card/.topbar/.left/.right 全部 blur(18px) 毛玻璃——透全局背景）③色系收敛（完成度条统一 accent→accent-2 渐变+透明度分级，partial/low 多彩类废，tile-bar 孤儿删；风险卡 GlowBorder 拆除换 danger 顶边条）④动效统一（ds-rank-fill 填充动画全站完成度条）。验证：dashboard/workbench/evidence 三页截图 t5-*，blur/背景/动画机器验证全过。**方法论修正**：视觉改动一律从 token/背景层定义处下手全站生效，禁止逐块贴素材。
- 2026-09-20（三）：T4 Dashboard 重皮三轮迭代完成——①DS 素材接入（Glass/Spotlight/CountUp/Glow）②Slate 式液态玻璃+氛围底+逐字入场（SplitLine 组件+ds-liquid 原版 CSS+白色系细字重）③亮色主题适配+AI 背景图（火山 Ark doubao-seedream 生成 hero-dark/light.png 2K，双图随主题切换，Ken Burns 微呼吸，prefers-reduced-motion 尊重）。数值验收：暗图均值 16/255、亮图 242/255。截图 qa-shots/t4~t4d 五张。**遗留**：联动主视图重构（排行+风险合并为联动视图）待做；node build 偶发 OOM 需 NODE_OPTIONS=--max-old-space-size=4096；ARK key 在会话中临时使用未入库。
- 2026-09-20（二）：T1/T3 完成。本地依赖已装齐（lock 内网源 URL 已镜像化，原版备份 package-lock.json.inner；animejs 已入 dependencies）；本地服务起法 `python server/serve.py --port 8090`（会话内可能还在跑）；样式指南入口 http://127.0.0.1:8090/?view=styleguide。**下一步 T4**：Dashboard/Workbench 接入 DS 组件（CountUp→完成度数字、GlowBorder→选中功能区、Spotlight→卡片）。注意事项：node_modules 不入库不推送；推送时恢复 inner 版 lock 或让于淼侧重装；R4 交互纪律未动（只改了颜色 token，无逻辑改动）。
- 2026-09-20（一）：PLAN 批准；项目副本拉至本地（排除 node_modules/data.assets）；墨刀 DEMO 存 D:\tmp\modao-testcase-demo.html（task 6aae57c31795c0ce4a1afee2）；于淼机 SSH swan@100.112.214.105 通。
