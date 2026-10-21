// 数据加载 —— 全部相对路径（迁移安全：数据可随站点目录整体搬移）
import type {
  Meta, MapData, MatrixData, DefectsData, ChangelogEntry,
} from "./types";

export interface SemNode {
  clickable: boolean; scrollable: boolean; text: string; rid: string;
  "class": string; bounds: [string, string, string, string] | null;
}
export interface SemDump {
  file: string; page: string; nodeCount: number; clickableCount: number; nodes: SemNode[];
}
export interface SemanticsData { dumps: SemDump[]; stats: { files: number; nodes: number }; }
export interface NavEdge { from: string; label: string; to: string; note: string; }
export interface ModuleEdge { a: string; b: string; w: number; }
export interface NavData { edges: NavEdge[]; pages: string[]; pageJoin?: Record<string, string[]>; moduleEdges?: ModuleEdge[]; }

// 当前版本：URL ?v= 覆盖 > data/current.json 指针（版本切换器写 override，全站取数跟随）
declare const __BUILD_TS__: number;
let VER: string | null = null;
let VER_OVERRIDE: string | null = null;
try { VER_OVERRIDE = new URLSearchParams(location.search).get("v"); } catch { /* 非浏览器环境 */ }
const B = __BUILD_TS__;

export const setVersionOverride = (v: string | null) => { VER_OVERRIDE = v; };
export const getVersionOverride = () => VER_OVERRIDE;

async function ver(): Promise<string> {
  if (VER_OVERRIDE) return VER_OVERRIDE;
  if (VER === null) {
    // 相对路径：根路径部署与子路径挂载（/swan-map-station/）均正确解析
    const res = await fetch(`data/current.json?b=${B}`);
    VER = ((await res.json()) as { version: string }).version;
  }
  return VER;
}

export interface VersionInfo {
  version: string; current: boolean; date: string; demo: boolean; cases: number; apk: string;
}
export const loadVersions = async (): Promise<{ current: string; versions: VersionInfo[] }> => {
  const res = await fetch(`/api/versions?b=${B}`);
  if (!res.ok) throw new Error(`/api/versions: HTTP ${res.status}`);
  return res.json() as Promise<{ current: string; versions: VersionInfo[] }>;
};

