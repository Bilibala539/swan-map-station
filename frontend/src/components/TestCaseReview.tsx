// T6 两道门人审工作台（墨刀 DEMO 同化进主站设计系统）
// 墨刀原件：测试用例人审工作台·两道门审核（task 6aae57c31795c0ce4a1afee2）
// 同化原则：不 iframe——产品设计全量搬运，视觉/交互全走 DS（Stepper/Toast/chip/控制台+队列布局）
// R8.7 对齐：深链 ?view=cases；demo 状态内存级（刷新重置），真实管线接 build_data 后落库
import { useEffect, useMemo, useState } from "react";
import type { CasesData, TestCase } from "../api";
import { loadCases } from "../api";
import { Stepper, useToast } from "../ds";

const GATE_LABEL = { definition: "门一 · 用例定义", script: "门二 · 编译脚本" } as const;
const REJECT_REASONS = ["定义错误", "步骤不可执行", "预期错误", "数据标签缺失"] as const;
const P_COLOR = { P0: "var(--danger)", P1: "var(--warn)", P2: "var(--muted)" } as const;
const REVIEWER = "用例审核员 · 登录与设备域";

type Audit = { time: string; text: string };

// 0.3.0 人审接口：POST /api/review/*——服务端持久化 decisions/audit 并强制两道门；
// 静态托管（无后端）时 fetch 失败静默降级为原有内存 demo 行为。
async function apiPost(path: string, body: Record<string, unknown>): Promise<{ ok?: boolean; error?: string }> {
  try {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { error: (data as { error?: string }).error || `HTTP ${res.status}` };
    return data as { ok?: boolean };
  } catch {
    return { error: "接口不可达" };
  }
}

