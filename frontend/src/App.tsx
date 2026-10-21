import { useEffect, useMemo, useState } from "react";
import { loadMeta, loadMap, loadMatrix, loadDefects, loadChangelog, loadVersions, setVersionOverride, getVersionOverride } from "./api";
import type { Meta, MapData, MatrixData, DefectsData, ChangelogEntry } from "./types";
import type { VersionInfo } from "./api";
import Topbar from "./components/Topbar";
import Sidebar from "./components/Sidebar";
import TreePanel from "./components/TreePanel";
import Dashboard from "./components/Dashboard";
import Workbench from "./components/Workbench";
import Defects from "./components/Defects";
import Changelog from "./components/Changelog";
import GraphView from "./components/GraphView";
import EvidenceLibrary from "./components/EvidenceLibrary";
import KnowledgeView from "./components/KnowledgeView";
import StyleGuide from "./components/StyleGuide";
import TestCaseReview from "./components/TestCaseReview";

export type ViewName = "dashboard" | "workbench" | "defects" | "changelog" | "graph" | "evidence" | "knowledge" | "cases" | "styleguide";

export interface Selection {
  moduleId: string | null;
  featureId: string | null;
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [map, setMap] = useState<MapData | null>(null);
  const [matrix, setMatrix] = useState<MatrixData | null>(null);
  const [defects, setDefects] = useState<DefectsData | null>(null);
  const [changelog, setChangelog] = useState<ChangelogEntry[] | null>(null);
  const [view, setViewState] = useState<ViewName>(() => {
    const q = new URLSearchParams(location.search);
    return (q.get("view") as ViewName) || "dashboard";
  });
  const [sel, setSel] = useState<Selection>(() => {
    const q = new URLSearchParams(location.search);
    return { moduleId: q.get("module"), featureId: q.get("feature") };
  });
  const [error, setError] = useState<string | null>(null);
  // 版本管理：ver=null 跟随 data/current.json；用户切换后 override 全站取数并同步 URL
  const [ver, setVerState] = useState<string | null>(() => new URLSearchParams(location.search).get("v"));
  const [versions, setVersions] = useState<VersionInfo[] | null>(null);
  const [serverCurrent, setServerCurrent] = useState<string>("");
  const setVer = (nv: string) => {
    setVersionOverride(nv);
    setVerState(nv);
  };
  // 顶栏搜用例回落:编号/标题非功能地图编号时,落到用例页带词搜索
  const [caseSearch, setCaseSearch] = useState("");
  // 0.3.0 树面板可折叠：窄条释放工作区（折叠态 40px 只留展开钮）
  const [treeCollapsed, setTreeCollapsed] = useState(false);

  // URL 深链：视图/编号变化同步到地址栏（pushState——后退可逐步回退，popstate 恢复状态）
  const setView = (v: ViewName) => {
    setViewState(v);
    syncUrl(v, sel);
  };
  const onSelect = (s: Selection) => {
    setSel(s);
    syncUrl(view, s);
  };
  const syncUrl = (v: ViewName, s: Selection, page?: string) => {
    const q = new URLSearchParams();
    if (v !== "dashboard") q.set("view", v);
    if (s.moduleId) q.set("module", s.moduleId);
    if (s.featureId) q.set("feature", s.featureId);
    if (page) q.set("page", page);
    if (ver && ver !== serverCurrent) q.set("v", ver);
    history.pushState(null, "", `${location.pathname}${q.toString() ? "?" + q : ""}`);
  };

