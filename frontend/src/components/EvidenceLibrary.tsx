import { useEffect, useMemo, useState } from "react";
import { evidenceUrl, loadEvidence } from "../api";
import type { EvFile, EvGroup, EvidenceData } from "../api";
import type { Module } from "../types";
import { IconPlay } from "./Icons";

interface Props {
  modules: Module[];
  moduleId: string | null;
  onModule: (id: string | null) => void;
}

// C 批：证据库模块过滤——模块→前缀组映射由编译器策划指定+状态行推导（evidence.json.modulePrefixes）
export default function EvidenceLibrary({ modules, moduleId, onModule }: Props) {
  const [groups, setGroups] = useState<EvGroup[] | null>(null);
  const [modulePrefixes, setModulePrefixes] = useState<Record<string, string[]> | null>(null);
  const [stats, setStats] = useState<{ total: number; unreferenced: number } | null>(null);
  const [group, setGroup] = useState<string>("ALL");
  const [q, setQ] = useState("");
  const [preview, setPreview] = useState<EvFile | null>(null);
  const [onlyUnref, setOnlyUnref] = useState(false);
  const [onlyVideo, setOnlyVideo] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadEvidence()
      .then((d: EvidenceData) => {
        setGroups(d.groups); setStats(d.stats); setModulePrefixes(d.modulePrefixes ?? {});
      })
      .catch((e) => setError(String(e)));
  }, []);

  const moduleName = modules.find((m) => m.id === moduleId)?.name ?? null;
  const allowedPrefixes = moduleId ? modulePrefixes?.[moduleId] : undefined;

  // X4 修复：useMemo 必须在条件 return 之前（Hooks 顺序恒定，错误路径不再白屏）
  const videoCount = useMemo(() =>
    groups?.reduce((a, g) => a + g.files.filter((f) => f.name.toLowerCase().endsWith(".mp4")).length, 0) ?? 0, [groups]);

  const visible = useMemo(() => {
    if (!groups) return [];
    const out: { g: EvGroup; f: EvFile }[] = [];
    for (const g of groups) {
      if (allowedPrefixes && !allowedPrefixes.includes(g.prefix)) continue;
      if (group !== "ALL" && g.prefix !== group) continue;
      for (const f of g.files) {
        if (onlyUnref && f.referencedBy.length) continue;
        if (onlyVideo && !f.name.toLowerCase().endsWith(".mp4")) continue;
        if (q && !f.name.toLowerCase().includes(q.toLowerCase())) continue;
        out.push({ g, f });
      }
    }
    // 视频置顶：录屏是演示价值最高的证据
    out.sort((a, b) => Number(b.f.name.endsWith(".mp4")) - Number(a.f.name.endsWith(".mp4")));
    return out;
  }, [groups, group, q, onlyUnref, onlyVideo, allowedPrefixes]);

  if (error) return <div style={{ padding: 20, color: "var(--danger)" }}>证据库加载失败：{error}</div>;
  if (!groups) return <div className="muted">加载中…</div>;

  return (
    <>
      <div className="card">
        <h3>证据库（{stats?.total} 个文件 · {stats?.unreferenced} 个未被状态矩阵引用）</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
          <button className={`btn ${!moduleId ? "active" : ""}`} onClick={() => onModule(null)}>
            全部模块
          </button>
          {modules.map((m) => {
            const has = !!modulePrefixes?.[m.id];
            return (
              <button key={m.id} className={`btn ${moduleId === m.id ? "active" : ""}`}
                style={!has ? { opacity: 0.45 } : undefined}
                title={has ? `${modulePrefixes![m.id].length} 个采证组` : "本模块暂无专属采证组"}
                onClick={() => onModule(moduleId === m.id ? null : m.id)}>
                {m.name} {has ? `(${modulePrefixes![m.id].length})` : ""}
              </button>
            );
          })}
        </div>
        {moduleId && (
          <p className="muted" style={{ margin: "0 0 8px", fontSize: 12.5 }}>
            模块过滤：<b>{moduleName}</b>
            {allowedPrefixes
              ? `—— ${allowedPrefixes.length} 个专属采证组`
              : "—— 暂无专属采证组（本轮探索未覆盖），显示全部。"}
          </p>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
          <button className={`btn ${group === "ALL" ? "active" : ""}`} onClick={() => setGroup("ALL")}>
            全部
          </button>
          {groups.map((g) => (
            <button key={g.prefix} className={`btn ${group === g.prefix ? "active" : ""}`}
              style={allowedPrefixes && !allowedPrefixes.includes(g.prefix) ? { opacity: 0.35 } : undefined}
              onClick={() => setGroup(g.prefix)}>
              {g.label} ({g.files.length})
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <input
            className="search" style={{ flex: "0 1 340px", marginLeft: 0 }}
            placeholder="按文件名过滤，如 phone_field"
            value={q} onChange={(e) => setQ(e.target.value)}
          />
          <label style={{ fontSize: 12.5, cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={onlyVideo} onChange={(e) => setOnlyVideo(e.target.checked)} />
            只看视频（{videoCount}）
          </label>
          <label style={{ fontSize: 12.5, cursor: "pointer" }}>
            <input type="checkbox" checked={onlyUnref} onChange={(e) => setOnlyUnref(e.target.checked)} /> 只看未引用
          </label>
          <span className="muted" style={{ fontSize: 12 }}>当前显示 {visible.length} 个</span>
        </div>
      </div>

      <div className="card">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
          {visible.map(({ g, f }) => {
            const isVideo = f.name.toLowerCase().endsWith(".mp4");
            const clickable = f.image || isVideo;
            return (
            <div key={f.name} style={{ cursor: clickable ? "pointer" : "default" }}
              onClick={() => clickable && setPreview(f)}
              title={f.referencedBy.length ? `被 ${f.referencedBy.join(", ")} 引用` : "未被状态矩阵引用"}>
              {f.image ? (
                <img src={evidenceUrl(f.name)} loading="lazy" alt={f.name}
                  style={{ width: "100%", aspectRatio: "9/16", objectFit: "cover", borderRadius: 6, border: "1px solid var(--border)" }} />
              ) : isVideo ? (
                <div style={{
                  width: "100%", aspectRatio: "9/16", borderRadius: 6, border: "1px solid var(--border)",
                  display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                  color: "var(--accent)", gap: 6, background: "var(--panel-2)",
                }}>
                  <IconPlay size={26} />
                  <span className="muted" style={{ fontSize: 10 }}>MP4 录屏 · 点击播放</span>
                </div>
              ) : (
                <div style={{
                  width: "100%", aspectRatio: "9/16", borderRadius: 6, border: "1px dashed var(--border)",
                  display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 11, padding: 8, textAlign: "center",
                }}>{f.name}</div>
              )}
              <div className="mono" style={{ fontSize: 10.5, marginTop: 4, wordBreak: "break-all" }}>
                {f.referencedBy.length
                  ? <span className="tag tag-ok">已引用</span>
                  : <span className="tag tag-warn">未引用</span>}{" "}
                {f.name.length > 34 ? f.name.slice(0, 33) + "…" : f.name}
              </div>
            </div>
            );
          })}
          {visible.length === 0 && <div className="muted">无匹配文件</div>}
        </div>
      </div>

      {preview && (
        <div style={{
          position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 100,
          display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 10,
        }} onClick={() => setPreview(null)}>
          {preview.name.toLowerCase().endsWith(".mp4") ? (
            <video controls autoPlay src={evidenceUrl(preview.name)}
              style={{ maxHeight: "78vh", maxWidth: "88vw", borderRadius: 8, background: "#000" }} />
          ) : (
            <img src={evidenceUrl(preview.name)} alt={preview.name}
              style={{ maxHeight: "82vh", maxWidth: "90vw", borderRadius: 8 }} />
          )}
          <div style={{ color: "#fff", fontFamily: "var(--mono)", fontSize: 12 }}>{preview.name}</div>
          <div style={{ color: "#aaa", fontSize: 12 }}>
            {preview.referencedBy.length ? `被引用：${preview.referencedBy.join(", ")}` : "未被状态矩阵引用"}
          </div>
        </div>
      )}
    </>
  );
}
