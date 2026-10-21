# Jenkins 原生界面评审（2.568.3 LTS 新版 UI）

- 采集:2026-09-22,主会话带认证双源交付——本目录八张实拍(dashboard/job-build/build-detail/console 各 viewport+full)+ jenkins-html/ 四页 HTML。评审以 PNG 为准,HTML 查交互细节(tooltip/aria/下拉模板)。
- 背景:平台 runs 已外链 Jenkins 日志(jk 按钮),流水线经 Jenkins 派发(build-176 执行 swan-audio-build)。本次评审回答两个问题——新版 Jenkins 有什么值得平台吸收;哪些继续外链。
- 先行交付:登录页分析见 auto/jenkins-login.md(2025 新版登录页,分栏渐变 hero)。

## 一、整体视觉语言(与 classic 时代判若两物)

- 纯白底+浅灰描边圆角卡(约 10px),全局无渐变(登录页 hero 是唯一品牌渐变位);链接用鲜明蓝;语义色:绿圈勾=成功、红圈叉=失败、蓝=进行中(配进度条)、灰=中性。状态一律"图标+可点链接",几乎无裸文字状态。
- 顶栏极简:左 serif 字标"Jenkins"+面包屑,右三图标(搜索/设置/头像,头像带红点通知);无一级导航堆砌。serif 品牌字与无衬线工作字分离。
- 控件普遍胶囊化:视图筛选"All +"、密度切换 S/M/L 分段、构建号 pill(#7/#2 浅灰蓝胶囊)。
- 侧栏=图标+文字的导航卡,active 项浅灰填充;危险项(Delete Project/Delete build)带 trash 图标但**不做红色**——破坏性只靠图标语义+确认,视觉降噪。

## 二、逐页观察

### 1. 仪表盘
- 布局:左窄侧栏(4 个全局入口)+左下两张可折叠小卡(Build Queue 空态一句"No builds in the queue."/Build Executor Status)+右侧主表。
- 主表列:S(末次构建状态)| W(天气=近期构建健康聚合)| Name(可排序,箭头明示)| Last Success | Last Failure | Last Duration | 行尾 play。
- **表头 tooltip 解释缩写**:S="Status of the last build",W="Weather report showing aggregated status of recent builds"——缩写列必配解释,零成本防误读。
- **Last Success / Last Failure 双列+构建号 pill**:成功与失败最近一次各占一列,直达对应构建——失败回查不用进 job 翻历史。
- 行尾 play 的 tooltip="Schedule a Build with parameters for X"——高频动作(触发)就地可达且明示是参数化触发。
- 执行器状态卡:节点 tooltip "1 of 1 executor busy";运行中构建=进度条+Cancel 按钮+#N 链接——"现在在跑什么、能否插队/终止"全局可见。
- **Icon S/M/L 密度切换**:官方把信息密度做成用户可选(L 默认)。与我们"单档紧凑"取舍相反(我们拍板过密度是设计决策不外包给用户)——记录差异,不跟。
- 页脚 REST API 链接:API 当一等公民露出。

### 2. job 页(swan-audio-build)
- 侧栏导航卡:Status(active)/Changes/Workspace/Build with Parameters/Delete Project/Configure/Rename,每项配图标。
- **Builds 面板**:filter 输入框+按日期分组(Today/2026年9月21日)+每行状态图标/#N/相对时间/chevron 展开;运行中行内嵌进度条+取消。构建历史被组织成"可扫读的日历流"而非平铺表。
- **Permalinks 语义锚点清单**:Last build/Last stable/Last successful/Last failed/Last unsuccessful/Last completed,每项=语义名+构建号+相对时间,直达目标构建——"跳到最近一次失败"一步完成,不用筛选不用翻页。
- 描述区:多行文本展示态,"Edit description" ghost 右上——点按才进编辑,展示/编辑分离与我们 5.3 同款。
- RSS(全量/失败/changelog)藏面包屑 title,机器可订阅不占界面。

### 3. 构建详情(#6)
- 面包屑三级 Jenkins / swan-audio-build(带下拉菜单) / #6——层级可跳转。
- 标题="#6 (绝对时间)";右上双 ghost:Add description / **Keep this build forever**(防自动清理的钉住,放在最常达位置)。
- **meta 三要素右上角**:"Started by user admin"(用户可点)+ "Started 1 hr 9 min ago / Took 4 min 57 sec on build-176"(耗时+执行节点,节点可点)——触发者/耗时/节点不占主内容区。
- **Previous Build ← / Next Build →** 做进侧栏:串行排查时"上一发/下一发"导航极顺手。
- 空态"No changes."一行灰字配图标,无插画——与我们空态理念同向。
- 整页密度极低,大片留白:比 classic 更"消费级"。对我们的表单型工具页而言密度偏低,信息效率不如我们紧凑档——差异点,不是要抄的点。

### 4. 控制台(Console Output)
- 头部 Console h1+右侧 Download / Copy / **View as plain text**(切纯文本 consoleText)。
- **日志富文本化**:pre 等宽块内,用户名/节点名/URL 自动渲染成可点链接(截图中 admin、build-176、http://…:8080/api/apks 全部蓝链)——日志从死文本变成可导航结构;另有 ExpandableDetailsNote 可折叠详情块收纳异常类长输出。
- 顶栏搜索=**命令面板**(jenkins-command-palette dialog,Ctrl+K 式全局检索 job/构建/管理页)。

## 三、对本平台的建议

### 值得吸收(按价值排序,均已按"每家一两点"纪律裁量)
1. **Permalinks 语义锚点** → 流水线页签(6.3):配置卡或"最近执行"表上方加一行语义直达——"最近成功 #N · x 分钟前 / 最近失败 #N · x 小时前 / 最近执行"。一次查询的成本,解决失败回查最高频路径。已落规范 6.3 P1。
2. **会话间 Previous/Next 导航** → runs 详情抽屉(6.1):抽屉头部加一对导航链接,串行排查不回列表。已落规范 6.1 P1。
3. **日志/错误 pre 的 URL 链接化** → runs 详情 case 折叠面板与 6.6 失败摘要的 pre 块:URL 自动渲染为链接(等宽字体不变)。已落规范 6.1 P1。
4. **确认项(不新做)**:meta 三要素(触发者/耗时/节点)——runs 抽屉 meta 行已有设备+包体+开始时间+耗时,触发来源在 6.6 P1 已挂账,不用再加;Last Success/Failure 双列——runs 表列已满(≤7±2),且统计卡"最近一次状态"已承担,流水线侧由 Permalinks 承担,不加列。

### 继续外链,不吸收
- **控制台日志、参数化触发、配置管理**:Jenkins 是这三者的源头系统,平台侧复刻=重复建设;runs 抽屉 jk 按钮外链日志维持现状。
- **执行器状态卡/Build Queue**:调度视角 Jenkins 全局 dashboard 已承担,我们流水线页签"任务列表"已覆盖平台需要的那部分。
- **Weather 天气图标**:维持原裁决(七)"失败红点+通过率上色"的轻量版已够。
- **命令面板、S/M/L 密度切换、RSS**:单页应用体量不需要;密度不外包;无订阅场景。

## 四、裁决结论

平台与 Jenkins 的分工不变:**平台管"结果扫描与语义定位",Jenkins 管"构建过程与全量细节"**。本次只吸收"语义锚点/轻导航"类零依赖交互(上面 1-3,全是 P1 顺路改),其余继续外链。新版 UI 的低密度消费风不建议整体跟——我们 3.2 紧凑档是拍板过的取舍。
