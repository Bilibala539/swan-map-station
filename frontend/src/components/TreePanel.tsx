import { useState } from "react";
import type { MapData, MatrixData } from "../types";
import type { Selection } from "../App";

interface Props {
  map: MapData;
  matrix: MatrixData;
  sel: Selection;
  onSelect: (moduleId: string, featureId: string | null) => void;
}

function statusDot(status: string, deadRoute: boolean): string {
  if (deadRoute) return "dot dot-danger";
  if (status.includes("已考古")) return "dot dot-ok";
  if (status.includes("未确认")) return "dot dot-warn";
  return "dot dot-muted";
}

export default function TreePanel({ map, matrix, sel, onSelect }: Props) {
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(sel.moduleId ? [sel.moduleId] : []),
  );

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const zoneCountByPage = new Map<string, number>();
  for (const p of matrix.pages)
    zoneCountByPage.set(p.name, (zoneCountByPage.get(p.name) ?? 0) + p.zones.length);

  return (
    <div className="left">
      {map.modules.map((m) => {
        const feats = map.features.filter((f) => f.moduleId === m.id);
        const isOpen = open.has(m.id);
        return (
          <div key={m.id}>
            <div
              className={`tree-module ${isOpen ? "open" : ""} ${sel.moduleId === m.id && !sel.featureId ? "open" : ""}`}
              onClick={() => { toggle(m.id); onSelect(m.id, null); }}
            >
              <span className="id-code">{m.id}</span>
              <span style={{ flex: 1 }}>{m.name}</span>
              <span className="muted" style={{ fontSize: 11 }}>{m.featureCount}</span>
            </div>
            {isOpen &&
              feats.map((f) => (
                <div
                  key={f.id}
                  className={`tree-feature ${sel.featureId === f.id ? "sel" : ""}`}
                  onClick={() => onSelect(m.id, f.id)}
                  title={f.name}
                >
                  <span className={statusDot(f.status, f.deadRoute)} />
                  <span className="fid">{f.id.replace(/^M\d+-/, "")}</span>
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {f.name}
                  </span>
                </div>
              ))}
          </div>
        );
      })}
      <div style={{ padding: "14px 12px", fontSize: 11.5 }} className="muted">
        页面采证：{matrix.pages.length} 页 ·{" "}
        {[...zoneCountByPage.values()].reduce((a, b) => a + b, 0)} 功能区
        <br />图例：<span className="dot dot-ok" style={{ display: "inline-block" }} />已考古
        &nbsp;<span className="dot dot-muted" style={{ display: "inline-block" }} />未考古
        &nbsp;<span className="dot dot-warn" style={{ display: "inline-block" }} />未确认
        &nbsp;<span className="dot dot-danger" style={{ display: "inline-block" }} />死路由
      </div>
    </div>
  );
}
