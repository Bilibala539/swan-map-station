// R8.2 Dashboard 汇报首屏：hero 结论 + KPI 磁贴 + 模块排行 + 风险卡 + 可信度行
// 0.3.0 demo 重皮：材质层升级（Glass/Spotlight/CountUp/Glow），数据逻辑与深链行为零改动
// 方向规范：docs/plans/PLAN-030-demo.md 附录（数字先立住；材质有语义；密度优先）
import { useState } from "react";
import type { Meta, MapData, MatrixData, DefectsData } from "../types";
import { IconGrid, IconBox, IconEye, IconDoc, IconShield, IconAlert, IconGraph } from "./Icons";
import { CountUp, SpotlightCard, ProgressBar, SplitLine, useReveal } from "../ds";

interface Props {
  meta: Meta;
  map: MapData;
  matrix: MatrixData;
  defects: DefectsData;
  onOpenWorkbench: () => void;
  onOpenDefects: () => void;
  onSelectModule: (moduleId: string) => void;
}

function statusSummary(map: MapData) {
  let ok = 0, warn = 0, dead = 0, other = 0;
  for (const f of map.features) {
    if (f.deadRoute) dead++;
    else if (f.status.includes("已考古")) ok++;
    else if (f.status.includes("未确认")) warn++;
    else other++;
  }
  return { ok, warn, dead, other };
}

