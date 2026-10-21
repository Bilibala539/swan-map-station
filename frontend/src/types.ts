// 站点数据类型 —— 与 tools/build_data.py 产出的 JSON 一一对应

export interface MetaBadge {
  mapVersion: string;
  appRepo: string;
  coreRepo: string;
  apk: string;
  taskId: string;
}

export interface Meta {
  version: string;        // 站点版本，如 0.2.0
  demo: boolean;
  demoNote: string;
  buildDate: string;
  badge: MetaBadge;
  stats: {
    modules: number; features: number; zones: number; states: number;
    evidenceFiles: number; bugCandidates: number; oItems: number;
  };
}

export interface Module {
  id: string;          // SW-M01
  name: string;
  featureCount: number;
}

export interface Feature {
  id: string;          // SW-M03-F15
  moduleId: string;
  name: string;
  status: string;      // 已考古-L1 / 已扫描-未考古 / 发现-未确认 / …
  entries: string[];       // 入口（路由/testId）
  implementations: string[]; // 实现锚点（文件:行）
  notes: string[];
  deadRoute: boolean;
  evidenceZoneIds?: string[]; // 策划映射：本模块采证页的功能区编号
  evidencePages?: string[];   // 策划映射：本模块采证页名
  evidenceStateIds?: string[]; // 功能点级专属映射：直达状态（跨模块有明确记载的关系）
}

export interface MapData { modules: Module[]; features: Feature[]; }

export interface ZoneState {
  id: string;          // Z001-S01
  state: string;       // empty / filled / error / normal-focus / 联动组合…
  files: string[];     // 相对 assets/evidence 的文件名
  notes: string;
}

export interface Zone {
  id: string;          // Z001
  section: number;     // states-matrix §N
  title: string;       // 功能区名（含锚点原文）
  anchors: string[];   // resource-id 提取
  bounds: [string, string][]; // [left,top][right,bottom]
  raw: string;
  states: ZoneState[];
  linkage: string;     // 联动结论行
}

export interface MatrixPage { name: string; source: "vm" | "real"; zones: Zone[]; }
export interface MatrixData { pages: MatrixPage[]; }

export interface BugCandidate {
  id: string; file: string; classification: string; module: string;
  phenomenon: string; evidence: string; conclusion: string;
}

export interface DefectsData {
  bugs: BugCandidate[];
  oItems: string[];
  boundaryNotes: string[];
  deepPending?: string[];
}

export interface ChangelogEntry {
  version: string; date: string; taskId: string; summary: string; entries: string[];
}

export interface MapIndexEntry { version: string; date: string; label: string; }
