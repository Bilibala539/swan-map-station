import { useEffect, useMemo, useState } from "react";
import { knowledgeUrl, loadKnowledge, loadKg } from "../api";
import type { KnowledgeData, KnowledgePage, KnowledgeGraphData, KgEntity } from "../api";

/** 极简 Markdown 渲染：覆盖知识库卡片用到的语法（标题/表格/列表/引用/代码/粗体/[[链接]]） */
function mdToHtml(md: string): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const inline = (s: string) => esc(s)
    .replace(/\[\[([^\]]+)\]\]/g, (_m, t) => `<a href="#" class="wikilink" data-target="${t.split("|")[0].split("#")[0].replace(/\.md$/, "")}">${t}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = md.split(/\r?\n/);
  const out: string[] = [];
  let inCode = false, inTable = false, tableHeadDone = false;
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inTable) { out.push("</tbody></table>"); inTable = false; }
      out.push(inCode ? "</code></pre>" : "<pre><code>"); inCode = !inCode; continue;
    }
    if (inCode) { out.push(esc(line)); continue; }
    const t = line.trim();
    if (t.startsWith("|") && t.endsWith("|")) {
      const cells = t.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // 分隔行
      if (!inTable) { out.push('<table class="tbl"><thead>'); inTable = true; tableHeadDone = false; }
      if (!tableHeadDone) {
        out.push(`<tr>${cells.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`);
        tableHeadDone = true;
      } else {
        out.push(`<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`);
      }
      continue;
    }
    if (inTable) { out.push("</tbody></table>"); inTable = false; }
    if (t === "") out.push("");
    else if (t.startsWith("### ")) out.push(`<h3>${inline(t.slice(4))}</h3>`);
    else if (t.startsWith("## ")) out.push(`<h2>${inline(t.slice(3))}</h2>`);
    else if (t.startsWith("# ")) out.push(`<h1>${inline(t.slice(2))}</h1>`);
    else if (t.startsWith("> ")) out.push(`<blockquote>${inline(t.slice(2))}</blockquote>`);
    else if (/^[-*] /.test(t)) out.push(`<li>${inline(t.slice(2))}</li>`);
    else if (/^\d+\. /.test(t)) out.push(`<li>${inline(t.replace(/^\d+\. /, ""))}</li>`);
    else out.push(`<p>${inline(t)}</p>`);
  }
  if (inTable) out.push("</tbody></table>");
  return out.join("\n").replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");
}

interface Props { onBack: () => void; }

export default function KnowledgeView({ onBack }: Props) {
  const [data, setData] = useState<KnowledgeData | null>(null);
  const [current, setCurrent] = useState<KnowledgePage | null>(null);
  const [md, setMd] = useState<string>("");
  const [mdError, setMdError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  // S5 实体图谱模式（swan-kg/0.1）；?kg=1 深链直达
  const [mode, setMode] = useState<"docs" | "kg">(() => new URLSearchParams(location.search).get("kg") === "1" ? "kg" : "docs");
  const [kg, setKg] = useState<KnowledgeGraphData | null>(null);
  const [kgEntId, setKgEntId] = useState<string | null>(null);
  const [kgQ, setKgQ] = useState("");
  useEffect(() => {
    if (mode === "kg" && !kg) loadKg().then(setKg).catch(() => {});
  }, [mode, kg]);
  const kgEnt = kg?.entities.find((e) => e.id === kgEntId) ?? null;
  const kgRel = useMemo(() => (kg && kgEnt
    ? kg.relations.filter((r) => r.from === kgEnt.id || r.to === kgEnt.id)
    : []), [kg, kgEnt]);
  const entName = (id: string) => kg?.entities.find((e) => e.id === id)?.name ?? id;

  useEffect(() => {
    // B 批：网络失败/非 2xx 落入错误态，不再永远「加载中」
    loadKnowledge()
      .then((d: KnowledgeData) => {
        setData(d);
        const first = d.pages.find((p) => p.relPath === "README.md") ?? d.pages[0] ?? null;
        if (first) setCurrent(first);
      })
      .catch((e) => setMdError(String(e)));
  }, []);

  useEffect(() => {
    if (!current) return;
    setMd("");
    setMdError(null);
    fetch(knowledgeUrl(current.relPath))
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then(setMd)
      .catch((e) => setMdError(String(e)));
  }, [current]);

  const resolve = (target: string): KnowledgePage | null => {
    if (!data) return null;
    const t = target.replace(/\.md$/, "");
    return data.pages.find((p) => p.name === t)
      // C 批：嵌套 relPath（digest/features/x.md）取文件名 stem 匹配，不再只剥一层目录
      ?? data.pages.find((p) => (p.relPath.split("/").pop() ?? "").replace(/\.md$/, "") === t)
      ?? data.pages.find((p) => p.name.includes(t) || t.includes(p.name.replace(/^swan_/, "")))
      ?? null;
  };

  const grouped = useMemo(() => {
    if (!data) return { readme: [], pages: [], digest: [] } as Record<string, KnowledgePage[]>;
    return {
      readme: data.pages.filter((p) => p.relPath === "README.md"),
      pages: data.pages.filter((p) => p.relPath.startsWith("pages/")),
      digest: data.pages.filter((p) => p.relPath.startsWith("digest/")),
    };
  }, [data]);

  const ListItem = ({ p }: { p: KnowledgePage }) => (
    <div
      className={`tree-feature ${current?.relPath === p.relPath ? "sel" : ""}`}
      onClick={() => setCurrent(p)}
    >
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
    </div>
  );

  return (
    <div className="workspace">
      <div className="left" style={{ width: 260 }}>
        <div style={{ display: "flex", gap: 4, padding: 10 }}>
          <button className={`btn ${mode === "docs" ? "active" : ""}`} style={{ flex: 1 }} onClick={() => setMode("docs")}>文档</button>
          <button className={`btn ${mode === "kg" ? "active" : ""}`} style={{ flex: 1 }} onClick={() => setMode("kg")}>实体图谱</button>
        </div>
        {mode === "docs" && data && (
          <>
            <div className="tree-module open"><span className="id-code">总览</span></div>
            {grouped.readme.map((p) => <ListItem key={p.relPath} p={p} />)}
            <div className="tree-module open"><span className="id-code">页面卡片（{grouped.pages.length}）</span></div>
            {grouped.pages.filter((p) => !q || p.name.includes(q)).map((p) => <ListItem key={p.relPath} p={p} />)}
            {grouped.digest.length > 0 && (
              <>
                <div className="tree-module open"><span className="id-code">摘要索引</span></div>
                {grouped.digest.map((p) => <ListItem key={p.relPath} p={p} />)}
              </>
            )}
            <div style={{ padding: "10px 12px" }}>
              <input className="search" style={{ width: "100%", marginLeft: 0 }} placeholder="过滤页面卡片"
                value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </>
        )}
        {mode === "kg" && kg && (() => {
          const types = [...new Set(kg.entities.map((e) => e.type))];
          const hit = kg.entities.filter((e) =>
            !kgQ || e.name.includes(kgQ) || e.summary.includes(kgQ) ||
            e.codeAnchors.some((a) => a.toLowerCase().includes(kgQ.toLowerCase())));
          return (
            <>
              {types.map((t) => (
                <div key={t}>
                  <div className="tree-module open"><span className="id-code">{t}（{hit.filter((e) => e.type === t).length}）</span></div>
                  {hit.filter((e) => e.type === t).map((e) => (
                    <div key={e.id} className={`tree-feature ${kgEntId === e.id ? "sel" : ""}`} onClick={() => setKgEntId(e.id)}>
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.name}</span>
                    </div>
                  ))}
                </div>
              ))}
              <div style={{ padding: "10px 12px" }}>
                <input className="search" style={{ width: "100%", marginLeft: 0 }} placeholder="过滤实体/锚点"
                  value={kgQ} onChange={(e) => setKgQ(e.target.value)} />
              </div>
            </>
          );
        })()}
        {mode === "kg" && !kg && <div className="muted" style={{ padding: 16 }}>知识图谱加载中…</div>}
      </div>
      <div className="center">
        {mode === "docs" && (current ? (
          <>
            <div className="card" style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button className="btn" onClick={onBack}>← 工作台</button>
              <b>{current.name}</b>
              <span className="id-code">{current.relPath}</span>
              {current.links.length > 0 && <span className="muted" style={{ fontSize: 11.5 }}>出链 {current.links.length} 条</span>}
            </div>
            {mdError ? (
              <div className="card" style={{ color: "var(--danger)" }}>知识文档加载失败：{mdError}</div>
            ) : md ? (
              <div className="card kb-md" style={{ lineHeight: 1.7 }}
                onClick={(e) => {
                  const a = (e.target as HTMLElement).closest("a.wikilink");
                  if (a) {
                    e.preventDefault();
                    const target = resolve(a.getAttribute("data-target") || "");
                    if (target) setCurrent(target);
                  }
                }}
                dangerouslySetInnerHTML={{ __html: mdToHtml(md) }} />
            ) : (
              <div className="card muted">文档加载中…</div>
            )}
          </>
        ) : (
          <p className="muted">加载中…</p>
        ))}

        {mode === "kg" && kg && (
          <div className="fade-in">
            <div className="card" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <button className="btn" onClick={onBack}>← 工作台</button>
              <b>知识图谱（swan-kg/0.1）</b>
              <span className="id-code">{kg.entities.length} 实体 · {kg.relations.length} 关系 · {kg.imageConstants?.length ?? 0} 图像常量</span>
            </div>

            {/* 矛盾与缺口：体检报告 TOP 的图谱视图 */}
            <div className="card">
              <h3>矛盾（{kg.contradictions.length}）—— 待事实裁决</h3>
              {kg.contradictions.map((c) => (
                <div key={c.id} style={{ marginBottom: 10, borderLeft: "3px solid var(--danger)", paddingLeft: 10 }}>
                  <b>{c.id} {c.topic}</b>
                  <div style={{ fontSize: "var(--fs-sm)", marginTop: 2 }}>A：{c.sideA}</div>
                  <div style={{ fontSize: "var(--fs-sm)" }}>B：{c.sideB}</div>
                  <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>影响：{c.impact} · {c.status}</div>
                </div>
              ))}
              <h3 style={{ marginTop: 12 }}>覆盖缺口（{kg.gaps.length}）</h3>
              {kg.gaps.map((g) => (
                <div key={g.id} style={{ marginBottom: 4 }}>
                  <span className="tag tag-warn">{g.id}</span> {g.topic}
                  <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>（{g.impact}）</span>
                </div>
              ))}
            </div>

            {/* 实体详情 */}
            {kgEnt ? (
              <div className="card">
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span className="badge">{kgEnt.type}</span>
                  <b style={{ fontSize: "var(--fs-lg)" }}>{kgEnt.name}</b>
                  <span className="id-code">{kgEnt.doc}</span>
                </div>
                <p style={{ margin: "8px 0" }}>{kgEnt.summary}</p>
                {kgEnt.codeAnchors.length > 0 && (
                  <div style={{ margin: "6px 0" }}>
                    <span className="muted">代码锚点：</span>
                    {kgEnt.codeAnchors.map((a) => <span key={a} className="tag tag-accent mono">{a}</span>)}
                  </div>
                )}
                <h3 style={{ marginTop: 12 }}>关系（{kgRel.length}）</h3>
                {kgRel.length === 0 && <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>无关系记录。</div>}
                {kgRel.map((r, i) => {
                  const other = r.from === kgEnt.id ? r.to : r.from;
                  const arrow = r.from === kgEnt.id ? "→" : "←";
                  return (
                    <div key={i} className="risk-row" style={{ padding: "6px 8px" }} onClick={() => setKgEntId(other)}>
                      <span className="mono">{arrow} {r.type}</span>
                      <b>{entName(other)}</b>
                      <span className="muted" style={{ flex: 1, fontSize: "var(--fs-xs)" }}>「{r.evidence}」</span>
                      <span className="id-code">{r.doc}</span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="card empty-state">
                <p className="muted">左侧选择一个实体查看其关系；矛盾与缺口已列于上方。</p>
              </div>
            )}
          </div>
        )}
        {mode === "kg" && !kg && <div className="card muted">知识图谱加载中…</div>}
      </div>
    </div>
  );
}