  useEffect(() => {
    Promise.all([loadMeta(), loadMap(), loadMatrix(), loadDefects(), loadChangelog()])
      .then(([m, mp, mx, df, cl]) => {
        setMeta(m); setMap(mp); setMatrix(mx); setDefects(df); setChangelog(cl);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // 版本清单：拉 /api/versions 供侧栏切换器；无 override 时跟随站点指针
  useEffect(() => {
    let dead = false;
    loadVersions()
      .then((d) => {
        if (dead) return;
        setVersions(d.versions);
        setServerCurrent(d.current);
        if (!getVersionOverride()) setVerState(null); // 跟随指针；仅同步 current 展示
      })
      .catch(() => {});
    return () => { dead = true; };
  }, []);

  // B 批：后退/前进按钮恢复视图与选中（配合 pushState）
  useEffect(() => {
    const onPop = () => {
      const q = new URLSearchParams(location.search);
      setViewState((q.get("view") as ViewName) || "dashboard");
      setSel({ moduleId: q.get("module"), featureId: q.get("feature") });
      const qv = q.get("v");
      setVersionOverride(qv);
      setVerState(qv);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const featureIndex = useMemo(() => {
    const idx = new Map<string, { feature: MapData["features"][0]; moduleName: string }>();
    if (map) {
      const nameById = new Map(map.modules.map((m) => [m.id, m.name]));
      for (const f of map.features) idx.set(f.id, { feature: f, moduleName: nameById.get(f.moduleId) ?? "" });
    }
    return idx;
  }, [map]);

  const gotoFeature = (featureId: string): boolean => {
    const f = featureIndex.get(featureId);
    if (!f) return false;
    const s = { moduleId: f.feature.moduleId, featureId };
    setSel(s);
    setViewState("workbench");
    syncUrl("workbench", s);
    return true;
  };

  // B 批：返回是否命中，供顶栏搜索给出「未找到」反馈
  const gotoId = (raw: string): boolean => {
    const id = raw.trim().toUpperCase();
    if (featureIndex.has(id)) return gotoFeature(id);
    if (map?.modules.some((m) => m.id === id)) {
      const s = { moduleId: id, featureId: null };
      setSel(s);
      setViewState("workbench");
      syncUrl("workbench", s);
      return true;
    }
    // 非功能地图编号 → 用例搜索(launch-NEG-001 式编号或标题/关键词都能落到用例)
    setCaseSearch(raw.trim());
    setView("cases");
    return true;
  };

  // C 批：导航图节点 → 对应采证页（matrix 页名 → 首个含它的模块 → 工作台并选中该页）
  const gotoMatrixPage = (matrixPage: string) => {
    const hit = map?.features.find((f) => f.evidencePages?.includes(matrixPage));
    if (!hit) return;
    const s = { moduleId: hit.moduleId, featureId: null };
    setSel(s);
    setViewState("workbench");
    syncUrl("workbench", s, matrixPage);
  };

  // 0.3.0 全站卡片聚光：一次监听，hover 到哪张卡就把 --mx/--my 写到哪（全站统一鼠标跟随）
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const card = (e.target as HTMLElement | null)?.closest?.(".card") as HTMLElement | null;
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    return () => window.removeEventListener("mousemove", onMove);
  }, []);

  // C 批：功能点详情 → 证据库（带模块过滤）
  const openEvidence = (moduleId: string) => {
    const s = { moduleId, featureId: null };
    setSel(s);
    setViewState("evidence");
    syncUrl("evidence", s);
  };

  if (error) return <div style={{ padding: 40, color: "var(--danger)" }}>数据加载失败：{error}<br />请确认已运行 tools/build_data.py 且服务已启动。</div>;
  // R8.1：骨架屏替代文字加载态
  if (!meta || !map || !matrix || !defects || !changelog) return (
    <div className="center" style={{ maxWidth: 980, margin: "0 auto", width: "100%" }}>
      <div className="card">
        <div className="skeleton" style={{ height: 26, width: 220, marginBottom: 16 }} />
        <div style={{ display: "flex", gap: 24 }}>
          {[64, 64, 64, 64].map((h, i) => <div key={i} className="skeleton" style={{ height: h, flex: 1 }} />)}
        </div>
      </div>
      <div className="card">
        <div className="skeleton" style={{ height: 18, width: 160, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 12, width: "70%", marginBottom: 8 }} />
        <div className="skeleton" style={{ height: 12, width: "55%" }} />
      </div>
    </div>
  );

  return (
    <div id="app">
      {/* 全局背景层：AI 背景图（暗/亮随主题）+ 氛围光 + 噪点，垫在整页之下（Slate 式 behind interface chrome） */}
      <div className="ds-global-bg" aria-hidden="true">
        <img className="ds-atmo-media ds-atmo-media-dark" src="/bg-night.png" alt="" />
        <img className="ds-atmo-media ds-atmo-media-light" src="/bg-day.png" alt="" />
      </div>
      <Topbar onSearch={gotoId} />
      <div className="shell">
        <Sidebar meta={meta} view={view} onView={setView} ver={ver} versions={versions} serverCurrent={serverCurrent} onVer={setVer} />
        <main className="content">
          {view === "workbench" ? (
            <div className="full-bleed">
              <div className="workspace" style={{ flex: 1, minHeight: 0 }}>
                <div className={treeCollapsed ? "left left-collapsed" : "left"}>
                  <button className="tree-toggle" title={treeCollapsed ? "展开模块树" : "收起模块树"} onClick={() => setTreeCollapsed((v) => !v)}>
                    {treeCollapsed ? "»" : "«"}
                  </button>
                  <div className="tree-body">
                    <TreePanel
                      map={map}
                      matrix={matrix}
                      sel={sel}
                      onSelect={(moduleId, featureId) => onSelect({ moduleId, featureId })}
                    />
                  </div>
                </div>
                <Workbench
                  map={map}
                  matrix={matrix}
                  defects={defects}
                  sel={sel}
                  onSelect={onSelect}
                  onOpenEvidence={openEvidence}
                  featureIndex={featureIndex}
                />
              </div>
            </div>
          ) : view === "graph" ? (
            <div className="full-bleed">
              <div style={{ flex: 1, minWidth: 0 }}>
                <GraphView map={map} gotoFeature={gotoFeature} gotoNavPage={gotoMatrixPage} selectModule={(id) => { const s = { moduleId: id, featureId: null }; setSel(s); setViewState("workbench"); syncUrl("workbench", s); }} />
              </div>
            </div>
          ) : view === "knowledge" ? (
            <div className="full-bleed">
              <KnowledgeView onBack={() => setViewState("workbench")} />
            </div>
          ) : (
            <div className="content-inner">
              {view === "dashboard" ? (
                <Dashboard meta={meta} map={map} matrix={matrix} defects={defects} onOpenWorkbench={() => setView("workbench")} onOpenDefects={() => setView("defects")} onSelectModule={(id) => { const s = { moduleId: id, featureId: null }; setSel(s); setViewState("workbench"); syncUrl("workbench", s); }} />
              ) : view === "defects" ? (
                <Defects defects={defects} featureIndex={featureIndex} gotoFeature={gotoFeature} />
              ) : view === "evidence" ? (
                <EvidenceLibrary
                  modules={map.modules}
                  moduleId={sel.moduleId}
                  onModule={(id) => onSelect({ ...sel, moduleId: id, featureId: null })}
                />
              ) : view === "cases" ? (
                <TestCaseReview version={ver} initialQuery={caseSearch} />
              ) : view === "styleguide" ? (
                <StyleGuide />
              ) : (
                <Changelog entries={changelog} />
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
