// R8.3 Workbench 主从重组：面包屑 + 中区 Tab 化（状态矩阵/语义树/跳转关系）
// 保留：R4.3 切换重置、page= 深链、本模块/全部开关、bounds 红线、语义树/导航懒加载
import { useEffect, useMemo, useState } from "react";
import type { MapData, MatrixData, DefectsData } from "../types";
import { evidenceUrl, stateKind, navPageOf, loadSemantics, loadNav } from "../api";
import type { SemanticsData, NavData } from "../api";
import type { Selection } from "../App";
import { IconChevron } from "./Icons";
import { SmartImage } from "../ds";

interface Props {
  map: MapData;
  matrix: MatrixData;
  defects: DefectsData;
  sel: Selection;
  onSelect: (s: Selection) => void;
  onOpenEvidence: (moduleId: string) => void;
  featureIndex: Map<string, { feature: MapData["features"][0]; moduleName: string }>;
}

const KIND_COLOR: Record<string, string> = {
  empty: "var(--state-empty)", filled: "var(--state-filled)", error: "var(--state-error)",
  focus: "var(--state-focus)", combo: "var(--state-combo)", other: "var(--border-strong)",
};
const KIND_CLASS: Record<string, string> = {
  empty: "st-empty", filled: "st-filled", error: "st-error",
  focus: "st-focus", combo: "st-combo", other: "st-other",
};
const LEGEND: { k: string; label: string }[] = [
  { k: "empty", label: "空态" }, { k: "filled", label: "有值" },
  { k: "error", label: "报错" }, { k: "focus", label: "聚焦" },
  { k: "combo", label: "组合" }, { k: "other", label: "其它" },
];
type Tab = "matrix" | "semantics" | "nav";

// R8.4 虚拟滚动参数（自写，行高固定 28px，视窗 440px）
const SEM_ROW_H = 28;
const SEM_VIEW_H = 440;

