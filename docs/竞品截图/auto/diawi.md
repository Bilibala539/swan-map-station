# 竞品采集：Diawi

> 采集 2026-09-20，design_shot.py（viewport.png / fullpage.png / styles.json 同目录）。
> 样本：diawi.com 首页（其应用页为每次上传生成的一次性短链，无固定公开列表页）。
> 关注点：分发流程的极简形态（反模式样本：无版本目录）。

## 页面结构

- 标题"Development & In-house Apps Wireless Installation"，三步说明：
  1. Upload the iOS or Android application.
  2. Send the link to your testers, clients, friends or even use it yourself.
  3. **Open the link in the browser on the device and click on install.**
  - 尾注 "It works for iOS and Android."
- 主体：大上传拖拽框（Upload your app）+ 两个勾选项（测试者可按 UDID 查找 / 展示图标到 Wall of apps）+ More options。
- 右栏 Stats：Apps uploaded / Apps installed 大数字计数（since november 2010）。
- 视觉极简：黑白灰 + 单一品牌色，无卡片堆叠、无渐变，正文衬线感弱、密度低。

## 四项关注点比对

| 关注点 | Diawi 的做法 |
|---|---|
| 下载按钮形态 | 无固定按钮——短链落地页内"Install"按钮（链接墙内，首页不可见） |
| 二维码呈现 | 上传后生成短链+二维码（流程内产物，不在首页） |
| 正式/测试环境分类 | 无——每次上传都是独立一次性链接，**无版本目录、无历史追溯** |
| 版本列表信息密度 | 无列表（反模式样本） |

## 可借鉴点

1. **"在设备浏览器打开链接并点击安装"的三步文案结构**——把平台差异（iOS/Android 行为不同）说清的最短方式。我们下包页 iOS 说明文案的组织可参考，但我们的场景一句话够用（单平台产物），不照搬三步。
2. Apps installed 全局计数佐证"下载数是分发场景核心指标"——我们 P1 的下载次数统计列维持。

## 对本平台下包页的建议

- **反模式警示**：一次性链接无目录、无历史，装过的包找不回——正好反证我们"apks 表为目录、页面按包列出"的定位正确。
- 无设计改动建议；下载统计列（P1）获一个业界佐证。