async function getJson<T>(rel: string): Promise<T> {
  const v = await ver();
  const sep = rel.includes("?") ? "&" : "?";
  const res = await fetch(`data/versions/${v}/${rel}${sep}b=${B}`);
  if (!res.ok) throw new Error(`${rel}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const loadMeta = () => getJson<Meta>("meta.json");
export const loadMap = () => getJson<MapData>("map.json");
export const loadMatrix = () => getJson<MatrixData>("matrix.json");
export const loadDefects = () => getJson<DefectsData>("defects.json");
export const loadChangelog = () => getJson<ChangelogEntry[]>("changelog.json");
export const loadSemantics = () => getJson<SemanticsData>("semantics.json");
export const loadNav = () => getJson<NavData>("nav.json");

export interface KnowledgePage { name: string; relPath: string; links: string[]; }
export interface KnowledgeData { pages: KnowledgePage[]; stats: { files: number }; }
export const loadKnowledge = () => getJson<KnowledgeData>("knowledge.json");

export const knowledgeUrl = (relPath: string) => `/data/assets/knowledge/${relPath}`;

// ---- S5 图谱接入数据源 ----
export interface CommNode { id: string; label: string; size: number; files: number; top: string[]; }
export interface CommLink { s: string; t: string; w: number; }
export interface GraphifyCommunityData {
  schema: string; built_at_commit?: string; sourceNodes: number; sourceLinks: number;
  nodes: CommNode[]; links: CommLink[];
}
export const loadGraphify = () => getJson<GraphifyCommunityData>("graphify.json");

export interface KgEntity {
  id: string; type: string; name: string; doc: string;
  codeAnchors: string[]; summary: string;
}
export interface KgRelation { from: string; to: string; type: string; evidence: string; doc: string; }
export interface KgContradiction { id: string; topic: string; sideA: string; sideB: string; impact: string; status: string; }
export interface KgGap { id: string; topic: string; impact: string; status: string; }
export interface KnowledgeGraphData {
  schema: string; sources: string[];
  entities: KgEntity[]; relations: KgRelation[];
  contradictions: KgContradiction[]; gaps: KgGap[];
  imageConstants?: string[];
}
export const loadKg = () => getJson<KnowledgeGraphData>("kg.json");

// 中心机快照 vault 的 wikilink 图谱（GraphView「知识图谱」模式）
export interface VaultNode { id: string; name: string; group: string; rel: string; deg: number; }
export interface VaultLink { s: string; t: string; }
export interface VaultGraphData { schema: string; source: string; nodes: VaultNode[]; links: VaultLink[]; }
export const loadVaultGraph = () => getJson<VaultGraphData>("vault-graph.json");

export interface EvFile { name: string; size: number; image: boolean; referencedBy: string[]; }
export interface EvGroup { prefix: string; label: string; files: EvFile[]; }
export interface EvidenceData { groups: EvGroup[]; stats: { total: number; unreferenced: number }; modulePrefixes?: Record<string, string[]>; }
export const loadEvidence = () => getJson<EvidenceData>("evidence.json");

// 采证页名 → 导航页名（跳转关系匹配用；先专后泛：pwd 规则必须在 login 之前）
export function navPageOf(pageName: string): string | null {
  const rules: [RegExp, string][] = [
    [/pwd|密码/i, "密码模式"], [/area|归属地/i, "归属地页"], [/发码|sendcode/i, "验证码页"],
    [/login|登录/i, "登录页"], [/验证码/i, "验证码页"],
    [/appshell/i, "AppShell 首页"], [/tab_me|我的/i, "我的页"], [/平台/i, "平台页"],
    [/设备/i, "设备页"], [/搜索/i, "搜索页"], [/radio|电台/i, "电台列表"],
    [/频道条|首页/i, "首页"], [/splash|启动/i, "启动页"], [/关于/i, "关于与升级页"],
    [/person_info|个人/i, "个人信息修改页"], [/扫一扫/i, "扫码页"], [/配网|添加设备/i, "配网页"],
    [/小薇|AI/i, "AI 聊天页"], [/帮助|反馈/i, "帮助与反馈"], [/基础态/i, "启动页"],
  ];
  for (const [re, page] of rules) if (re.test(pageName)) return page;
  return null;
}

// B 批：保留完整相对路径（sync_evidence 已按相对结构入库，子目录文件可达）
export const evidenceUrl = (file: string) => `/data/assets/evidence/${file}`;

// 状态归一化：把矩阵里的自由文本状态归到四态徽标
export function stateKind(state: string): "empty" | "filled" | "error" | "focus" | "combo" | "other" {
  const s = state.toLowerCase();
  if (s.includes("error") || state.includes("报错") || state.includes("错误")) return "error";
  if (s.includes("empty") || state.includes("空")) return "empty";
  if (s.includes("focus") || state.includes("聚焦")) return "focus";
  if (s.includes("filled") || s.includes("valid") || state.includes("有值")) return "filled";
  if (state.includes("+") || state.includes("点击") || state.includes("tapped")) return "combo";
  return "other";
}

// ── 两道门人审（0.3.0 T5/T6：测试用例审核工作台）──
export interface TestCase {
  id: string; title: string;
  priority: "P0" | "P1" | "P2"; level: string; batch: string; submitter: string;
  submitTime: string; estimate: string; deadlineHours: number; featureId?: string;
  status: "pending" | "approved" | "rejected" | "stored";
  channel: "positive" | "negative" | "auto" | "legacy"; // auto/legacy=降级行(快修全量补显,serve.py _pseudo_entry)
  sla: "normal" | "urgent" | "overdue";
  moduleL1: string; moduleL2: string;
  definition: {                                      // 门一内容：结构化用例定义
    steps: { no: number; action: string; locator: string; data: string; expect: string }[];
    overallExpect: string;                           // 总体预期
    ai: { source: string; cards: string[]; promptVersion: string; model: string; generatedAt: string; confidence: number; note: string }; // AI 溯源
  };
  script: { compiler: string; hash: string; file: string; code: string }; // 门二内容
  reason?: string; note?: string; audit?: string[];
  gates?: { definition: boolean; script: boolean }; // 服务端两道门 viewed 状态（/api/review/cases 合并返回）
  decidedAt?: string;                                // 人审裁决落库时间
}
export interface CasesData { schema: string; note: string; policy: string; reviewer: string; cases: TestCase[]; }
export const loadCases = () => getJson<CasesData>("cases.json");