export default function TestCaseReview({ version, initialQuery }: { version?: string | null; initialQuery?: string }) {
  const toast = useToast();
  const [data, setData] = useState<CasesData | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [auditLog, setAuditLog] = useState<Audit[]>([]);
  // 用例搜索:空=待审队列;有词=全库(编号/标题/模块)匹配——工作站搜用例的入口
  const [q, setQ] = useState(initialQuery ?? "");
  // 顶栏搜索回落:已在用例页时 App 传新词也要生效(props 只在挂载读一次不够)
  useEffect(() => { if (initialQuery != null) setQ(initialQuery); }, [initialQuery]);
  // 驳回弹层状态
  const [rejecting, setRejecting] = useState<"one" | "batch" | null>(null);
  const [reason, setReason] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const [batchConfirm, setBatchConfirm] = useState(false);
  // 批量统一驳回（墨刀：批量仅同批次同原因）
  const [batchReason, setBatchReason] = useState<string>("");
  const [batchNote, setBatchNote] = useState<string>("");
  const [slaFilter, setSlaFilter] = useState<"all" | "urgent" | "overdue">("all"); // DEMO 三档筛选
  const [modFilter, setModFilter] = useState<string>("all"); // 一级模块筛选（全量出库后按模块收敛）
  const [mod2Filter, setMod2Filter] = useState<string>("all"); // 二级分组筛选（moduleL2，如 7.7 平台授权 / 2.2 第三方登录）
  // ── 两道门审阅流（DEMO 真实逻辑）──
  // 每条用例过两门：门一=用例定义审阅、门二=编译脚本审阅；查看即记录 viewed；
  // 「提交审核结论」被两门锁：都看过才可入库（approveBtn.disabled 同款）。
  const [gateTab, setGateTab] = useState<"definition" | "script">("definition");
  const [viewed, setViewed] = useState<Record<string, { definition: boolean; script: boolean }>>({});

  useEffect(() => {
    (async () => {
      try {
        // 优先走人审接口（服务端已合并 decisions/gates）；?v= 按版本取数；静态托管降级 loadCases
        const q = version ? `?v=${encodeURIComponent(version)}` : "";
        const res = await fetch(`/api/review/cases${q}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        setData(d);
        setCases(d.cases);
        setViewed(Object.fromEntries(d.cases.map((c: TestCase) => [c.id, c.gates ?? { definition: false, script: false }])));
        setSelId(d.cases[0]?.id ?? null);
      } catch {
        loadCases().then((d) => {
          setData(d);
          setCases(d.cases);
          setSelId(d.cases[0]?.id ?? null);
        }).catch(() => {});
      }
    })();
  }, [version]);

  const pendingAll = cases.filter((c) => c.status === "pending");
  // 专项伪分组（跨模块聚合）：Tidal / SUP 补充——按 id 前缀匹配
  const SPECIAL_GROUPS: { key: string; label: string; match: (id: string) => boolean }[] = [
    { key: "__tidal__", label: "Tidal 专项", match: (id) => id.startsWith("TIDAL-") || id.startsWith("TD-NEG-") },
    { key: "__sup__", label: "SUP 补充专项", match: (id) => id.startsWith("SUP-AUTH-") },
  ];
  const specialMatch = SPECIAL_GROUPS.find((g) => g.key === mod2Filter);
  const pending = (slaFilter === "all" ? pendingAll : pendingAll.filter((c) => c.sla === slaFilter))
    .filter((c) => modFilter === "all" || c.moduleL1 === modFilter)
    .filter((c) => mod2Filter === "all" || (specialMatch ? specialMatch.match(c.id) : (c.moduleL2 ?? "") === mod2Filter));
  const l2Options = [...new Set(pendingAll
    .filter((c) => modFilter === "all" || c.moduleL1 === modFilter)
    .map((c) => c.moduleL2 ?? ""))].filter(Boolean).sort();
  const specialCounts = SPECIAL_GROUPS.map((g) => ({
    ...g,
    n: pendingAll.filter((c) => (modFilter === "all" || c.moduleL1 === modFilter) && g.match(c.id)).length,
  })).filter((g) => g.n > 0);
  const ql = q.trim().toLowerCase();
  const searching = ql.length > 0;
  const matches = searching
    ? cases.filter((c) => `${c.id} ${c.title} ${c.moduleL1} ${c.moduleL2}`.toLowerCase().includes(ql))
    : [];
  const queue = searching ? matches : pending; // 搜索态跨全库(含已入库/驳回),非搜索态维持待审队列
  const doneToday = cases.length - pending.length;
  const sel = cases.find((c) => c.id === selId) ?? pending[0] ?? null;
  const checkedCases = pending.filter((c) => checked.has(c.id));
  const checkedBatches = [...new Set(checkedCases.map((c) => c.batch))];

  const getView = (id: string) => viewed[id] ?? { definition: false, script: false };
  const markViewed = (id: string, gate: "definition" | "script") => {
    setViewed((v) => ({ ...v, [id]: { ...v[id] ?? { definition: false, script: false }, [gate]: true } }));
    void apiPost("/api/review/gate", { caseId: id, gate, reviewer: REVIEWER, version }); // 服务端留痕；失败保持内存态
  };
  // 选中用例即开始审阅当前门（看到的即已审阅）
  useEffect(() => {
    if (selId) markViewed(selId, gateTab);
  }, [selId]);

  const log = (text: string) => {
    const t = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    setAuditLog((a) => [{ time: t, text }, ...a].slice(0, 8));
  };

  const decide = async (id: string, pass: boolean, why?: string, memo?: string) => {
    const res = await apiPost("/api/review/decision", { caseId: id, pass, reason: why, note: memo, reviewer: REVIEWER, version });
    if (res.error) { toast(`提交失败：${res.error}`, "warn"); return; }
    setCases((cs) => cs.map((c) =>
      c.id === id ? { ...c, status: pass ? "stored" : "rejected", reason: pass ? undefined : why, note: memo } : c));
    log(pass ? `${id} 提交审核结论：两道门均已通过，已入库` : `${id} 驳回（${why}）`);
    toast(pass ? "两门全通过，用例入库" : `已驳回：${why}`, pass ? "ok" : "warn");
    const next = pending.find((p) => p.id !== id);
    if (next && pass) { setSelId(next.id); setGateTab("definition"); }
  };

  const batchDecide = async (pass: boolean) => {
    const ids = checkedCases.map((c) => c.id);
    const res = await apiPost("/api/review/batch", { caseIds: ids, pass, reason: batchReason, note: batchNote, reviewer: REVIEWER, version });
    if (res.error) { toast(`批量失败：${res.error}`, "warn"); return; }
    const idSet = new Set(ids);
    setCases((cs) => cs.map((c) => idSet.has(c.id)
      ? { ...c, status: pass ? "stored" : "rejected", reason: pass ? undefined : batchReason, note: batchNote } : c));
    for (const c of checkedCases) log(pass ? `${c.id} 提交审核结论：两道门均已通过，已入库` : `${c.id} 驳回（${batchReason}）`);
    toast(pass ? `批量通过 ${ids.length} 条，已入库` : `批量驳回 ${ids.length} 条`, pass ? "ok" : "warn");
    setChecked(new Set());
    setRejecting(null);
    setBatchConfirm(false);
  };

  const v = sel ? getView(sel.id) : { definition: false, script: false };
  const bothViewed = v.definition && v.script;
  const gateHint = bothViewed ? "两道门已审阅 · 可提交审核结论"
    : v.definition ? "门一已审阅，待完成门二（编译脚本）审阅"
    : "待完成门一（用例定义）审阅";

  if (!data) return <div className="content-inner"><div className="card ds-skeleton" style={{ height: 300 }} /></div>;

  // policy 兼容(2026-10-15):旧 demo/服务端会给 {baseline,dataLabel} 对象——直接渲染对象触发
  // React #31 全页白屏,统一转人读文本(baseline · dataLabel)
  const policyRaw = (data as unknown as { policy?: unknown }).policy;
  const policyText = typeof policyRaw === "string" ? policyRaw
    : policyRaw && typeof policyRaw === "object"
      ? [String((policyRaw as Record<string, unknown>).baseline ?? ""), String((policyRaw as Record<string, unknown>).dataLabel ?? "")].filter(Boolean).join(" · ")
      : "";

  return (
    <div className="content-inner">
      {/* 顶栏：批次/策略/今日已审/角色（墨刀头部全要素） */}
      <div className="card" style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div>
          <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>当前批次</div>
          <b style={{ fontSize: "var(--fs-lg)" }}>{pending[0]?.batch ?? "—"}</b>
        </div>
        <span className="tag tag-accent">门禁策略 · {policyText || "—"}</span>
        <span className="tag tag-muted">{data.reviewer}</span>
        <span style={{ marginLeft: "auto", fontSize: "var(--fs-sm)" }} className="muted">今日已审 <b style={{ color: "var(--ok)", fontSize: "var(--fs-lg)" }}>{doneToday}</b> / {cases.length}</span>
      </div>

      <div className="workcanvas-card card" style={{ marginTop: 0 }}>
        <div className="workcanvas">
          {/* 左列:待审队列(勾选批量 + 优先级/门/超时)/ 搜索结果 */}
          <div className="wc-ctl">
            <div className="wc-group">
              <div className="wc-group-head">
                {searching ? `搜索「${q.trim()}」(${queue.length})` : `待审队列（${pending.length}）`}
                {!searching && (
                <label style={{ display: "flex", gap: 4, alignItems: "center", fontSize: "var(--fs-xs)", cursor: "pointer" }}>
                  <input type="checkbox" checked={checkedCases.length === pending.length && pending.length > 0}
                    onChange={(e) => setChecked(e.target.checked ? new Set(pending.map((c) => c.id)) : new Set())} /> 全选
                </label>
                )}
              </div>
              <input
                className="search" style={{ width: "100%", marginBottom: 8 }}
                placeholder="搜用例:编号 / 标题 / 模块,如 launch-NEG-001、冷启动"
                value={q} onChange={(e) => setQ(e.target.value)}
              />
              {!searching && (
              <>
              <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
                {([["all", "全部"], ["urgent", "即将超时"], ["overdue", "已超时"]] as const).map(([k, label]) => (
                  <button key={k} className={`btn ${slaFilter === k ? "active" : ""}`} onClick={() => setSlaFilter(k)}>
                    {label}{k !== "all" && pendingAll.filter((c) => c.sla === k).length > 0 ? ` (${pendingAll.filter((c) => c.sla === k).length})` : ""}
                  </button>
                ))}
              </div>
              <div style={{ marginBottom: 8 }}>
                <select value={modFilter} onChange={(e) => { setModFilter(e.target.value); setMod2Filter("all"); }}
                  style={{ width: "100%", padding: "4px 6px", fontSize: "var(--fs-xs)", background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6 }}>
                  <option value="all">全部模块·待审（{pendingAll.length}/全库{cases.length}）</option>
                  {[...new Set(pendingAll.map((c) => c.moduleL1))].sort().map((m) => (
                    <option key={m} value={m}>{m}·待审（{pendingAll.filter((c) => c.moduleL1 === m).length}）</option>
                  ))}
                </select>
              </div>
              {l2Options.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <select value={mod2Filter} onChange={(e) => setMod2Filter(e.target.value)}
                    style={{ width: "100%", padding: "4px 6px", fontSize: "var(--fs-xs)", background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6 }}>
                    <option value="all">全部分组（{pending.filter((c) => mod2Filter === "all" || (specialMatch ? specialMatch.match(c.id) : (c.moduleL2 ?? "") === mod2Filter)).length}）</option>
                    {specialCounts.map((g) => (
                      <option key={g.key} value={g.key}>◆ {g.label}（{g.n}）</option>
                    ))}
                    {l2Options.map((l2) => (
                      <option key={l2} value={l2}>{l2}（{pendingAll.filter((c) => (modFilter === "all" || c.moduleL1 === modFilter) && (c.moduleL2 ?? "") === l2).length}）</option>
                    ))}
                  </select>
                </div>
              )}
              </>
              )}
              {queue.length === 0 && <p className="muted wc-hint">{searching ? "无匹配用例——试试更短的关键词,或用用例编号精确搜索。" : "当前筛选下暂无待审用例——切换筛选条件,或等待批次推送新用例。"}</p>}
              {queue.map((c) => (
                <div key={c.id} className={`queue-card${sel?.id === c.id ? " selected" : ""}`} onClick={() => setSelId(c.id)}>
                  <div className="qc-top">
                    {!searching && (
                    <input type="checkbox" checked={checked.has(c.id)} onClick={(e) => e.stopPropagation()}
                      onChange={(e) => { const s = new Set(checked); e.target.checked ? s.add(c.id) : s.delete(c.id); setChecked(s); }} />
                    )}
                    <span className="mono" style={{ fontSize: "var(--fs-xs)" }}>{c.id}</span>
                    <span className="mono" style={{ color: P_COLOR[c.priority], fontWeight: 700, fontSize: "var(--fs-xs)" }}>{c.priority}</span>
                    <span className="tag tag-muted">{c.level}</span>
                    <span title={c.channel === "positive" ? "正向用例" : c.channel === "auto" ? "自动化脚本用例" : "异常向用例"} style={{ fontSize: 11, color: c.channel === "positive" ? "var(--ok)" : c.channel === "auto" ? "var(--accent)" : "var(--warn)" }}>{c.channel === "positive" ? "✓ 正向" : c.channel === "auto" ? "⚙ 脚本" : "✕ 异常向"}</span>
                    <span style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                      {c.sla === "overdue" && <span className="tag tag-danger">已超时</span>}
                      {c.sla === "urgent" && <span className="tag tag-warn">临期</span>}
                    </span>
                  </div>
                  <div className="qc-title">{c.title}</div>
                  <div className="qc-meta">{(() => { const vv = getView(c.id); return (vv.definition ? "门一✓" : "门一…") + " " + (vv.script ? "门二✓" : "门二…"); })()} · {c.moduleL1}/{c.moduleL2} · {c.batch} · {c.submitTime}</div>
                </div>
              ))}
              {/* 批量操作（同批次校验） */}
              {checkedCases.length > 0 && (
                <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => batchDecide(true)}>批量通过（{checkedCases.length}）</button>
                  <button className="btn" onClick={() => { setRejecting("batch"); setBatchConfirm(false); }}>批量驳回</button>
                  {checkedBatches.length > 1 && <span className="tag tag-warn">批量仅支持同批次（当前 {checkedBatches.length} 个批次）</span>}
                </div>
              )}
            </div>
            {/* 审计日志（不可撤销提示） */}
            <div className="wc-group">
              <div className="wc-group-head">审核日志（写入后不可撤销）</div>
              {auditLog.length === 0 && <p className="muted wc-hint">尚无操作。</p>}
              {auditLog.map((a, i) => (
                <div key={i} className="mono" style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", lineHeight: 1.8 }}>
                  [{a.time}] {a.text}
                </div>
              ))}
            </div>
          </div>

          {/* 右列：当前用例详情 + 两道门 + 操作 */}
          <div className="wc-canvas">
            {sel ? (
              <>
                <div style={{ flex: 1, minWidth: 300 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="badge">{sel.id}</span>
                    <span className="tag" style={{ color: P_COLOR[sel.priority] }}>{sel.priority} · {sel.level}</span>
                    <span className="tag tag-muted">{sel.batch}</span>
                    {sel.featureId && <span className="tag tag-accent mono">{sel.featureId}</span>}
                    <span className="tag tag-muted">{sel.moduleL1} / {sel.moduleL2}</span>
                    <span className="tag" style={{ color: sel.channel === "positive" ? "var(--ok)" : sel.channel === "auto" ? "var(--accent)" : "var(--warn)" }}>{sel.channel === "positive" ? "正向" : sel.channel === "auto" ? "脚本" : "异常向"}</span>
                  </div>
                  <h3 style={{ margin: "10px 0", color: "var(--text-strong)" }}>{sel.title}</h3>
                  <div className="muted" style={{ fontSize: "var(--fs-sm)", lineHeight: 1.9 }}>
                    提交：{sel.submitter} · {sel.submitTime}　预计执行 {sel.estimate || "—"}　超时时限 {sel.deadlineHours != null ? `${sel.deadlineHours}h` : "—"}
                  </div>
                  <div style={{ margin: "16px 0" }}>
                    <Stepper steps={["门一 · 用例定义", "门二 · 编译脚本", "入库"]} current={v.definition ? (v.script ? 2 : 1) : 0} />
                  </div>
                  {/* 两道门审阅 Tab：切换即查看对应维度（DEMO activeTab 语义） */}
                  <div className="tab-bar">
                    <button className={`tab ${gateTab === "definition" ? "active" : ""}`}
                      onClick={() => { setGateTab("definition"); markViewed(sel.id, "definition"); }}>门一 · 用例定义</button>
                    <button className={`tab ${gateTab === "script" ? "active" : ""}`}
                      onClick={() => { setGateTab("script"); markViewed(sel.id, "script"); }}>门二 · 编译脚本</button>
                  </div>
                  {gateTab === "definition" ? (
                    <>
                      {/* 结构化用例字段 grid（DEMO 8 字段） */}
                      <div className="gate-fields">
                        {[["用例编号", sel.id], ["一级模块", sel.moduleL1], ["二级模块", sel.moduleL2], ["优先级", sel.priority],
                          ["用例级别", sel.level], ["用例通道", sel.channel === "positive" ? "正向" : "负向"], ["所属批次", sel.batch], ["预计执行", sel.estimate]]
                          .map(([k, v]) => <div key={k} className="gate-field"><span>{k}</span><b>{v}</b></div>)}
                      </div>
                      {/* 用例步骤表（5 列：步骤号/业务动作/目标定位/测试数据/步骤预期）;降级行(auto/REG 旧代)无 ai 溯源,步骤可空 */}
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <h3>用例步骤 <span className="muted" style={{ fontWeight: 400 }}>共 {sel.definition?.steps?.length ?? 0} 步</span></h3>
                        <div style={{ overflowX: "auto" }}>
                          <table className="gate-steps">
                            <thead><tr><th>步骤号</th><th>业务动作</th><th>目标定位</th><th>测试数据</th><th>步骤预期</th></tr></thead>
                            <tbody>
                              {(sel.definition?.steps ?? []).map((st) => (
                                <tr key={st.no}><td className="mono">{st.no}</td><td>{st.action}</td><td className="mono">{st.locator}</td><td className="mono">{st.data}</td><td>{st.expect}</td></tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div style={{ marginTop: 10 }}>
                          <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>总体预期：</span>
                          <span style={{ fontSize: "var(--fs-sm)" }}>{sel.definition.overallExpect}</span>
                        </div>
                      </div>
                      {/* AI 溯源信息（降级行无 ai 块,整体隐藏） */}
                      {sel.definition?.ai && (
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <h3>AI 溯源信息</h3>
                        <div className="gate-fields">
                          {[["来源", sel.definition.ai.source], ["Prompt", sel.definition.ai.promptVersion], ["模型", sel.definition.ai.model], ["生成时间", sel.definition.ai.generatedAt]].map(([k, v]) => (
                            <div key={k} className="gate-field"><span>{k}</span><b className="mono" style={{ fontSize: "var(--fs-xs)" }}>{v}</b></div>
                          ))}
                        </div>
                        <div style={{ fontSize: "var(--fs-xs)", color: "var(--muted)", lineHeight: 1.8 }}>
                          知识卡：{(sel.definition.ai.cards ?? []).join(" · ")}<br />
                          置信度 <b style={{ color: sel.definition.ai.confidence >= 90 ? "var(--ok)" : "var(--warn)" }}>{sel.definition.ai.confidence}%</b> · {sel.definition.ai.note}
                        </div>
                      </div>
                      )}
                      {/* 审核动作指引（DEMO 三条） */}
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)", borderLeft: "3px solid var(--warn)" }}>
                        <h3>审核动作指引</h3>
                        <ol style={{ margin: 0, paddingLeft: 18, fontSize: "var(--fs-sm)", lineHeight: 1.9 }}>
                          <li>核对步骤与需求条目是否一一对应，勿遗漏分支。</li>
                          <li>确认目标定位在最新页面结构中可被唯一命中。</li>
                          <li>负向用例必须覆盖接口直连等绕过路径。</li>
                        </ol>
                      </div>
                    </>
                  ) : (
                    <>
                      {/* 门二审核要点（DEMO 三项 check，数据隔离按通道动态） */}
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <h3>门二审核要点</h3>
                        <div className="gate-checks">
                          {[["断言完整性", "每条预期在脚本中均有对应 assert 语句", true],
                            ["定位唯一性", "选择器与门一定义中的目标定位一致", true],
                            ["数据隔离", "测试数据通过 Mock / 工厂生成，无硬编码账号", sel.channel === "positive"]].map(([t, d2, ok]) => (
                            <div key={t as string} className="gate-check">
                              <b style={{ color: ok ? "var(--ok)" : "var(--danger)" }}>{ok ? "✓" : "✕"} {t as string}</b>
                              <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>{d2 as string}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                      {sel.script?.code ? (
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                          编译器 {sel.script.compiler} · source_hash <span className="mono">{sel.script.hash}</span> · {sel.script.file}
                        </div>
                        <pre className="code" style={{ marginTop: 6, maxHeight: 260, overflow: "auto" }}>{sel.script.code}</pre>
                      </div>
                      ) : sel.script?.file ? (
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <h3>编译脚本</h3>
                        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>脚本索引 <span className="mono">{sel.script.file}</span>——自动化脚本在代码工程维护,源码与测试方法名见测试平台用例列表。</div>
                      </div>
                      ) : (
                      <div className="card" style={{ margin: "10px 0", background: "var(--panel-2)" }}>
                        <h3>编译脚本</h3>
                        <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>该用例暂无脚本报文——文字用例先过门一定义审,编译脚本随自动化落地补齐。</div>
                      </div>
                      )}
                    </>
                  )}
                  <div style={{ fontSize: "var(--fs-sm)", color: bothViewed ? "var(--ok)" : "var(--muted)", margin: "8px 0" }}>{gateHint}</div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn" disabled={!bothViewed} onClick={() => decide(sel.id, true)} title={bothViewed ? "" : "需先完成两道门审阅"}>提交审核结论（入库）</button>
                    <button className="btn" onClick={() => { setRejecting("one"); setReason(""); setNote(""); }}>驳回</button>
                    <button className="btn" onClick={() => { const next = pending.find((p) => p.id !== sel.id); if (next) { setSelId(next.id); setGateTab("definition"); } }}>下一条 →</button>
                  </div>
                </div>
              </>
            ) : <p className="muted">队列已空。</p>}
          </div>
        </div>
      </div>

      {/* 驳回弹层（单条 / 批量：原因枚举 + 备注≥10字 + 批量确认声明） */}
      {rejecting && (
        <div className="modal-mask" onClick={() => setRejecting(null)}>
          <div className="card modal-body" onClick={(e) => e.stopPropagation()}>
            <h3>{rejecting === "batch" ? `批量驳回复审用例（${checkedCases.length} 条）` : `驳回复审用例 ${sel?.id}`}</h3>
            <div className="muted" style={{ fontSize: "var(--fs-sm)", marginBottom: 10 }}>驳回原因（必选）</div>
            <div className="ds-chipgrid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
              {(rejecting === "batch" ? REJECT_REASONS : REJECT_REASONS).map((r) => (
                <button key={r} className={`btn ${((rejecting === "batch" ? batchReason : reason)) === r ? "active" : ""}`}
                  onClick={() => rejecting === "batch" ? setBatchReason(r) : setReason(r)}>{r}</button>
              ))}
            </div>
            <div className="muted" style={{ fontSize: "var(--fs-sm)", margin: "12px 0 6px" }}>
              备注说明（必填，不少于 10 个字）<span className="mono">{rejecting === "batch" ? batchNote.length : note.length}/200</span>
            </div>
            <textarea className="search" style={{ width: "100%", height: 80, resize: "vertical" }} maxLength={200}
              value={rejecting === "batch" ? batchNote : note}
              onChange={(e) => rejecting === "batch" ? setBatchNote(e.target.value) : setNote(e.target.value)} />
            {rejecting === "batch" ? (
              <>
                <label style={{ display: "flex", gap: 6, alignItems: "flex-start", margin: "10px 0", fontSize: "var(--fs-sm)" }}>
                  <input type="checkbox" checked={batchConfirm} onChange={(e) => setBatchConfirm(e.target.checked)} style={{ marginTop: 3 }} />
                  我已逐条核对以上 {checkedCases.length} 条用例，确认两道门均已通过并批量入库（操作将写入审核日志且不可撤销）。
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn" disabled={!batchReason || batchNote.length < 10 || !batchConfirm || checkedBatches.length > 1}
                    onClick={() => batchDecide(false)}>确认驳回</button>
                  <button className="btn" onClick={() => setRejecting(null)}>取消</button>
                </div>
                {!batchReason && <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 6 }}>先选统一驳回原因</div>}
                {checkedBatches.length > 1 && <div className="tag tag-warn" style={{ marginTop: 6 }}>批量操作仅支持同一批次</div>}
              </>
            ) : (
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <button className="btn" disabled={!reason || note.length < 10}
                  onClick={() => { if (sel) decide(sel.id, false, reason, note); setRejecting(null); }}>确认驳回</button>
                <button className="btn" onClick={() => setRejecting(null)}>取消</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