export default function Dashboard({ meta, map, matrix, defects, onOpenWorkbench, onOpenDefects, onSelectModule }: Props) {
  const s = statusSummary(map);
  const zoneTotal = matrix.pages.reduce((a, p) => a + p.zones.length, 0);
  const stateTotal = matrix.pages.reduce(
    (a, p) => a + p.zones.reduce((b, z) => b + z.states.length, 0), 0);
  const deadRoutes = map.features.filter((f) => f.deadRoute);
  const coverage = Math.round((s.ok / map.features.length) * 100);

  // 模块完成度排行：已考古占比降序，全部列出（18 模块一屏可读）
  const byModule = map.modules.map((m) => {
    const feats = map.features.filter((f) => f.moduleId === m.id);
    const ok = feats.filter((f) => f.status.includes("已考古") && !f.deadRoute).length;
    return { id: m.id, name: m.name, total: feats.length, ok, pct: feats.length ? Math.round((ok / feats.length) * 100) : 0 };
  }).sort((a, b) => b.pct - a.pct || b.total - a.total);
  const strongest = byModule[0];
  const weakest = [...byModule].reverse().find((m) => m.total > 0 && m.pct < 100);

  // 状态分布条：主状态（已考古）用主色渐变（与完成度条同语言），其余状态语义色小段
  const dist = [
    { n: s.ok, c: "var(--grad-data)" },
    { n: s.warn, c: "var(--warn)" },
    { n: s.dead, c: "var(--danger)" },
    { n: s.other, c: "var(--state-empty)" },
  ];
  const heroReveal = useReveal<HTMLDivElement>();

  // 0.3.0 联动：选中模块 → 风险卡切换为模块视图；hover 风险行 → 高亮关联模块行（区域关系=数据关系）
  const [selMod, setSelMod] = useState<string | null>(null);
  const [linkedMods, setLinkedMods] = useState<string[]>([]);
  const modName = (id: string) => map.modules.find((m) => m.id === id)?.name ?? id;
  // BUG 候选的模块归属（module 字段宽松匹配 SW-M03/M03 两种形态）
  const bugsOf = (id: string) => {
    const short = id.replace("SW-", "");
    return defects.bugs.filter((b) => b.module && (b.module.includes(short) || b.module.includes(id)));
  };
  const deadOf = (id: string) => map.features.filter((f) => f.moduleId === id && f.deadRoute);
  const deadModIds = [...new Set(deadRoutes.map((f) => f.moduleId))];
  const bugModIds = [...new Set(defects.bugs.flatMap((b) => map.modules.filter((m) => b.module && (b.module.includes(m.id.replace("SW-", "")) || b.module.includes(m.id))).map((m) => m.id)))];
  const riskView = selMod ? { dead: deadOf(selMod).length, bugs: bugsOf(selMod).length } : { dead: deadRoutes.length, bugs: defects.bugs.length };

  return (
    <div className="bento fade-in">
      {/* Hero：结论先行——毛玻璃卡浮在全局背景层上（背景归 .ds-global-bg，此处只管内容） */}
      <div ref={heroReveal} className="ds-reveal col-12 card" style={{ padding: "30px 28px 24px" }}>
        <div className="ds-liquid" style={{ borderRadius: 99, display: "inline-flex", alignItems: "center", gap: 10, padding: "6px 16px", marginBottom: 16 }}>
            <span style={{ color: "var(--text-strong)", fontSize: "var(--fs-sm)" }}>Welcome · 功能地图工作站</span>
            <span style={{ color: "var(--muted)" }}>|</span>
            <span style={{ color: "var(--muted)", fontSize: "var(--fs-sm)", lineHeight: 1.8, textAlign: "left" }}>
              v{meta.version}{meta.demo ? " · Demo 验证版" : ""}
              {" · "}{meta.badge.appRepo}{" · "}{meta.badge.coreRepo}
              {" · APK "}{meta.badge.apk}
              <br />
              {"数据轮次 "}{meta.badge.taskId}{meta.buildDate ? ` · 编译于 ${meta.buildDate}` : ""}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 26, flexWrap: "wrap" }}>
            <div style={{ minWidth: 300, flex: 1 }}>
              <div className="ds-hero-title" style={{ fontSize: "clamp(24px, 2.8vw, 34px)" }}>
                <SplitLine text="全域功能 一图在册" />
                <div style={{ marginTop: "-0.1em", display: "flex", alignItems: "baseline", gap: 10 }}>
                  <span style={{ fontSize: "0.62em" }}>已考古</span>
                  <CountUp to={coverage} suffix="%" duration={1100} />
                </div>
              </div>
              <p className="ds-hero-sub" style={{ fontSize: "var(--fs-sm)", lineHeight: 1.6, margin: "8px 0 0", maxWidth: 560 }}>
                {s.ok}/{map.features.length} 功能点已考古取证 · {deadRoutes.length} 死路由 · {defects.bugs.length} 缺陷候选 · 编号终身可溯源。
              </p>
            </div>
            <div className="ds-liquid" style={{ borderRadius: "var(--radius-lg)", padding: "16px 20px", minWidth: 240 }}>
              <div className="muted" style={{ fontSize: "var(--fs-xs)", marginBottom: 8 }}>状态分布</div>
              <div className="ds-dist">
                {dist.map((d, i) => d.n > 0 && (
                  <div key={i} className="ds-dist-seg" title={`${d.n} 项`} style={{ flexGrow: d.n, background: d.c }} />
                ))}
              </div>
              <div style={{ display: "flex", gap: 14, marginTop: 10, fontSize: "var(--fs-xs)", color: "var(--muted)", flexWrap: "wrap" }}>
                <span style={{ color: "var(--accent)" }}>● 已考古 {s.ok}</span><span style={{ color: "var(--warn)" }}>● 未确认 {s.warn}</span>
                <span style={{ color: "var(--danger)" }}>● 死路由 {s.dead}</span><span>● 其他 {s.other}</span>
              </div>
            </div>
          </div>
      </div>

      {/* KPI 磁贴 ×4：聚光材质 + 数字滚动 + 渐变微条 */}
      <SpotlightCard className="card kpi-tile col-3" onClick={onOpenWorkbench} style={{ cursor: "pointer" }}>
        <div className="tile-head"><span className="tile-chip"><IconGrid size={15} /></span>功能模块</div>
        <div className="tile-num"><CountUp to={meta.stats.modules} duration={800} /></div>
        <ProgressBar value={100} />
        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>全部入图 · M01-M{String(meta.stats.modules).padStart(2, "0")}</div>
      </SpotlightCard>
      <SpotlightCard className="card kpi-tile col-3" onClick={onOpenWorkbench} style={{ cursor: "pointer" }}>
        <div className="tile-head"><span className="tile-chip"><IconBox size={15} /></span>功能点</div>
        <div className="tile-num"><CountUp to={map.features.length} duration={800} /></div>
        <ProgressBar value={coverage} />
        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>已考古 {s.ok} · 未考古 {s.other + s.warn}</div>
      </SpotlightCard>
      <SpotlightCard className="card kpi-tile col-3">
        <div className="tile-head"><span className="tile-chip"><IconEye size={15} /></span>状态采证</div>
        <div className="tile-num"><CountUp to={stateTotal} duration={800} /></div>
        <ProgressBar value={82} />
        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>{zoneTotal} 个功能区 · 键入区四态</div>
      </SpotlightCard>
      <SpotlightCard className="card kpi-tile col-3">
        <div className="tile-head"><span className="tile-chip chip-ok"><IconDoc size={15} /></span>证据文件</div>
        <div className="tile-num"><CountUp to={meta.stats.evidenceFiles} duration={800} /></div>
        <ProgressBar value={100} />
        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>截图/语义树/录屏/日志</div>
      </SpotlightCard>

      {/* 覆盖故事线（8 列）+ 风险卡（4 列）*/}
      <div className="card col-8">
        <h3>模块完成度排行（点击进工作台）</h3>
        {byModule.map((m) => (
          <div
            key={m.id}
            className={`rank-row${selMod === m.id ? " selected" : ""}${linkedMods.includes(m.id) ? " linked" : ""}`}
            onClick={() => setSelMod((cur) => (cur === m.id ? null : m.id))}
            onDoubleClick={() => onSelectModule(m.id)}
            title={`${m.name}：${m.ok}/${m.total} 已考古（单击选中联动，双击进工作台）`}
          >
            <span className="rank-id">{m.id}</span>
            <span className="rank-name">{m.name}</span>
            <div className="rank-track">
              <i className={m.pct >= 80 ? "" : m.pct >= 40 ? "partial" : "low"} style={{ width: `${m.pct}%` }} />
            </div>
            <span className="rank-num">{m.ok}/{m.total}</span>
          </div>
        ))}
      </div>

      <div className="card col-4">
            <h3>
              <IconShield size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              {selMod ? <>风险 · <span className="id-code">{selMod}</span> {modName(selMod)}</> : "风险在册（点击行看关联模块）"}
            </h3>
            <div className="risk-row" onClick={onOpenDefects} onMouseEnter={() => setLinkedMods(deadModIds)} onMouseLeave={() => setLinkedMods([])}>
              <span className="tile-chip chip-danger"><IconAlert size={15} /></span>
              <span style={{ flex: 1 }}>死路由</span>
              <span className="risk-num" style={{ color: "var(--danger)" }}>{riskView.dead}</span>
            </div>
            <div className="risk-row" onClick={onOpenDefects} onMouseEnter={() => setLinkedMods(bugModIds)} onMouseLeave={() => setLinkedMods([])}>
              <span className="tile-chip chip-danger"><IconAlert size={15} /></span>
              <span style={{ flex: 1 }}>BUG 候选</span>
              <span className="risk-num" style={{ color: "var(--danger)" }}>{riskView.bugs}</span>
            </div>
            <div className="risk-row" onClick={onOpenDefects}>
              <span className="tile-chip chip-warn"><IconAlert size={15} /></span>
              <span style={{ flex: 1 }}>O 项观察</span>
              <span className="risk-num" style={{ color: "var(--warn)" }}>{defects.oItems.length}</span>
            </div>
            <div className="risk-row" onClick={onOpenDefects}>
              <span className="tile-chip chip-warn"><IconAlert size={15} /></span>
              <span style={{ flex: 1 }}>边界标注</span>
              <span className="risk-num" style={{ color: "var(--warn)" }}>{defects.boundaryNotes.length}</span>
            </div>
            {selMod ? (
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <button className="btn" onClick={() => onSelectModule(selMod)}>进入本模块工作台 →</button>
                <button className="btn" onClick={() => setSelMod(null)}>返回全局</button>
              </div>
            ) : strongest && weakest && (
              <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 10, lineHeight: 1.7 }}>
                最强：<span className="id-code">{strongest.id}</span> {strongest.name}（{strongest.pct}%）
                <br />待补：<span className="id-code">{weakest.id}</span> {weakest.name}（{weakest.pct}%）
              </div>
            )}
      </div>

      {/* 可信度行 */}
      <div className="card col-12" style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}><IconGraph size={14} style={{ verticalAlign: "-2px", marginRight: 4 }} />数据可信度</h3>
        <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          来源 = <b style={{ color: "var(--text)" }}>2 个代码仓</b>静态解析 × <b style={{ color: "var(--text)" }}>9 片模拟器探索</b>实采；
          每条功能点带实现锚点（文件:行），每张采证页带 uiautomator 语义树；
          图谱边 100% EXTRACTED（无猜测边）。编号终身制可追溯（Mxx-Fyy）。
        </span>
        <button className="btn" onClick={onOpenWorkbench}>进入功能工作台 →</button>
      </div>
    </div>
  );
}
