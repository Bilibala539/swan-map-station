import { useEffect, useRef, useState } from "react";
import type { MapData } from "../types";
import { loadNav, loadGraphify } from "../api";
import type { NavData, GraphifyCommunityData, CommNode } from "../api";

interface Props {
  map: MapData;
  gotoFeature: (id: string) => void;
  gotoNavPage: (matrixPage: string) => void;
  selectModule: (id: string) => void;
}

interface Node {
  id: string; label: string; kind: "module" | "feature";
  deg: number; r: number; x: number; y: number; vx: number; vy: number;
  color: string; data?: CommNode;
}

/** Obsidian 风格力导向图谱。默认模块级（18 节点，与探索知识库图谱同级的清爽度），
 *  可展开功能点层。颜色语义：蓝=模块 绿=有实机采证 灰=未采证 红=死路由 */
// B 批：nav 数据自取；gotoFeature/selectModule 经 ref 引用——回调身份变化不再重建整张图
export default function GraphView({ map, gotoFeature, gotoNavPage, selectModule }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [showFeatures, setShowFeatures] = useState(false);
  const [navMode, setNavMode] = useState(() => new URLSearchParams(location.search).get("mode") === "nav");
  const [nav, setNav] = useState<NavData | null>(null);
  const [commMode, setCommMode] = useState(() => new URLSearchParams(location.search).get("mode") === "comm");
  const [graphify, setGraphify] = useState<GraphifyCommunityData | null>(null);
  const [commDetail, setCommDetail] = useState<CommNode | null>(null);
  const cbRef = useRef({ gotoFeature, gotoNavPage, selectModule, setCommDetail });
  cbRef.current = { gotoFeature, gotoNavPage, selectModule, setCommDetail };
  useEffect(() => {
    loadNav().then(setNav).catch(() => {});
    loadGraphify().then(setGraphify).catch(() => {});
  }, []);

  useEffect(() => {
    if (navMode && !nav) return;
    if (commMode && !graphify) return;
    const nv = nav as NavData; // navMode 分支内非空（上方守卫保证；闭包内 TS 不收窄，此处断言）
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const ctx = canvas.getContext("2d")!;
    let W = 0, H = 0, dpr = 1;
    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      W = wrap.clientWidth; H = wrap.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);  // 布局尺寸（ensureLoop 由下方 onResize 补挂）

    const css = getComputedStyle(document.documentElement);
    const INK = css.getPropertyValue("--text").trim() || "#e2e8f0";
    const ACCENT = css.getPropertyValue("--accent").trim() || "#38bdf8";

    const nodes: Node[] = [];
    const byId = new Map<string, Node>();
    if (commMode && graphify) {
      // S5 代码社区：745 个社区节点，半径按成员数缩放
      for (const c of graphify.nodes) {
        const n: Node = {
          id: c.id, label: c.label, kind: "module", deg: c.size,
          r: Math.min(26, 4 + Math.sqrt(c.size) * 1.1), x: 0, y: 0, vx: 0, vy: 0,
          color: ACCENT, data: c,
        };
        nodes.push(n); byId.set(c.id, n);
      }
    } else if (navMode) {
      // 导航图：页面节点 + 带标签跳转边
      const deg = new Map<string, number>();
      for (const e of nv.edges) {
        deg.set(e.from, (deg.get(e.from) ?? 0) + 1);
        deg.set(e.to, (deg.get(e.to) ?? 0) + 1);
      }
      for (const p of nv.pages) {
        const n: Node = { id: p, label: p, kind: "module", deg: deg.get(p) ?? 1, r: 9 + Math.sqrt(deg.get(p) ?? 1) * 2.2, x: 0, y: 0, vx: 0, vy: 0, color: ACCENT };
        nodes.push(n); byId.set(p, n);
      }
    } else {
      for (const m of map.modules) {
        const n: Node = { id: m.id, label: m.name, kind: "module", deg: m.featureCount, r: 12 + Math.sqrt(m.featureCount) * 3.2, x: 0, y: 0, vx: 0, vy: 0, color: ACCENT };
        nodes.push(n); byId.set(m.id, n);
      }
      for (const f of showFeatures ? map.features : []) {
        const n: Node = {
          id: f.id, label: f.name.length > 16 ? f.name.slice(0, 15) + "…" : f.name, kind: "feature", deg: 1, r: 4.5,
          x: 0, y: 0, vx: 0, vy: 0,
          color: f.deadRoute ? css.getPropertyValue("--danger").trim() : (f.evidenceZoneIds?.length ? css.getPropertyValue("--ok").trim() : css.getPropertyValue("--muted").trim()),
        };
        nodes.push(n); byId.set(f.id, n);
      }
    }
    const links: [Node, Node][] = [];
    if (commMode && graphify) {
      for (const l of graphify.links) {
        const a = byId.get(l.s), b = byId.get(l.t);
        if (a && b) links.push([a, b]);
      }
    } else if (navMode) {
      for (const e of nv.edges) {
        const a = byId.get(e.from), b = byId.get(e.to);
        if (a && b) links.push([a, b]);
      }
    } else {
      for (const f of showFeatures ? map.features : []) {
        const a = byId.get(f.moduleId), b = byId.get(f.id);
        if (a && b) links.push([a, b]);
      }
    }
    const edgeLabels: { x: number; y: number; text: string }[] = [];
    // 初始布局：小图圆周；大图（社区 745 节点）用向日葵散布，避免挤成一环再炸飞
    const cx = () => W / 2, cy = () => H / 2;
    if (commMode) {
      nodes.forEach((n, i) => {
        const a = i * 2.39996323;  // 黄金角
        const r = 16 * Math.sqrt(i);
        n.x = cx() + r * Math.cos(a); n.y = cy() + r * Math.sin(a);
      });
    } else {
      const mods = nodes.filter((n) => n.kind === "module");
      mods.forEach((m, i) => {
        const a = (i / mods.length) * Math.PI * 2;
        m.x = cx() + 260 * Math.cos(a); m.y = cy() + 220 * Math.sin(a);
      });
    }
    // 0.3.0 物理修正：散布只在节点未定位时进行——社区/导航模式已布好阵，随机重散会摧毁
    // 初始布局并引发斥力风暴；功能点改用黄金角环绕模块均匀铺开（原随机 ±60px 严重重叠）
    const ringCounters = new Map<string, number>();
    for (const [a, b] of links) {
      if (b.x !== 0 || b.y !== 0) continue;
      const k = ringCounters.get(a.id) ?? 0;
      ringCounters.set(a.id, k + 1);
      const ang = k * 2.39996323;
      const rr = 30 + 7 * Math.sqrt(k);
      b.x = a.x + rr * Math.cos(ang); b.y = a.y + rr * Math.sin(ang);
    }

    let alpha = 1;
    const tick = () => {
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d2 = dx * dx + dy * dy || 1;
          if (d2 > 40000) continue;
          // 0.3.0 物理修正：近距斥力封顶——原 1800/d2 在重叠时单对踢速数百/帧，是"乱飞"根源
          const f = Math.min(1800 / d2, 16), d = Math.sqrt(d2);
          a.vx -= (dx / d) * f; a.vy -= (dy / d) * f;
          b.vx += (dx / d) * f; b.vy += (dy / d) * f;
        }
      }
      for (const [a, b] of links) {
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 46) * 0.06;
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
      for (const n of nodes) {
        n.vx += (cx() - n.x) * 0.012; n.vy += (cy() - n.y) * 0.012;
        n.vx *= 0.86; n.vy *= 0.86;
        // 0.3.0 物理修正：速度封顶 + alpha 加速冷却——杜绝持续乱飞，快速收敛到可读布局
        const sp = Math.hypot(n.vx, n.vy);
        if (sp > 12) { n.vx = (n.vx / sp) * 12; n.vy = (n.vy / sp) * 12; }
        n.x += n.vx * alpha; n.y += n.vy * alpha;
      }
      alpha = Math.max(0.02, alpha * 0.985);
    };

    const view = { k: 1, x: 0, y: 0 };
    let hover: Node | null = null, drag: Node | null = null, panning = false, last: [number, number] | null = null;
    const toScreen = (n: Node): [number, number] => [n.x * view.k + view.x, n.y * view.k + view.y];

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      ctx.globalAlpha = 0.16; ctx.strokeStyle = INK; ctx.lineWidth = 1;
      edgeLabels.length = 0;
      for (const [a, b] of links) {
        const [ax, ay] = toScreen(a), [bx, by] = toScreen(b);
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
        if (navMode) {
          // 箭头（指向目标 70% 处）+ 边标签（中点）
          const mx = ax + (bx - ax) * 0.5, my = ay + (by - ay) * 0.5;
          const ex = ax + (bx - ax) * 0.72, ey = ay + (by - ay) * 0.72;
          const ang = Math.atan2(by - ay, bx - ax);
          ctx.globalAlpha = 0.45; ctx.fillStyle = INK;
          ctx.beginPath();
          ctx.moveTo(ex, ey);
          ctx.lineTo(ex - 7 * Math.cos(ang - 0.4), ey - 7 * Math.sin(ang - 0.4));
          ctx.lineTo(ex - 7 * Math.cos(ang + 0.4), ey - 7 * Math.sin(ang + 0.4));
          ctx.fill();
          const e = nv.edges.find((x) => byId.get(x.from) === a && byId.get(x.to) === b);
          if (e) edgeLabels.push({ x: mx, y: my, text: e.label });
          ctx.globalAlpha = 0.16;
        }
      }
      ctx.globalAlpha = 1;
      if (navMode) {
        ctx.font = '10.5px "Microsoft YaHei", sans-serif';
        for (const l of edgeLabels) {
          const w = ctx.measureText(l.text).width + 8;
          ctx.fillStyle = css.getPropertyValue("--panel").trim() || "#151d2e";
          ctx.globalAlpha = 0.85;
          ctx.fillRect(l.x - w / 2, l.y - 8, w, 15);
          ctx.globalAlpha = 0.95; ctx.fillStyle = INK;
          ctx.fillText(l.text, l.x - w / 2 + 4, l.y + 3);
        }
      }
      ctx.globalAlpha = 1;
      ctx.font = '13px "Microsoft YaHei", sans-serif';
      for (const n of nodes) {
        const [x, y] = toScreen(n);
        ctx.beginPath(); ctx.arc(x, y, n.r * view.k, 0, Math.PI * 2);
        ctx.fillStyle = n === hover ? ACCENT : n.color;
        ctx.globalAlpha = n.kind === "module" ? 0.95 : 0.75;
        ctx.fill();
        ctx.globalAlpha = 0.92; ctx.fillStyle = INK;
        ctx.fillText(n.label, x + n.r * view.k + 4, y + 4);
      }
      ctx.globalAlpha = 1;
    };

    const hit = (mx: number, my: number): Node | null => {
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        const [x, y] = toScreen(n);
        const rr = n.r * view.k + 5;
        if ((x - mx) ** 2 + (y - my) ** 2 < rr * rr) return n;
      }
      return null;
    };

    let moved = false;
    const onDown = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const n = hit(mx, my);
      if (n) drag = n; else { panning = true; last = [e.clientX, e.clientY]; }
      ensureLoop();
      moved = false;
    };
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      if (drag) { drag.x = (mx - view.x) / view.k; drag.y = (my - view.y) / view.k; drag.vx = drag.vy = 0; alpha = 0.4; moved = true; }
      else if (panning && last) { view.x += e.clientX - last[0]; view.y += e.clientY - last[1]; last = [e.clientX, e.clientY]; moved = true; }
      else {
        hover = hit(mx, my);
        // C 批：导航图悬停提示可跳转性（有 pageJoin 的节点才可点）
        canvas.style.cursor = hover
          ? ((navMode && !nav?.pageJoin?.[hover.id]?.length) ? "default" : "pointer")
          : "grab";
      }
      ensureLoop();
    };
    const onUp = () => { drag = null; panning = false; last = null; };
    const onClick = () => {
      if (moved || !hover) return;
      if (commMode) {
        // S5：点社区 → 详情面板（成员符号/文件数）
        if (hover.data) cbRef.current.setCommDetail(hover.data);
        return;
      }
      if (navMode) {
        // C 批：导航图节点 → pageJoin 命中的首个采证页（工作台并选中该页）
        const tgt = nav?.pageJoin?.[hover.id]?.[0];
        if (tgt) cbRef.current.gotoNavPage(tgt);
        return;
      }
      if (hover.kind === "feature") cbRef.current.gotoFeature(hover.id); else cbRef.current.selectModule(hover.id);
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const k = view.k * (e.deltaY < 0 ? 1.1 : 1 / 1.1);
      view.x = mx - (mx - view.x) * (k / view.k);
      view.y = my - (my - view.y) * (k / view.k);
      view.k = Math.min(4, Math.max(0.3, k));
      ensureLoop();
    };

    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    let raf = 0, stopped = false;
    // B 批：静止停帧——动能与 alpha 都收敛后停 rAF；交互（拖拽/滚轮/缩放）再唤醒
    const ensureLoop = () => {
      if (stopped) { stopped = false; raf = requestAnimationFrame(loop); }
    };
    const loop = () => {
      tick(); draw();
      const energy = nodes.reduce((s, n) => s + Math.abs(n.vx) + Math.abs(n.vy), 0);
      if (!drag && !panning && energy < Math.max(0.6, nodes.length * 0.02)) { stopped = true; raf = 0; return; }
      raf = requestAnimationFrame(loop);
    };
    loop();
    const onResize = () => { resize(); ensureLoop(); };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [map, nav, graphify, navMode, commMode, showFeatures]);

  return (
    <div ref={wrapRef} style={{ position: "relative", height: "100%", minHeight: 480 }}>
      <canvas ref={canvasRef} style={{ display: "block", cursor: "grab" }} />
      {commDetail && (
        <div className="card" style={{ position: "absolute", top: 10, right: 10, width: 280, opacity: 0.96, padding: 12, fontSize: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b style={{ color: "var(--accent)" }}>{commDetail.label}</b>
            <span className="id-code">{commDetail.id}</span>
            <button className="icon-btn" style={{ marginLeft: "auto", width: 22, height: 22 }} onClick={() => setCommDetail(null)}>×</button>
          </div>
          <div className="muted" style={{ margin: "6px 0" }}>
            {commDetail.size} 个符号 · {commDetail.files} 个文件
          </div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>枢纽符号（度数 Top5）</div>
          {commDetail.top.map((t, i) => (
            <div key={i} className="mono" style={{ fontSize: 11, margin: "3px 0", wordBreak: "break-all" }}>· {t}</div>
          ))}
        </div>
      )}
      <div className="card" style={{ position: "absolute", top: 10, left: 10, opacity: 0.92, padding: "8px 12px", fontSize: 12 }}>
        <b>{commMode ? (graphify?.nodes.length ?? 0) : showFeatures ? map.features.length : map.modules.length}</b> 节点
        <div style={{ margin: "4px 0" }}>
          <button className={`btn ${!navMode && !commMode && !showFeatures ? "active" : ""}`} onClick={() => { setNavMode(false); setCommMode(false); setShowFeatures(false); }}>模块级</button>{" "}
          <button className={`btn ${!navMode && !commMode && showFeatures ? "active" : ""}`} onClick={() => { setNavMode(false); setCommMode(false); setShowFeatures(true); }}>展开功能点</button>{" "}
          <button className={`btn ${navMode ? "active" : ""}`} onClick={() => { setCommMode(false); setNavMode(true); }}>导航图</button>{" "}
          <button className={`btn ${commMode ? "active" : ""}`} onClick={() => { setNavMode(false); setCommMode(true); }}>代码社区</button>
        </div>
        {commMode ? (
          <div className="muted" style={{ fontSize: 11 }}>
            代码社区图（{graphify?.nodes.length ?? 0} 社区 · graphify AST）· 点社区看成员符号
          </div>
        ) : navMode ? (
          <div className="muted" style={{ fontSize: 11 }}>页面跳转流（{nav?.edges.length ?? 0} 条边）· 箭头=跳转方向 · 点节点跳对应采证页</div>
        ) : (
          <div>
            <span className="dot" style={{ background: "var(--accent)", display: "inline-block" }} /> 模块
            {showFeatures && <>
              <span className="dot dot-ok" style={{ display: "inline-block" }} /> 有实机采证
              <span className="dot dot-muted" style={{ display: "inline-block" }} /> 未采证
              <span className="dot dot-danger" style={{ display: "inline-block" }} /> 死路由
            </>}
          </div>
        )}
        <div className="muted" style={{ marginTop: 2, fontSize: 11 }}>拖拽节点 / 滚轮缩放 / 点击节点跳转</div>
      </div>
    </div>
  );
}
