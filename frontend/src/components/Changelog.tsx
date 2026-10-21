import type { ChangelogEntry } from "../types";

export default function Changelog({ entries }: { entries: ChangelogEntry[] }) {
  return (
    <>
      <div className="card">
        <h3>版本时间线（新 → 旧）</h3>
        {entries.map((e, i) => (
          <div key={e.version} style={{
            borderLeft: i === 0 ? "3px solid var(--accent)" : "3px solid var(--border)",
            paddingLeft: 14, margin: "14px 0", position: "relative",
          }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--mono)" }}>
                {e.version}{i === 0 && <span className="tag tag-accent" style={{ marginLeft: 8 }}>当前</span>}
              </span>
              <span className="muted">{e.date}</span>
              <span className="badge muted-badge">{e.taskId}</span>
            </div>
            <div style={{ margin: "6px 0" }}>{e.summary}</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
              {e.entries.map((x, j) => <li key={j}>{x}</li>)}
            </ul>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>关于版本机制</h3>
        <div className="muted" style={{ fontSize: 12.5 }}>
          每轮探索/测绘收口 = 一个版本快照（JSON 冻结在 data/versions/ 下）。Update Log
          条目按编号对齐——编号终身制保证跨版本 diff 的可比性。历史版本回看与条目级 diff
          视图在二期提供，当前版本机制已就位。
        </div>
      </div>
    </>
  );
}
