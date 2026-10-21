import type { DefectsData, MapData } from "../types";

interface Props {
  defects: DefectsData;
  featureIndex: Map<string, { feature: MapData["features"][0]; moduleName: string }>;
  gotoFeature: (id: string) => void;
}

export default function Defects({ defects, featureIndex, gotoFeature }: Props) {
  const deadRoutes = [...featureIndex.values()].filter((e) => e.feature.deadRoute);

  return (
    <>
      <div className="card">
        <h3>BUG 候选（{defects.bugs.length}）—— 候选-未定级-未提单</h3>
        {defects.bugs.map((b) => (
          <div key={b.id} style={{ marginBottom: 18, borderLeft: "3px solid var(--danger)", paddingLeft: 12 }}>
            <div>
              <span className="badge">{b.id}</span>{" "}
              <span className="tag tag-danger">{b.classification}</span>{" "}
              <span className="muted">{b.module}</span>
            </div>
            <div style={{ margin: "8px 0" }}><b>现象：</b>{b.phenomenon}</div>
            <pre className="code">{b.evidence}</pre>
            <div className="muted" style={{ fontSize: 12.5 }}><b>结论：</b>{b.conclusion}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>边界与危险区标注（功能地图「探索边界标注」节）</h3>
        {defects.boundaryNotes.map((b, i) => (
          <p key={i} style={{ margin: "6px 0" }} dangerouslySetInnerHTML={{
            __html: b
              .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
              .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>"),
          }} />
        ))}
        {(defects.deepPending ?? []).length > 0 && (
          <>
            <h3 style={{ marginTop: 14 }}>深考古域占位（{(defects.deepPending ?? []).length} 条）—— 行为级待深挖</h3>
            {(defects.deepPending ?? []).map((d, i) => (
              <div key={i} style={{ marginBottom: 4 }}>
                <span className="tag tag-warn">行为级待深挖</span> {d}
              </div>
            ))}
          </>
        )}
      </div>

      <div className="card">
        <h3>O 项候选问题（{defects.oItems.length}）—— 观察记录，不判定</h3>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {defects.oItems.map((o, i) => (
            <li key={i} style={{ marginBottom: 6 }}>{o}</li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h3>死路由警示（{deadRoutes.length} 条功能点）—— 写用例前必读</h3>
        {deadRoutes.map(({ feature, moduleName }) => (
          <div key={feature.id} style={{ marginBottom: 6 }}>
            <span className="tag tag-danger">{feature.id}</span>
            <span className="muted">[{moduleName}]</span>{" "}
            <span style={{ cursor: "pointer" }} onClick={() => gotoFeature(feature.id)}>{feature.name}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>待裁决池</h3>
        <ul style={{ margin: 0, paddingLeft: 18, color: "var(--warn)" }}>
          <li>软件升级（检查更新）点击 —— 触发更新检查请求</li>
          <li>账号绑定管理（疑含解绑）</li>
          <li>用户反馈新建表单（写操作）</li>
          <li>消费明细页（财务只读边界）</li>
          <li>新建歌单（写操作）</li>
          <li>F-2 服务端核对（登录请求是否已发出）</li>
        </ul>
      </div>
    </>
  );
}