export default function Workbench({ map, matrix, defects, sel, onSelect, onOpenEvidence, featureIndex }: Props) {
  const entry = sel.featureId ? featureIndex.get(sel.featureId) : undefined;
  const feature = entry?.feature;
  const [semDumpFile, setSemDumpFile] = useState<string | null>(null);
  const [semOnlyClick, setSemOnlyClick] = useState(true);
  const [semQ, setSemQ] = useState("");
  const [semScroll, setSemScroll] = useState({ top: 0 });
  const [copied, setCopied] = useState<string | null>(null);
  // 点击可点击行 → 复制 resource-id（自动化定位符直接进剪贴板）
  const copyRid = (rid: string) => {
    navigator.clipboard?.writeText(rid).then(() => {
      setCopied(rid);
      window.setTimeout(() => setCopied(null), 1500);
    }).catch(() => {});
  };
  const [semantics, setSemantics] = useState<SemanticsData | null>(null);
  const [nav, setNav] = useState<NavData | null>(null);
  const [tab, setTab] = useState<Tab>(() => {
    const q = new URLSearchParams(location.search).get("tab");
    return q === "semantics" || q === "nav" ? q : "matrix";
  });
  useEffect(() => {
    loadSemantics().then(setSemantics).catch(() => {});
    loadNav().then(setNav).catch(() => {});
  }, []);

  // 采证浏览：点功能点 → 默认显示本模块采证页；单开关可展开全部（按钮常驻不可消失）
  const modulePages = useMemo(
    () => (feature?.evidencePages?.length ? matrix.pages.filter((p) => feature.evidencePages!.includes(p.name)) : []),
    [feature, matrix],
  );
  const [showAllPages, setShowAllPages] = useState(false);
  const [pageName, setPageName] = useState<string | null>(null);
  const [jumped, setJumped] = useState(false); // 功能点名是否命中专属采证区（未命中时明示"模块级采证"）

  // 切换功能点时重置浏览状态（R4.3）；URL 带 page= 时落在指定页（导航图跳转）
  // 0.3.0 对应修复：功能点名 → 证据功能区精确跳转。token=功能点名的中文串+路由段；
  // ASCII 词边界匹配；特异性折扣——命中多个功能区的泛词降权（2 区半权重 / ≥3 区归零），
  // 总分 ≥2 才跳（如「区号选择」→ Z005 区号选择区），未命中回退模块默认，杜绝误跳。
  useEffect(() => {
    setShowAllPages(false);
    setSemDumpFile(null);
    setZoneId(null);
    setStateIdx(0);
    const urlPage = new URLSearchParams(location.search).get("page");
    if (urlPage) { setPageName(urlPage); setJumped(true); return; }
    if (feature) {
      // 数据驱动直达：功能点级专属映射（编译器按源文档记载挂到具体状态）
      const sids = feature.evidenceStateIds ?? [];
      if (sids.length) {
        for (const p of matrix.pages) {
          for (const z of p.zones) {
            const si = z.states.findIndex((s) => sids.includes(s.id));
            if (si >= 0) { setPageName(p.name); setZoneId(z.id); setStateIdx(si); setJumped(true); return; }
          }
        }
      }
      const name = feature.name.toLowerCase();
      const toks = new Set<string>();
      for (const m of name.matchAll(/[一-鿿]{2,}/g)) toks.add(m[0]);
      for (const m of name.matchAll(/\/([a-z0-9\-_]+)/g)) {
        toks.add(m[1]);
        toks.add(m[1].replace(/-/g, "_"));
      }
      const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const hasTok = (hay: string, t: string) =>
        /[一-鿿]/.test(t) ? hay.includes(t)
          : new RegExp(`(?<![a-z0-9])${esc(t)}(?![a-z0-9])`).test(hay);
      const hays = matrix.pages.map((p) => ({
        p, hs: p.zones.map((z) => `${z.title} ${z.raw} ${z.anchors.join(" ")}`.toLowerCase()),
      }));
      const weight = new Map<string, number>();
      for (const t of toks) {
        const cnt = hays.reduce((n, x) => n + x.hs.filter((h) => hasTok(h, t)).length, 0);
        const base = /[一-鿿]/.test(t) ? 2 : t.length >= 4 ? 2 : 1;
        weight.set(t, base * (cnt === 1 ? 1 : cnt === 2 ? 0.5 : 0));
      }
      let best: { page: string; zone: string; score: number } | null = null;
      for (const { p, hs } of hays) {
        for (let i = 0; i < p.zones.length; i++) {
          let score = 0;
          for (const t of toks) { const w = weight.get(t); if (w && hasTok(hs[i], t)) score += w; }
          if (score >= 2 && (!best || score > best.score)) best = { page: p.name, zone: p.zones[i].id, score };
        }
      }
      if (best) { setPageName(best.page); setZoneId(best.zone); setJumped(true); return; }
    }
    setPageName(null);
    setJumped(false);
  }, [feature?.id, matrix]);

  // 0.3.0 对应修复：功能点所属模块无采证页时不再默认落全站首页（误导），改为诚实空态；
  // 有模块采证页时正常展示，回退情形由 jumped 状态明示。
  const pageList = !feature
    ? matrix.pages
    : modulePages.length
      ? (showAllPages ? matrix.pages : modulePages)
      : (showAllPages ? matrix.pages : []);
  const activePage = pageList.find((p) => p.name === (pageName ?? pageList[0]?.name)) ?? pageList[0];
  // 页面组内的图片数（仅 png/jpg 口径）
  const imgCount = (p: MatrixData["pages"][0]) =>
    new Set(p.zones.flatMap((z) => z.states.flatMap((s) => s.files)).filter((f) => /\.(png|jpe?g)$/i.test(f))).size;
  const [zoneId, setZoneId] = useState<string | null>(null);
  const zone = activePage?.zones.find((z) => z.id === zoneId) ?? activePage?.zones[0];
  const [stateIdx, setStateIdx] = useState(0);
  const st = zone?.states[Math.min(stateIdx, zone.states.length - 1)];

  // 键盘 ←→ 切状态（状态矩阵 Tab 激活时）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (tab !== "matrix" || !zone) return;
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      if (e.key === "ArrowRight") setStateIdx((i) => Math.min(zone.states.length - 1, i + 1));
      else if (e.key === "ArrowLeft") setStateIdx((i) => Math.max(0, i - 1));
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, zone]);

  const semDumps = activePage && semantics ? semantics.dumps.filter((d) => d.page === activePage.name) : [];
  const semCount = semantics ? semantics.dumps.reduce((a, d) => a + 1, 0) : 0;

  return (
    <>
      <div className="center">
        {/* 面包屑（R8.3：滚动不迷路） */}
        <div className="crumbs">
          <span className="crumb" onClick={() => sel.moduleId && onSelect({ ...sel, featureId: null })}>
            {entry?.moduleName ?? "选择功能点"}
          </span>
          {feature && <><span className="crumb-sep"><IconChevron size={12} /></span>
            <span className="crumb-here">{feature.id} {feature.name}</span></>}
          {feature && activePage && <><span className="crumb-sep"><IconChevron size={12} /></span>
            <span className={tab === "matrix" ? "crumb-here" : "crumb"} onClick={() => setTab("matrix")}>{activePage.name}</span></>}
          {feature && activePage && zone && tab === "matrix" && <><span className="crumb-sep"><IconChevron size={12} /></span>
            <span className="crumb-here">{zone.id} {zone.title.slice(0, 16)}</span></>}
        </div>

        {!feature && (
          <div className="card">
            <h3>{map.modules.find((m) => m.id === sel.moduleId)?.name ?? "选择功能点"}</h3>
            <p className="muted">在左栏展开模块并点击功能点；或按 <kbd>/</kbd> 聚焦顶部搜索输入编号（如 M01-F03）。</p>
            {sel.moduleId && (
              <table className="tbl">
                <thead><tr><th>编号</th><th>功能点</th><th>状态</th></tr></thead>
                <tbody>
                  {map.features.filter((f) => f.moduleId === sel.moduleId).map((f) => (
                    <tr key={f.id} style={{ cursor: "pointer" }} onClick={() => onSelect({ ...sel, featureId: f.id })}>
                      <td className="id-code">{f.id}</td>
                      <td>{f.name}</td>
                      <td>{f.deadRoute ? <span className="tag tag-danger">死路由</span> : <span className="tag tag-muted">{f.status || "—"}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {feature && (
          <>
            {/* 功能点详情（紧凑条） */}
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span className="badge">{feature.id}</span>
                {feature.deadRoute && <span className="tag tag-danger">死路由</span>}
                {feature.status && <span className="tag tag-muted">{feature.status}</span>}
                <b style={{ fontSize: "var(--fs-lg)" }}>{feature.name}</b>
                <span style={{ marginLeft: "auto" }}>
                  <button className="btn" onClick={() => onOpenEvidence(feature.moduleId)}>证据库（模块过滤）→</button>
                </span>
              </div>
              {feature.entries.length > 0 && (
                <div style={{ marginTop: 6 }}><span className="muted">入口：</span>{feature.entries.map((e, i) => <span key={i} className="mono" style={{ marginRight: 10 }}>{e}</span>)}</div>
              )}
              {feature.implementations.length > 0 && (
                <pre className="code" style={{ marginTop: 8 }}>{feature.implementations[0]}</pre>
              )}
              {feature.implementations.length > 1 && (
                <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>另有实现锚点 {feature.implementations.length - 1} 条</div>
              )}
            </div>

            {/* Tab 条 */}
            <div className="tab-bar">
              <button className={`tab ${tab === "matrix" ? "active" : ""}`} onClick={() => setTab("matrix")}>状态矩阵</button>
              <button className={`tab ${tab === "semantics" ? "active" : ""}`} onClick={() => setTab("semantics")}>
                语义树{semantics ? `（${semCount} dump）` : ""}
              </button>
              <button className={`tab ${tab === "nav" ? "active" : ""}`} onClick={() => setTab("nav")}>跳转关系</button>
            </div>

            {/* ── Tab: 状态矩阵（0.3.0 控制台+画布单卡：左控制列 / 右预览画布） ── */}
            {tab === "matrix" && feature && modulePages.length === 0 && !showAllPages ? (
              <div className="card" style={{ padding: "36px 28px", textAlign: "center" }}>
                <b style={{ fontSize: "var(--fs-lg)" }}>本模块暂无采证页</b>
                <p className="muted" style={{ fontSize: "var(--fs-sm)", lineHeight: 1.9, margin: "10px auto 16px", maxWidth: 520 }}>
                  该模块属未考古 / 待后续分片范围（源文档无对应采证节）。为避免误导，此处不默认展示其它模块的截图；可浏览全部采证页自行对照。
                </p>
                <button className="btn" onClick={() => { setShowAllPages(true); setPageName(null); }}>浏览全部采证页（{matrix.pages.length} 页）</button>
              </div>
            ) : tab === "matrix" && (
              <div className="card workcanvas-card">
                <div className="workcanvas">
                  {/* 左：控制列（采证页 → 功能区 → 状态，层级选择流） */}
                  <div className="wc-ctl">
                    <div className="wc-group">
                      <div className="wc-group-head">
                        采证页{feature && modulePages.length > 0 && !showAllPages ? `（本模块 ${modulePages.length}）` : `（全部 ${matrix.pages.length}）`}
                        {feature && modulePages.length > 0 && (
                          <span className="wc-toggle">
                            <button className={`btn ${!showAllPages ? "active" : ""}`} onClick={() => { setShowAllPages(false); setPageName(null); }}>本模块</button>
                            <button className={`btn ${showAllPages ? "active" : ""}`} onClick={() => { setShowAllPages(true); setPageName(null); }}>全部</button>
                          </span>
                        )}
                      </div>
                      {feature && modulePages.length === 0 && (
                        <p className="muted wc-hint">本模块暂无专属采证（边界外/待真机），已显示全部采证页。</p>
                      )}
                      {feature && modulePages.length > 0 && !jumped && (
                        <p className="muted wc-hint">该功能点无专属采证定位——以下为本模块采证页（最近证据），供模块级对照。</p>
                      )}
                      <div className="ds-chipgrid">
                        {pageList.map((p, i) => (
                          <button key={p.name + i} className={`btn ${activePage === p ? "active" : ""}`}
                            onClick={() => { setPageName(p.name); setZoneId(null); setStateIdx(0); }}
                            title={`${p.name} · ${p.zones.length} 区 · ${imgCount(p)} 图`}>
                            {p.name}
                          </button>
                        ))}
                      </div>
                    </div>
                    {activePage && (
                      <div className="wc-group">
                        <div className="wc-group-head">功能区（{activePage.zones.length}）</div>
                        <div className="ds-chipgrid">
                          {activePage.zones.map((z) => (
                            <button key={z.id} className={`btn ${zone?.id === z.id ? "active" : ""}`}
                              onClick={() => { setZoneId(z.id); setStateIdx(0); }} title={z.title}>
                              <span className="id-code">{z.id}</span> {z.title.slice(0, 10)}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {zone && (
                      <div className="wc-group">
                        <div className="wc-group-head">状态（{zone.states.length}）<span className="kbd-hint"><kbd>←</kbd><kbd>→</kbd></span></div>
                        <div className="legend-row" style={{ marginBottom: 4 }}>
                          {LEGEND.map((l) => <span key={l.k} className="lg"><span className="legend-dot" style={{ background: KIND_COLOR[l.k] }} />{l.label}</span>)}
                        </div>
                        <div className="wc-states">
                          {zone.states.map((st2, i) => {
                            const kind = stateKind(st2.state);
                            return (
                              <button key={st2.id} className={`btn state-btn ${i === stateIdx ? "active" : ""} ${KIND_CLASS[kind]}`}
                                onClick={() => setStateIdx(i)} title={st2.notes}>
                                {st2.state}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                  {/* 右：预览画布（截图三态 + bounds 红线 + 状态详情） */}
                  <div className="wc-canvas">
                    {zone && st && (
                      <>
                        <div className="wc-shot-col">
                          <div className="wc-shot">
                            <SmartImage src={st.files[0] ? evidenceUrl(st.files[0]) : null} alt={st.state} aspect="9 / 16" />
                            {zone.bounds[0] && st.files[0] && (
                              <div className="bounds-overlay" style={{
                                left: `${(parseInt(zone.bounds[0][0]) / 1080) * 100}%`,
                                top: `${(parseInt(zone.bounds[0][0].split(",")[1]) / 1920) * 100}%`,
                                width: `${((parseInt(zone.bounds[0][1].split(",")[0]) - parseInt(zone.bounds[0][0].split(",")[0])) / 1080) * 100}%`,
                                height: `${((parseInt(zone.bounds[0][1].split(",")[1]) - parseInt(zone.bounds[0][0].split(",")[1])) / 1920) * 100}%`,
                              }} />
                            )}
                          </div>
                          {zone.bounds[0] && <div className="bounds-legend mono">bounds [{zone.bounds[0][0]}][{zone.bounds[0][1]}] @1080×1920</div>}
                        </div>
                        <div className="wc-detail">
                          <div><span className="tag tag-accent">{st.id}</span><b>{st.state}</b></div>
                          <div className="muted" style={{ margin: "6px 0" }}>{st.notes || "—"}</div>
                          {zone.linkage && (
                            <div className="linkage-note"><b style={{ color: "var(--ok)" }}>联动结论</b><div style={{ fontSize: "var(--fs-sm)" }}>{zone.linkage}</div></div>
                          )}
                          {zone.anchors.length > 0 && (
                            <div><span className="muted">语义锚点：</span>{zone.anchors.map((a, i) => <span key={i} className="mono" style={{ marginRight: 8 }}>{a}</span>)}</div>
                          )}
                          {st.files.length > 1 && (
                            <div style={{ marginTop: 8 }}>
                              <span className="muted">该状态关联文件：</span>
                              {st.files.map((f) => <div key={f} className="mono" style={{ fontSize: "var(--fs-xs)" }}>{f}</div>)}
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Tab: 语义树（R8.4：虚拟滚动 + 只看可点击 + rid/text 过滤） ── */}
            {tab === "semantics" && activePage && (() => {
              const dump = semDumps.find((d) => d.file === semDumpFile) ?? semDumps[0];
              if (!dump) {
                return (
                  <div className="card empty-state">
                    <p className="muted">本页（{activePage.name}）暂无语义树 dump。</p>
                  </div>
                );
              }
              const filtered = dump.nodes.filter((n) =>
                (!semOnlyClick || n.clickable) &&
                (!semQ || n.rid.toLowerCase().includes(semQ.toLowerCase()) ||
                  n.text.toLowerCase().includes(semQ.toLowerCase())));
              const totalH = filtered.length * SEM_ROW_H;
              const start = Math.max(0, Math.floor(semScroll.top / SEM_ROW_H) - 8);
              const visible = Math.ceil(SEM_VIEW_H / SEM_ROW_H) + 16;
              const slice = filtered.slice(start, start + visible);
              return (
                <div className="card">
                  <h3>语义树 · {dump.page}（{dump.nodeCount} 交互节点 / {dump.clickableCount} 可点击）</h3>
                  {semDumps.length > 1 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                      {semDumps.map((d) => (
                        <button key={d.file} className={`btn ${dump.file === d.file ? "active" : ""}`}
                          onClick={() => setSemDumpFile(d.file)}>{d.file}</button>
                      ))}
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
                    <label style={{ fontSize: "var(--fs-sm)", cursor: "pointer", display: "flex", alignItems: "center", gap: 5 }}>
                      <input type="checkbox" checked={semOnlyClick}
                        onChange={(e) => { setSemOnlyClick(e.target.checked); setSemScroll({ top: 0 }); }} /> 只看可点击
                    </label>
                    <input
                      className="search" style={{ flex: "0 1 260px", margin: 0 }}
                      placeholder="过滤 resource_id / text"
                      value={semQ} onChange={(e) => { setSemQ(e.target.value); setSemScroll({ top: 0 }); }}
                    />
                    <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                      显示 {filtered.length} / {dump.nodes.length} 节点（虚拟滚动）
                    </span>
                    {copied && <span className="tag tag-ok">已复制 {copied}</span>}
                  </div>
                  <div className="vhead">
                    <span>click</span><span>resource_id</span><span>text / desc</span><span>bounds</span>
                  </div>
                  <div
                    className="vlist"
                    style={{ height: SEM_VIEW_H }}
                    onScroll={(e) => setSemScroll({ top: (e.target as HTMLDivElement).scrollTop })}
                  >
                    <div style={{ height: totalH, position: "relative" }}>
                      <div style={{ position: "absolute", top: start * SEM_ROW_H, left: 0, right: 0 }}>
                        {slice.map((n, i) => (
                          <div
                            key={start + i}
                            className={`vrow ${n.clickable ? "vrow-click" : ""}`}
                            style={n.clickable ? { cursor: "pointer" } : undefined}
                            title={n.clickable ? `点击复制 resource-id：${n.rid}` : `${n.rid} | ${n.text}`}
                            onClick={() => n.clickable && n.rid && copyRid(n.rid)}
                          >
                            <span className="mono">{n.clickable ? "click" : n.scrollable ? "scroll" : ""}</span>
                            <span className="mono">{n.rid}</span>
                            <span className="vtext">{n.text}</span>
                            <span className="mono">{n.bounds ? `[${n.bounds[0]},${n.bounds[1]}][${n.bounds[2]},${n.bounds[3]}]` : ""}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 6 }}>
                    蓝底 = 可点击，**点击行复制 resource-id**（自动化定位符）。来源：{dump.file}（uiautomator dump）
                  </div>
                </div>
              );
            })()}

            {/* ── Tab: 跳转关系 ── */}
            {tab === "nav" && activePage && nav && (() => {
              const np = navPageOf(activePage.name);
              const outEdges = np ? nav.edges.filter((e) => e.from === np) : [];
              const inEdges = np ? nav.edges.filter((e) => e.to === np) : [];
              return (outEdges.length > 0 || inEdges.length > 0) ? (
                <div className="card">
                  <h3>跳转关系（导航页：{np}）</h3>
                  {inEdges.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <span className="tag tag-accent">到达这里</span>
                      {inEdges.map((e, i) => (
                        <div key={`in-${e.from}-${e.to}-${e.label}-${i}`} style={{ margin: "4px 0", fontSize: "var(--fs-sm)" }}>
                          ← <b>{e.from}</b>：<span className="mono">{e.label}</span>{e.note && <span className="muted">（{e.note}）</span>}
                        </div>
                      ))}
                    </div>
                  )}
                  {outEdges.length > 0 && (
                    <div>
                      <span className="tag tag-ok">从这里出发</span>
                      {outEdges.map((e, i) => (
                        <div key={`out-${e.from}-${e.to}-${e.label}-${i}`} style={{ margin: "4px 0", fontSize: "var(--fs-sm)" }}>
                          → <b>{e.to}</b>：<span className="mono">{e.label}</span>{e.note && <span className="muted">（{e.note}）</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="card empty-state">
                  <p className="muted">{np ? `导航页「${np}」暂无已记录跳转边。` : "本页暂无导航匹配。"}</p>
                </div>
              );
            })()}
          </>
        )}
      </div>

      {/* 右栏：功能区原始信息 + 缺陷关联 */}
      <div className="right" style={{ padding: 14 }}>
        {zone && (
          <div className="card">
            <h3>功能区原始数据</h3>
            <div className="mono" style={{ fontSize: "var(--fs-xs)", wordBreak: "break-all" }}>{zone.raw || zone.title}</div>
            <table className="tbl" style={{ marginTop: 8 }}>
              <thead><tr><th>状态</th><th>文件数</th></tr></thead>
              <tbody>
                {zone.states.map((s) => (
                  <tr key={s.id} style={{ cursor: "pointer" }} onClick={() => { setStateIdx(zone.states.indexOf(s)); setTab("matrix"); }}>
                    <td>{s.state}</td>
                    <td>{s.files.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {feature && (
          <div className="card">
            <h3>关联缺陷候选</h3>
            {defects.bugs
              .filter((b) => (b.module + b.phenomenon).toLowerCase().includes(
                feature.name.split("/")[0].slice(0, 4).toLowerCase()))
              .map((b) => (
                <div key={b.id} style={{ marginBottom: 8 }}>
                  <span className="tag tag-danger">{b.id}</span> {b.classification}
                </div>
              ))}
            <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
              共 {defects.bugs.length} 条 BUG 候选 / {defects.oItems.length} 条 O 项，见「缺陷与裁决」视图。
            </div>
          </div>
        )}
      </div>
    </>
  );
}
