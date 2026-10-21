// 侧栏导航（R8.2 布局壳改造：shadcn-admin/Linear 式，icon+label+active 指示条）
import type { ReactNode } from "react";
import type { Meta } from "../types";
import type { ViewName } from "../App";
import type { VersionInfo } from "../api";
import {
  IconGrid, IconBox, IconGraph, IconEye, IconBook, IconAlert, IconRefresh, IconCheck,
} from "./Icons";

const NAV: { key: ViewName; label: string; icon: (p: { size?: number }) => ReactNode }[] = [
  { key: "dashboard", label: "总览", icon: IconGrid },
  { key: "workbench", label: "功能工作台", icon: IconBox },
  { key: "cases", label: "用例人审", icon: IconCheck },
  { key: "graph", label: "图谱", icon: IconGraph },
  { key: "evidence", label: "证据库", icon: IconEye },
  { key: "knowledge", label: "知识库", icon: IconBook },
  { key: "defects", label: "缺陷与裁决", icon: IconAlert },
  { key: "changelog", label: "Update Log", icon: IconRefresh },
];

interface Props {
  meta: Meta;
  view: ViewName;
  onView: (v: ViewName) => void;
  ver?: string | null;                       // 当前选中版本（null=跟随站点指针）
  versions?: VersionInfo[] | null;           // 可切换版本清单（/api/versions）
  serverCurrent?: string;                    // 站点指针版本（下拉默认值）
  onVer: (v: string) => void;                // 切换版本
}

export default function Sidebar({ meta, view, onView, ver, versions, serverCurrent, onVer }: Props) {
  return (
    <aside className="sidebar">
      <nav className="side-nav">
        {NAV.map((n) => (
          <button
            key={n.key}
            className={`side-item ${view === n.key ? "active" : ""}`}
            onClick={() => onView(n.key)}
          >
            <n.icon size={15} />
            <span>{n.label}</span>
          </button>
        ))}
      </nav>
      <div className="side-foot">
        {versions && versions.length > 1 && (
          <div className="foot-line">
            <span>版本</span>
            <select
              value={ver ?? serverCurrent ?? meta.version}
              onChange={(e) => onVer(e.target.value)}
              title="切换版本（用例/地图/审阅记录按版本隔离）"
              style={{ maxWidth: 150, fontSize: 12, padding: "2px 4px", borderRadius: 6 }}
            >
              {versions.map((x) => (
                <option key={x.version} value={x.version}>
                  v{x.version} · {x.cases} 条{x.demo ? " · Demo" : ""}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="foot-line"><span>站点</span><b>v{meta.version}</b></div>
        <div className="foot-line"><span>地图</span><b>{meta.badge.mapVersion}</b></div>
        <div className="foot-line"><span>APK</span><b>{meta.badge.apk.split(" ")[0]}</b></div>
        {meta.demo && <div className="foot-line"><span className="tag tag-warn">Demo</span></div>}
      </div>
    </aside>
  );
}
