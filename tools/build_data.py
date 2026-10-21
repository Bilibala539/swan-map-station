#!/usr/bin/env python3
"""swan-map-web 数据编译器：知识库文档 → 站点版本 JSON + 编号注册表 + 证据资产。

用法：
    python tools/build_data.py                 # 编译当前版本（v2）
    python tools/build_data.py --check         # 只校验源文件存在与解析统计

设计铁律：
1. 编号终身制：ids.json 注册表持久化"内容键→编号"，重跑稳定；新增追加不重排
2. 全部相对路径：站点可整体迁移（data/ 自包含）
3. 容错解析：单条解析失败记入 parse_warnings，不中断
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

# ---------- 路径 ----------
SITE_ROOT = Path(__file__).resolve().parents[1]
DATA = SITE_ROOT / "data"
REGISTRY_PATH = DATA / "registry" / "ids.json"
VERSIONS = DATA / "versions"
ASSETS_EVIDENCE = DATA / "assets" / "evidence"

KNOWLEDGE = Path(r"F:\SwanApp-Toolchain\docs\qa-knowledge")
MAP_MD = KNOWLEDGE / "01-系统认知" / "功能地图.md"
BUG_DIR = KNOWLEDGE / "03-BUG库"
RESULTS = Path(r"F:\SwanApp-Toolchain\results\SWAN-20260910-001-feature-boundary")
MATRIX_MD = RESULTS / "executor" / "states-matrix.md"
REPORT_MD = RESULTS / "report.md"
EVIDENCE_DIR = RESULTS / "executor" / "evidence"

VERSION = "0.2.0"
BUILD_DATE = "2026-09-10"

# 功能地图中的模块节：按稳定标记匹配（计数写进标题也不怕）
NON_MODULE_MARKERS = ("探索边界标注", "变更记录", "覆盖对照表", "裁决汇总", "发现-未确认] 裁决", "待测绘清单")


def is_non_module(title: str) -> bool:
    return any(mk in title for mk in NON_MODULE_MARKERS)

warnings: list[str] = []


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # 损坏自愈：备份坏文件后重建（编号终身制要求操作者知情，而非静默重排）
            backup = REGISTRY_PATH.with_suffix(".json.corrupt")
            shutil.copy2(REGISTRY_PATH, backup)
            raise SystemExit(
                f"[FAIL] ids.json 损坏（{e}）。坏文件已备份至 {backup.name}。\n"
                f"处理：确认后删除 {REGISTRY_PATH.name} 重跑编译（注意：重跑将按当前文档重排编号，违反编号终身制，请人工核对备份文件决定键值保留）。")
    return {"modules": {}, "features": {}, "zones": {}, "states": {}, "counters": {}}


def save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    # 原子替换：构建中途崩溃不会产生半截 JSON
    tmp.replace(REGISTRY_PATH)


def stable_id(reg: dict, kind: str, key: str, prefix: str, width: int = 2) -> str:
    """按内容键取/分配编号。编号终身制：键不变则号不变。"""
    table = reg[kind]
    if key in table:
        return table[key]
    counters = reg["counters"]
    n = counters.get(kind, 0) + 1
    counters[kind] = n
    sid = f"{prefix}{n:0{width}d}"
    table[key] = sid
    return sid


def content_key(*parts: str) -> str:
    raw = "|".join(p.strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + "|" + "|".join(parts)


# 脱敏钩子（2026-09-28 补，09-17 审计遗留）：全文内嵌读取点统一过 mask_pii，
# 公网站点禁止明文手机号。掩码形式与既有口径一致（137****4633）。
# 注意：只用于全文内嵌车道（knowledge/defects/oItems/boundaryNotes）；
# parse_map/parse_matrix 结构化解析车道不动（编号注册表 content_key 虽只散列标题，
# 不冒 ID 漂移风险）。证据二进制/文本转储资产的部署侧掩码见
# E:\SwanApp\scripts\stage_evidence_for_deploy.py（掩码→暂存→复扫→上传）。
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86)?(1[3-9]\d{9})(?!\d)")


def mask_pii(text: str) -> str:
    return PHONE_RE.sub(lambda m: f"{m.group(1)[:3]}****{m.group(1)[-4:]}", text)


# ---------- 1. 功能地图解析 ----------

STATUS_RE = re.compile(r"\[(已考古[^\]]*|已扫描-未考古|发现-未确认|发现-边界外)\]")


def parse_map() -> tuple[list[dict], list[dict], dict]:
    reg = load_registry()
    text = MAP_MD.read_text(encoding="utf-8")
    lines = text.splitlines()

    modules: list[dict] = []
    features: list[dict] = []
    cur_module: dict | None = None
    cur_feature: dict | None = None
    in_changelog = False

    for ln in lines:
        if ln.startswith("## "):
            title = ln[3:].strip()
            in_changelog = title == "变更记录"
            if is_non_module(title):
                cur_module = None
                continue
            mkey = content_key("module", title)
            mid = stable_id(reg, "modules", mkey, "M")
            cur_module = {"id": mid, "name": title, "featureCount": 0}
            modules.append(cur_module)
            cur_feature = None
            continue
        if cur_module is None or in_changelog:
            continue
        if ln.startswith("- ") and not ln.startswith("- 入口：") and not ln.startswith("- 实现："):
            body = ln[2:].strip()
            status_m = STATUS_RE.search(body)
            status = status_m.group(1) if status_m else ""
            name = STATUS_RE.sub("", body).strip().rstrip("－—- ").strip()
            if not name:
                continue
            fkey = content_key("feature", cur_module["name"], name)
            fid = stable_id(reg, "features", fkey, cur_module["id"] + "-F")
            cur_feature = {
                "id": fid, "moduleId": cur_module["id"], "name": name,
                "status": status, "entries": [], "implementations": [],
                "notes": [], "deadRoute": "死路由" in body,
            }
            features.append(cur_feature)
            cur_module["featureCount"] += 1
        elif cur_feature is not None and ln.startswith("  - "):
            sub = ln[4:].strip()
            if sub.startswith("入口："):
                cur_feature["entries"].append(sub[3:].strip())
            elif sub.startswith("实现："):
                cur_feature["implementations"].append(sub[3:].strip())
            else:
                cur_feature["notes"].append(sub)

    save_registry(reg)
    return modules, features, reg


# ---------- 2. 状态矩阵解析 ----------

SEC_RE = re.compile(r"^## (\d+)\.\s*(.+?)\s*$")
ANCHOR_RE = re.compile(r"([a-zA-Z_][\w/]*_(?:button|field|text|page|checkbox|icon|bar|card|tab|entry|list))")


def parse_matrix(reg: dict) -> list[dict]:
    text = MATRIX_MD.read_text(encoding="utf-8")
    pages: list[dict] = []
    cur_page: dict | None = None
    cur_zone: dict | None = None

    skipped_rows = 0
    for ln in text.splitlines():
        m = SEC_RE.match(ln.strip())
        if m:
            num, title = m.groups()
            paren = title  # 锚点/bounds 从整行提取
            page_name = title.split("·")[0].strip()
            # B 批：截断括注修补（如「area 选择归属地页（U4」→ 补全括号）
            if page_name.count("（") > page_name.count("）"):
                page_name += "）"
            zone_name = title.split("·", 1)[1].strip() if "·" in title else title
            if zone_name.count("（") > zone_name.count("）"):
                zone_name += "）"
            zkey = content_key("zone", page_name, zone_name)
            zid = stable_id(reg, "zones", zkey, "Z", 3)
            # 找所属功能点：锚点或页面名与地图条目对齐由前端做，这里先记原始页名
            if cur_page is None or cur_page["name"] != page_name:
                cur_page = {"name": page_name, "source": "vm", "zones": []}  # 本轮全部来自模拟器 emulator-5554
                pages.append(cur_page)
            col_state, col_file = 1, 2  # 新小节重置列映射为老四列默认
            cur_zone = {
                "id": zid, "section": int(num), "title": zone_name,
                "anchors": ANCHOR_RE.findall(paren),
                "bounds": re.findall(r"\[(\d+,\d+)\]\[(\d+,\d+)\]", paren),
                "raw": paren, "states": [], "linkage": "",
            }
            cur_page["zones"].append(cur_zone)
            continue
        if cur_zone is None:
            continue
        if ln.startswith("|"):
            # B 批：分隔行用整行形态判定（含 :---: 变体），不再用 "---" 子串误伤
            _s = ln.strip()
            if re.fullmatch(r"\|(\s*:?-{2,}:?\s*\|)+", _s if _s.endswith("|") else _s + "|"):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            # 0.3.0 修正：表头驱动列映射——老四列（# 状态 文件 …）与 U5 五列（# 功能区 状态 文件 备注）统一适配
            if "状态" in cells:
                col_state = cells.index("状态")
                col_file = cells.index("文件") if "文件" in cells else None
                continue
            if cells and re.fullmatch(r"\d+|-", cells[0]):
                state = cells[col_state] if col_state < len(cells) else ""
                if col_file is not None and col_file < len(cells):
                    file_cell = cells[col_file]
                else:  # 无「文件」列：从状态列之后的单元格整体找文件名
                    file_cell = " ".join(cells[col_state + 1:])
                notes = "；".join(c for c in cells[(col_file + 1) if col_file is not None else col_state + 1:] if c)
                # B 批：扩展名大小写不敏感 + jpg + 带括注路径
                files = re.findall(r"[\w\-/()（）]+\.(?:png|xml|mp4|txt|jpe?g)", file_cell, re.I)
                skey = content_key("state", cur_zone["id"], state, file_cell)
                sid = stable_id(reg, "states", skey, cur_zone["id"] + "-S", 2)
                cur_zone["states"].append({
                    "id": sid, "state": state,
                    "files": files, "notes": notes,
                })
            elif len(cells) > 1 and re.search(r"\.(?:png|xml|mp4|txt|jpe?g)", " ".join(cells), re.I):
                skipped_rows += 1  # 含证据文件名却未按状态行解析——疑似坏行，记数告警
        elif ln.startswith("联动结论") or ln.startswith("可见性切换"):
            cur_zone["linkage"] = ln.strip()

    if skipped_rows:
        warnings.append(f"状态矩阵：{skipped_rows} 行含证据文件名但未按状态行解析（多为其它表格内容行，请人工确认）")

    # B 批：同名页合并——非相邻同名页（如分片两次测绘的「login 页」）合到首现位置，页签不再重复
    merged: dict[str, dict] = {}
    pages_out: list[dict] = []
    for p in pages:
        tgt = merged.get(p["name"])
        if tgt is None:
            merged[p["name"]] = p
            pages_out.append(p)
            continue
        for z in p["zones"]:
            twin = next((x for x in tgt["zones"] if x["id"] == z["id"]), None)
            if twin is None:
                tgt["zones"].append(z)
            else:  # 极少见：同名区两段测绘都有状态行 → 状态按 id 去重并入
                have = {s["id"] for s in twin["states"]}
                twin["states"].extend(s for s in z["states"] if s["id"] not in have)
    save_registry(reg)
    return pages_out


def attach_note_section_files(pages: list[dict], reg: dict) -> int:
    """0.3.0 数据修正：采证说明节（无状态表、以「文件：」行/列表引用截图与语义 dump）
    → 生成单条「整页采证」合成状态，使整页测绘截图进入工作台画布与证据库引用账。
    文件仅取资产库存在者（后续存在性过滤兜底）；flutter_debug_tabme_* 式通配按前缀展开。"""
    text = MATRIX_MD.read_text(encoding="utf-8")
    zone_by_sec: dict[int, dict] = {}
    for p in pages:
        for z in p["zones"]:
            zone_by_sec.setdefault(z["section"], z)
    assets_known = {f.name for f in ASSETS_EVIDENCE.rglob("*") if f.is_file()}
    sections: list[tuple[int, list[str]]] = []
    cur_sec, body = 0, []
    for ln in text.splitlines():
        m = SEC_RE.match(ln.strip())
        if m:
            if cur_sec:
                sections.append((cur_sec, body))
            cur_sec, body = int(m.group(1)), []
        elif cur_sec:
            body.append(ln)
    if cur_sec:
        sections.append((cur_sec, body))
    added = 0
    for num, body in sections:
        zone = zone_by_sec.get(num)
        if zone is None or zone["states"]:
            continue  # 小节没有对应区，或已解析出状态行（U5 式），不重复挂
        files: list[str] = []
        for ln in body:
            for tok in re.findall(r"[\w\-]+\.(?:png|jpe?g|xml|mp4|txt)", ln, re.I):
                if tok not in files:
                    files.append(tok)
            for pref in re.findall(r"[\w\-]+_\*", ln):  # 通配族：flutter_debug_tabme_*
                files.extend(f for f in sorted(assets_known)
                             if f.startswith(pref[:-1]) and f not in files)
        existing = [f for f in files if f in assets_known]
        images = [f for f in existing if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        existing = images + [f for f in existing if f not in images]  # 截图排前，画布首图不落空
        if not existing:
            continue
        note = next((l.lstrip("- ").strip() for l in body if l.strip().startswith(("- ", "文件："))), "")
        skey = content_key("state", zone["id"], "整页采证", existing[0])
        sid = stable_id(reg, "states", skey, zone["id"] + "-S", 2)
        zone["states"].append({"id": sid, "state": "整页采证", "files": existing,
                               "notes": note[:150] or "整页采证集（源文档说明节）"})
        added += 1
    # 第二遍：有状态行但全无文件的小节（如「文件：」行 + 无文件列的表）——
    # 「文件：」行顺序与状态行一一对应，按序分配；截图优先占位，dump/余量并入末态。
    linked = 0
    for num, body in sections:
        zone = zone_by_sec.get(num)
        if zone is None or not zone["states"] or any(s["files"] for s in zone["states"]):
            continue
        files: list[str] = []
        for ln in body:
            for tok in re.findall(r"[\w\-]+\.(?:png|jpe?g|xml|mp4|txt)", ln, re.I):
                if tok not in files:
                    files.append(tok)
            for pref in re.findall(r"[\w\-]+_\*", ln):
                files.extend(f for f in sorted(assets_known)
                             if f.startswith(pref[:-1]) and f not in files)
        existing = [f for f in files if f in assets_known]
        if not existing:
            continue
        states = zone["states"]
        imgs = [f for f in existing if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        rest = [f for f in existing if f not in imgs]
        for i, img in enumerate(imgs[:len(states)]):
            states[i]["files"] = [img]
        states[-1]["files"].extend(imgs[len(states):] + rest)
        linked += 1
    save_registry(reg)
    return added, linked


# ---------- 3. 缺陷与问题 ----------

def parse_defects(features: list[dict]) -> dict:
    bugs = []
    for md in sorted(BUG_DIR.glob("*SWAN-BUG-20260910-*.md")):
        text = mask_pii(md.read_text(encoding="utf-8"))  # 脱敏钩子
        bid = md.stem.replace("BUG报告-", "").split("-weibo")[0].split("-password")[0]

        # B 批：多行字段捕获——字段行后的非空续行并入字段，直到下一条 "- " 项或标题
        def field(name: str, _text: str = text) -> str:
            m = re.search(rf"- {name}：", _text)
            if not m:
                return ""
            first = _text[m.end():_text.find("\n", m.end()) if "\n" in _text[m.end():] else len(_text)]
            lines = [first.strip()]
            for ln in _text[m.end():].splitlines()[1:]:
                if ln.startswith("- ") or ln.startswith("## ") or not ln.strip():
                    break
                lines.append(ln.strip())
            return " ".join(lines).strip()

        bugs.append({
            "id": bid,
            "file": md.name,
            "classification": field("分类"),
            "module": field("模块"),
            "phenomenon": field("现象"),
            "evidence": field("证据"),
            "conclusion": field("结论"),
        })
    o_items = []
    if REPORT_MD.exists():
        in_sec = False
        for ln in mask_pii(REPORT_MD.read_text(encoding="utf-8")).splitlines():  # 脱敏钩子
            if ln.startswith("## 3."):
                in_sec = True
                continue
            if in_sec and ln.startswith("## "):
                break
            if in_sec and re.match(r"^- (?:\*\*)?O-\d+", ln.strip()):
                o_items.append(ln.strip("- ").strip(" *"))

    # 边界标注节（功能地图.md「探索边界标注」）——建站预留③：危险区/边界外显式上站
    boundary_notes: list[str] = []
    deep_pending: list[str] = []  # 深考古域占位（保持未考古的功能点）
    if MAP_MD.exists():
        in_boundary = False
        for ln in mask_pii(MAP_MD.read_text(encoding="utf-8")).splitlines():  # 脱敏钩子
            if ln.startswith("## "):
                in_boundary = ln.startswith("## 探索边界标注")
                continue
            if in_boundary and ln.strip().startswith("- "):
                boundary_notes.append(ln.strip("- ").strip())
    for f in features:
        blob = f.get("status", "") + " " + f.get("name", "") + " " + " ".join(f.get("notes", []))
        if "保持未考古" in blob or "行为级深考古" in blob or "行为级待深挖" in blob:
            deep_pending.append(f"{f['id']} {f['name']}")
    return {"bugs": bugs, "oItems": o_items, "boundaryNotes": boundary_notes, "deepPending": deep_pending}



# ---------- 4. 元数据与变更记录 ----------

def build_meta(modules, features, pages, defects, reg) -> dict:
    state_count = sum(len(z["states"]) for p in pages for z in p["zones"])
    zone_count = sum(len(p["zones"]) for p in pages)  # B 批：口径=本次产物计数（与 Dashboard 自算一致）
    return {
        "version": VERSION,
        "demo": True,
        "demoNote": "Demo 版 · 仅做项目验证",
        "buildDate": BUILD_DATE,
        "badge": {
            "mapVersion": "v2",
            "appRepo": "HiVi-Network-FlutterApp@4d1ed7b",
            "coreRepo": "hivi_network_flutter_core@402853a",
            "apk": "5.3.0 (build 32) debug-test",
            "taskId": "SWAN-20260910-001-feature-boundary",
        },
        "stats": {
            "modules": len(modules),
            "features": len(features),
            "zones": zone_count,
            "states": state_count,
            # B 批：口径=站点证据目录实际文件数（rglob is_file），而非源目录全 rglob
            "evidenceFiles": sum(1 for f in ASSETS_EVIDENCE.rglob("*") if f.is_file()) if ASSETS_EVIDENCE.exists() else 0,
            "bugCandidates": len(defects["bugs"]),
            "oItems": len(defects["oItems"]),
        },
    }


def build_changelog() -> list[dict]:
    return [
        {
            "version": "0.2.0", "date": "2026-09-10",
            "taskId": "swan-map-web",
            "summary": "当前仅为项目验证（Demo）——验证知识库→站点数据链路与交互形态，功能与数据完整性以后续正式版本为准。",
            "entries": [
                "功能地图 v2（代码解析 + 9 片模拟器探索）数据接入",
                "页面状态矩阵 / 语义树 / 跳转关系 / 证据库 / 图谱五类视图验证",
                "版本机制与 Update Log 框架验证",
            ],
        },
    ]


# ---------- 5. 证据入库 ----------

# 0.3.0 二轮对账：R6.2 命名纪律的家族前缀 → 矩阵页面（人工核对；顺序敏感，专规则在先）
FAMILY_PAGE_RULES: list[tuple[str, str]] = [
    ("flutter_debug_login_base", "基础态全量采集"),
    ("flutter_debug_area_page", "area 选择归属地页"),
    ("home__xiaowei", "小薇 AI 入口形态"),
    ("home__", "首页顶部频道条联动"),
    ("about__", "关于与升级页测绘"),
    ("area__", "area 选择归属地页"),
    ("codepage__", "发码流程"),
    ("sendcode__", "发码流程"),
    ("device__", "设备页整页"),
    ("help__", "帮助与反馈"),
    ("loginpwd__", "loginpwd 密码登录模式"),
    ("login__", "login 页"),
    ("platform__", "平台行清单与授权状态联动"),
    ("search__", "搜索主链路"),
    ("tabme__", "tab_me 我的页整页"),
    ("appshell__", "AppShell 首页"),
    ("splash__", "splash 启动页"),
    ("chrome-firstrun", "login 页"),  # 政策链接点击撞 Chrome 首启（源文档 section 7 备注记载）
]


def attach_family_evidence(pages: list[dict], reg: dict) -> int:
    """0.3.0 二轮对账第三遍：源文档未逐条指认的采证/语义 dump，按 R6.2 命名家族
    与 DUMP_PAGE_RULES 页面映射归族挂页（页面级「补充采证」状态）。
    宁缺勿造：环境件/ANR 过程件/无规则文件保持未挂载，由证据库如实呈现。"""
    linked = {f for p in pages for z in p["zones"] for s in z["states"] for f in s["files"]}
    supp: dict[str, dict] = {}

    def supp_state(p: dict) -> dict:
        if p["name"] not in supp:
            zone = p["zones"][0]
            skey = content_key("state", zone["id"], "补充采证", "family")
            sid = stable_id(reg, "states", skey, zone["id"] + "-S", 2)
            st = {"id": sid, "state": "补充采证", "files": [],
                  "notes": "按 R6.2 命名家族/语义 dump 页面映射归档（源文档未逐条指认到状态）"}
            zone["states"].append(st)
            supp[p["name"]] = st
        return supp[p["name"]]

    added = 0
    for f in sorted(ASSETS_EVIDENCE.rglob("*")):
        if not f.is_file() or f.name in linked:
            continue
        page_name = None
        if f.suffix.lower() == ".xml":
            kw = resolve_dump_page(f.stem, pages)
            if kw != "其他" and any(p["name"] == kw for p in pages):
                page_name = kw
        if page_name is None:
            for pref, kw in FAMILY_PAGE_RULES:
                if f.name.startswith(pref):
                    page_name = next((p["name"] for p in pages if kw in p["name"]), None)
                    break
        if page_name is None:
            continue
        tgt = next((p for p in pages if p["name"] == page_name), None)
        if tgt is None:
            continue
        supp_state(tgt)["files"].append(f.name)
        added += 1
    for st in supp.values():  # 截图排前，画布首图不落空
        imgs = [f for f in st["files"] if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        st["files"] = imgs + [f for f in st["files"] if f not in imgs]
    save_registry(reg)
    return added


def sync_evidence() -> int:
    if not EVIDENCE_DIR.exists():
        warnings.append(f"证据目录不存在: {EVIDENCE_DIR}")
        return 0
    copied = 0
    for f in EVIDENCE_DIR.rglob("*"):
        if f.is_file():
            rel = f.relative_to(EVIDENCE_DIR)
            dst = ASSETS_EVIDENCE / rel
            # B 批：mtime_ns 一并比较（内容变而大小不变的截图不再漏更新）
            if not dst.exists() or dst.stat().st_size != f.stat().st_size or dst.stat().st_mtime_ns != f.stat().st_mtime_ns:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                copied += 1
    return copied


# 模块 → 采证页关键词（策划映射：人工核对过模块与探索页的对应关系，子串匹配）
MODULE_EVIDENCE_MAP: dict[str, list[str]] = {
    "启动与隐私": ["基础态全量采集", "splash 启动页"],
    "会话与登录": ["login 页", "area 选择归属地", "loginpwd", "发码流程", "U6 轻量复核"],
    "主框架与导航": ["AppShell 首页"],
    "首页（顶 Tab 容器）": ["首页顶部频道条联动"],
    "搜索": ["搜索主链路"],
    "音乐平台聚合": ["平台页整页", "平台行清单", "U3 滚动/空态维度"],
    "电台/播客": ["电台频道深一层"],
    "AI 助手": ["小薇 AI 入口形态"],
    "设备管理与配网": ["设备页整页", "添加设备/配网入口", "U3 辅助维度"],
    "个人中心": ["tab_me 我的页整页", "一级入口点击响应", "U3 退出/清理类入口定位", "person_info_page", "扫一扫"],
    "帮助/关于/反馈": ["帮助与反馈 4 子项", "关于与升级页测绘"],
    # 0.3.0 修正：撤销模块级跨模块粗映射（收藏与曲库/支付与会员 → 一级入口点击响应）——
    # 该页 9 态里仅 1-2 态与这两模块相关，模块级整挂造成 M12-F102 播放历史显示
    # 个人信息修改截图之类的错配。改为功能点级+状态级专属补挂（attach_evidence 尾部
    # FEATURE_EVIDENCE_MAP）：只挂源文档有明确记载的关系，没记载的如实显示无采证。
    "设置/切服/平台通道": ["设置/切服入口定位"],
}


def attach_evidence(features: list[dict], modules: list[dict], pages: list[dict]) -> int:
    """按策划映射把采证页的功能区挂到功能点：feature.evidenceZoneIds / evidencePages。"""
    name2module = {m["name"]: m["id"] for m in modules}
    attached = 0
    for m in modules:
        keywords = MODULE_EVIDENCE_MAP.get(m["name"], [])
        if not keywords:
            continue
        page_names = [p["name"] for p in pages
                      if any(kw in p["name"] or p["name"] in kw for kw in keywords)]
        zone_ids = [z["id"] for p in pages if p["name"] in page_names for z in p["zones"]]
        for f in features:
            if f["moduleId"] == m["id"]:
                f["evidenceZoneIds"] = zone_ids
                f["evidencePages"] = page_names
                attached += len(zone_ids)
    # 0.3.0：功能点级专属补挂——跨模块但有明确记载的证据关系，精确到状态
    FEATURE_EVIDENCE_MAP: dict[str, list[tuple[str, str]]] = {
        "M12-F95": [("一级入口点击响应", "SWAN收藏")],        # 我的收藏 ← tab_me 一级入口"SWAN收藏"态
        "M14-F113": [("一级入口点击响应", "充值与消费记录")],  # 充值 ← tab_me 一级入口"充值与消费记录"态
        "M13-F108": [("一级入口点击响应", "扫一扫")],          # 扫码 ← tab_me 一级入口"扫一扫"态
        # M09：AI 角色主页/AI 历史 ← 首页 AI 频道截图（角色卡+历史对话记录入镜）
        "M09-F65": [("首页顶部频道条联动", "channel_ai")],
        "M09-F67": [("首页顶部频道条联动", "channel_ai")],
    }
    for fid, pairs in FEATURE_EVIDENCE_MAP.items():
        f = next((x for x in features if x["id"] == fid), None)
        if f is None:
            continue
        page_names = [p["name"] for p in pages if any(kw in p["name"] for kw, _ in pairs)]
        state_ids = [s["id"] for p in pages if p["name"] in page_names
                     for z in p["zones"] for s in z["states"]
                     if any(kw2 in s["state"] or any(kw2 in fl for fl in s["files"])
                            for _, kw2 in pairs)]
        zone_ids = sorted({z["id"] for p in pages if p["name"] in page_names for z in p["zones"]
                           if any(any(kw2 in s["state"] or any(kw2 in fl for fl in s["files"])
                                      for _, kw2 in pairs) for s in z["states"])})
        f["evidencePages"] = sorted(set(f.get("evidencePages", [])) | set(page_names))
        f["evidenceZoneIds"] = sorted(set(f.get("evidenceZoneIds", [])) | set(zone_ids))
        f["evidenceStateIds"] = sorted(set(f.get("evidenceStateIds", [])) | set(state_ids))
        attached += len(state_ids)
    return attached


# 证据库前缀 → 展示标签
EVIDENCE_LABELS = {
    "login": "登录页（验证码模式）", "loginpwd": "登录页（密码模式）",
    "tabme": "我的页", "platform": "平台页", "device": "设备页",
    "home": "首页", "search": "搜索", "about": "关于与升级",
    "codepage": "验证码页", "help": "帮助与反馈", "radio": "电台",
    "anr": "ANR 证据", "area": "归属地页", "state": "状态记录",
    "sendcode": "发码流程", "appshell": "AppShell", "chrome": "外部浏览器",
    "env": "环境", "first": "首次启动", "personinfo": "个人信息页",
    "splash": "启动页", "flutter_debug": "flutter_debug 整页采集",
}


def build_evidence(pages: list[dict]) -> dict:
    """全量证据库清单：所有文件按前缀分组，标注被哪些状态引用。"""
    ref_by: dict[str, list[str]] = {}
    for p in pages:
        for z in p["zones"]:
            for s in z["states"]:
                for f in s["files"]:
                    ref_by.setdefault(f, []).append(s["id"])

    files_all = sorted(str(f.relative_to(ASSETS_EVIDENCE)).replace("\\", "/")
                       for f in ASSETS_EVIDENCE.rglob("*") if f.is_file())
    groups: dict[str, list[dict]] = {}
    for name in files_all:
        groups.setdefault(evidence_prefix(name), []).append({
            "name": name,
            "size": (ASSETS_EVIDENCE / name).stat().st_size,
            "image": name.lower().endswith((".png", ".jpg", ".jpeg")),
            "referencedBy": ref_by.get(name, []),
        })
    out_groups = [
        {"prefix": g, "label": EVIDENCE_LABELS.get(g, g), "files": files}
        for g, files in sorted(groups.items())
    ]
    total = sum(len(g["files"]) for g in out_groups)
    unref = sum(1 for g in out_groups for f in g["files"] if not f["referencedBy"])
    return {"groups": out_groups, "stats": {"total": total, "unreferenced": unref}}


def evidence_prefix(name: str) -> str:
    """证据文件 → 分组前缀（与原 build_evidence 内联逻辑一致，供模块映射复用）。"""
    if name.startswith("flutter_debug"):
        return "flutter_debug"
    if "__" in name:
        return name.split("__")[0]
    if "-" in name:
        return name.split("-")[0]
    return Path(name).stem.rsplit("_", 1)[0]


# C 批：模块 → 证据前缀组（策划指定，与前缀组标签同源人工核对）。
# 与状态行推导取并集——多数采证页状态行不带文件，纯推导覆盖过稀。
MODULE_PREFIX_MAP: dict[str, list[str]] = {
    "启动与隐私": ["splash", "first", "env", "state", "flutter_debug"],
    "会话与登录": ["login", "loginpwd", "area", "sendcode", "codepage", "crash", "anr", "logcat", "ui"],
    "主框架与导航": ["appshell", "home"],
    "首页（顶 Tab 容器）": ["home"],
    "搜索": ["search"],
    "音乐平台聚合": ["platform"],
    "电台/播客": ["radio"],
    "AI 助手": ["ui"],
    "设备管理与配网": ["device"],
    "个人中心": ["tabme", "personinfo"],
    "帮助/关于/反馈": ["help", "about", "chrome"],
    "收藏与曲库": ["tabme"],
    "支付与会员": ["tabme"],
}


def build_module_prefixes(modules: list[dict], pages: list[dict]) -> dict[str, list[str]]:
    """C 批：模块 → 证据前缀组。= 策划指定前缀（MODULE_PREFIX_MAP）∪ 本模块采证页状态行实际引用前缀。
    证据库模块过滤与功能点跳转联动用；两层都无的模块不出现在表内。"""
    out: dict[str, list[str]] = {}
    for m in modules:
        # 层1：策划指定前缀组
        prefixes = set(MODULE_PREFIX_MAP.get(m["name"], []))
        # 层2：采证页状态行引用（存在性过滤——matrix 文件碎片不入表）
        keywords = MODULE_EVIDENCE_MAP.get(m["name"], [])
        if keywords:
            page_names = {p["name"] for p in pages
                          if any(kw in p["name"] or p["name"] in kw for kw in keywords)}
            prefixes |= {evidence_prefix(f)
                         for p in pages if p["name"] in page_names
                         for z in p["zones"] for s in z["states"] for f in s["files"]
                         if (ASSETS_EVIDENCE / f).exists()}
        if prefixes:
            out[m["id"]] = sorted(prefixes)
    return out


# 语义树 dump 文件名 → 采证页关键词（顺序匹配，先专后泛；页名解析为矩阵页名原文）
DUMP_PAGE_RULES: list[tuple[str, str]] = [
    ("ui-about", "关于与升级页测绘"),
    ("ui-appshell", "AppShell 首页"),
    ("ui-area-code", "area 选择归属地页"),
    ("ui-area-p", "area 选择归属地页"),
    ("ui-area-top", "area 选择归属地页"),
    ("ui-area-z", "area 选择归属地页"),
    ("area-p", "area 选择归属地页"),
    ("ui-device-add", "添加设备/配网入口"),
    ("ui-device", "设备页整页"),
    ("ui-error-state", "基础态全量采集"),
    ("ui-help", "帮助与反馈 4 子项"),
    ("ui-home", "首页顶部频道条联动"),
    ("ui-login-after", "login 页"),
    ("ui-login", "login 页"),
    ("ui-slice3", "login 页"),
    ("ui-sms-mode", "login 页"),
    ("ui-password", "login 页（密码模式）"),
    ("ui-pwd", "loginpwd 密码登录模式"),
    ("ui-personinfo", "person_info_page 深测绘"),
    ("ui-platform-qq", "平台页整页"),
    ("ui-platform", "平台页整页"),
    ("ui-radio", "电台频道深一层"),
    ("ui-search", "搜索主链路"),
    ("ui-sendcode", "发码流程"),
    ("ui-tabme", "tab_me 我的页整页"),
]


def resolve_dump_page(file_stem: str, pages: list[dict]) -> str:
    for prefix, kw in DUMP_PAGE_RULES:
        if file_stem.startswith(prefix):
            for p in pages:
                if kw in p["name"]:
                    return p["name"]
            return kw
    return "其他"


def build_semantics(pages: list[dict]) -> dict:
    """解析全部 uiautomator 语义树 dump → 交互节点清单（clickable/text/rid/bounds）。"""
    import xml.etree.ElementTree as ET
    out = []
    for xf in sorted(ASSETS_EVIDENCE.glob("*.xml")):
        try:
            root = ET.parse(xf).getroot()
        except ET.ParseError:
            continue
        nodes = []
        clickable = 0
        for n in root.iter("node"):
            is_click = n.get("clickable") == "true"
            is_scroll = n.get("scrollable") == "true"
            text = (n.get("text") or "").strip()
            desc = (n.get("content-desc") or "").strip()
            rid = (n.get("resource-id") or "").strip()
            if not (is_click or is_scroll or text or desc or rid):
                continue  # 只保留交互/有内容节点，纯容器丢弃
            if is_click:
                clickable += 1
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
            nodes.append({
                "clickable": is_click, "scrollable": is_scroll,
                "text": text or desc, "rid": rid.split("/")[-1],
                "class": (n.get("class") or "").split(".")[-1],
                "bounds": m.groups() if m else None,
            })
        out.append({
            "file": xf.name, "page": resolve_dump_page(xf.stem, pages),
            "nodeCount": len(nodes), "clickableCount": clickable,
            "nodes": nodes,
        })
    return {"dumps": out, "stats": {"files": len(out), "nodes": sum(len(o["nodes"]) for o in out)}}


# 跳转/导航边（策划自 9 片探索记录：到达路径 + 交互跳转实证）
NAV_EDGES: list[dict] = [
    {"from": "启动页", "label": "隐私同意后自动", "to": "登录页", "note": "同意标记持久化（O-1）"},
    {"from": "登录页", "label": "点「区号选择」", "to": "归属地页", "note": "行点击回带区号；235 行+A-Z 索引"},
    {"from": "登录页", "label": "发码（下一步）", "to": "验证码页", "note": "4 格输入第 4 位自动提交；跳转反馈慢 O-7"},
    {"from": "验证码页", "label": "登录成功", "to": "AppShell 首页", "note": "tab_me 登录态判据"},
    {"from": "登录页", "label": "点「密码登入」", "to": "密码模式", "note": "互切；手机号跨模式保留"},
    {"from": "登录页", "label": "点协议链接", "to": "外部浏览器", "note": "4 入口同指未部署 URL（O-12）"},
    {"from": "AppShell 首页", "label": "底部 Tab", "to": "首页", "note": "推荐频道为默认"},
    {"from": "AppShell 首页", "label": "底部 Tab", "to": "平台页", "note": ""},
    {"from": "AppShell 首页", "label": "底部 Tab", "to": "设备页", "note": ""},
    {"from": "AppShell 首页", "label": "底部 Tab", "to": "我的页", "note": ""},
    {"from": "首页", "label": "顶部频道条", "to": "音乐频道", "note": "三重联动：内容+搜索词+背景色"},
    {"from": "首页", "label": "顶部频道条", "to": "AI 频道", "note": "AI 蓝底"},
    {"from": "首页", "label": "顶部频道条", "to": "可视音箱频道", "note": "功能不可用空态"},
    {"from": "首页", "label": "顶部频道条", "to": "电台频道", "note": "频道状态跨 Tab 保持"},
    {"from": "首页", "label": "点搜索栏", "to": "搜索页", "note": "placeholder 随频道联动"},
    {"from": "首页", "label": "点小薇入口", "to": "AI 聊天页", "note": "独立全屏页，非弹层"},
    {"from": "我的页", "label": "点用户信息", "to": "个人信息修改页", "note": "⚠ 注销/退出双按钮禁区"},
    {"from": "我的页", "label": "点一级入口", "to": "收藏/充值/家庭/设备管理/帮助/关于", "note": "9 入口点击响应已采证"},
    {"from": "我的页", "label": "点扫一扫", "to": "扫码页", "note": "模拟器无相机流=环境限制（O-9）"},
    {"from": "平台页", "label": "点已授权行体", "to": "平台详情页", "note": "QQ音乐/网易云/酷狗三模板非统一（O-13）"},
    {"from": "平台页", "label": "点未授权 + 钮", "to": "授权引导弹层", "note": "样式因平台而异；行体无响应（O-15）"},
    {"from": "平台页", "label": "点 Spotify 行体", "to": "平台详情页", "note": "Spotify 入口闭合（O-14）"},
    {"from": "搜索页", "label": "输入+搜索", "to": "搜索结果页", "note": "6 Tab+平台分组；返回栈无根防护（O-23）"},
    {"from": "电台列表", "label": "点行体", "to": "全屏播放器", "note": "⚠ P-9-1：自动开播，播放类禁点"},
    {"from": "设备页", "label": "点添加设备", "to": "配网页", "note": "权限序列：定位→附近设备（→通知）"},
]


def build_nav() -> dict:
    return {"edges": NAV_EDGES,
            "pages": sorted({e["from"] for e in NAV_EDGES} | {e["to"] for e in NAV_EDGES})}


# X3 nav↔matrix 命名 join：导航图页名（口语名）→ 状态矩阵采证页名（测绘名）。
# 关键词子串匹配矩阵页名；value 为规则关键词表，命中即关联。外部浏览器/全屏播放器无对应采证页。
NAV_MATRIX_RULES: dict[str, list[str]] = {
    "启动页": ["splash 启动页"],
    "登录页": ["login 页"],
    "归属地页": ["area 选择归属地"],
    "验证码页": ["发码流程"],
    "密码模式": ["loginpwd"],
    "AppShell 首页": ["AppShell 首页"],
    "首页": ["首页顶部频道条"],
    "音乐频道": ["首页顶部频道条"], "AI 频道": ["首页顶部频道条"],
    "可视音箱频道": ["首页顶部频道条"], "电台频道": ["首页顶部频道条"],
    "电台列表": ["电台频道深一层"],
    "搜索页": ["搜索主链路"], "搜索结果页": ["搜索主链路"],
    "AI 聊天页": ["小薇 AI 入口形态"],
    "我的页": ["tab_me 我的页整页"],
    "个人信息修改页": ["person_info_page"],
    "扫码页": ["扫一扫"],
    "平台页": ["平台页整页"], "授权引导弹层": ["平台页整页"],
    "平台详情页": ["平台行清单"],
    "设备页": ["设备页整页"],
    "配网页": ["添加设备/配网入口"],
}


def build_nav_join(pages: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    page_names = [p["name"] for p in pages]
    for nav_page, kws in NAV_MATRIX_RULES.items():
        hits = [pn for pn in page_names if any(kw in pn for kw in kws)]
        if hits:
            out[nav_page] = hits
    return out


def build_module_edges(nav_edges: list[dict], page_join: dict[str, list[str]], features: list[dict]) -> list[dict]:
    """模块级图谱的边：导航边（页面级）经 pageJoin→采证页→归属模块 聚合为模块对。
    输出 [{a, b, w}]（w=支撑该模块对的导航边数），供 GraphView 模块模式连线。"""
    page2mods: dict[str, set] = {}
    for f in features:
        for p in f.get("evidencePages", []) or []:
            page2mods.setdefault(p, set()).add(f["moduleId"])
    agg: dict[tuple, int] = {}
    for e in nav_edges:
        mods_a: set = set()
        for pn in page_join.get(e["from"], []):
            mods_a |= page2mods.get(pn, set())
        mods_b: set = set()
        for pn in page_join.get(e["to"], []):
            mods_b |= page2mods.get(pn, set())
        for ma in mods_a:
            for mb in mods_b:
                if ma != mb:
                    key = (min(ma, mb), max(ma, mb))
                    agg[key] = agg.get(key, 0) + 1
    return [{"a": k[0], "b": k[1], "w": v} for k, v in sorted(agg.items())]


# ---------- 4.5 知识库 ingestion（swan vault → 站点） ----------

KNOWLEDGE_VAULT = Path(r"E:\SwanApp\uiautotest\knowledge\swan")
KNOWLEDGE_ASSETS = DATA / "assets" / "knowledge"

WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


def build_knowledge() -> dict:
    """知识库 markdown 原样入库：pages/ + digest/ + README，含 [[链接]] 关系图。"""
    if not KNOWLEDGE_VAULT.exists():
        warnings.append(f"知识库目录不存在: {KNOWLEDGE_VAULT}")
        return {"pages": [], "stats": {"files": 0}}
    if KNOWLEDGE_ASSETS.exists():
        shutil.rmtree(KNOWLEDGE_ASSETS)
    entries = []
    for rel in ["README.md"]:
        src = KNOWLEDGE_VAULT / rel
        if src.exists():
            entries.append((rel, src))
    for sub in ("pages", "digest"):
        d = KNOWLEDGE_VAULT / sub
        if d.is_dir():
            # C 批：递归收——digest/features、digest/flows 等子层一并入站（rel 路径保留层级）
            for src in sorted(d.rglob("*.md")):
                entries.append((f"{sub}/{src.relative_to(d).as_posix()}", src))
    pages = []
    for rel, src in entries:
        text = mask_pii(src.read_text(encoding="utf-8", errors="replace"))  # 脱敏钩子：公网站点禁明文手机号
        dst = KNOWLEDGE_ASSETS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        title = re.match(r"^#\s+(.+)", text)
        links = sorted({l.split("|")[0].split("#")[0].replace(".md", "") for l in WIKI_RE.findall(text)})
        pages.append({
            "name": (title.group(1).strip() if title else Path(rel).stem),
            "relPath": rel,
            "links": links,
        })
    return {"pages": pages, "stats": {"files": len(pages)}}


# ---------- 主流程 ----------

def main() -> int:
    # C 批：源路径配置化——迁移重建（R6.1）不再要求盘符布局与本机一致
    global KNOWLEDGE, MAP_MD, BUG_DIR, RESULTS, MATRIX_MD, REPORT_MD, EVIDENCE_DIR, KNOWLEDGE_VAULT
    ap = argparse.ArgumentParser(description="swan-map-web 数据编译器")
    ap.add_argument("--check", action="store_true", help="只校验源文件存在与解析统计")
    ap.add_argument("--knowledge", default=str(KNOWLEDGE), help="qa-knowledge 知识库根目录")
    ap.add_argument("--results", default=str(RESULTS), help="本轮结果目录（states-matrix/evidence 所在）")
    ap.add_argument("--vault", default=str(KNOWLEDGE_VAULT), help="swan 知识 vault 目录")
    ap.add_argument("--graphify", default=r"E:\SwanApp\repos\flutter\hivi_network_flutter_core\graphify-out",
                    help="graphify 输出目录（含 community-graph.json，S5 图谱接入）")
    ap.add_argument("--kg", default=r"G:\SwanApp-Data\exports\graphify\knowledge-graph.json",
                    help="swan-kg 知识图谱 JSON（主模型语义抽取产物）")
    ap.add_argument("--vault-graph", default=r"G:\SwanApp-Data\snapshots\swan-kb-remote-20260914\knowledge\swan\vault-graph.json",
                    help="vault wikilink 图谱（中心机快照，GraphView「知识图谱」模式）")
    ap.add_argument("--cases", default=str(Path(__file__).resolve().parent.parent / "sources" / "cases.source.json"),
                    help="用例人审数据源（sources/cases.source.json，两道门真实用例 198 条，0.3.0 T5）")
    args = ap.parse_args()
    KNOWLEDGE = Path(args.knowledge)
    MAP_MD = KNOWLEDGE / "01-系统认知" / "功能地图.md"
    BUG_DIR = KNOWLEDGE / "03-BUG库"
    RESULTS = Path(args.results)
    MATRIX_MD = RESULTS / "executor" / "states-matrix.md"
    REPORT_MD = RESULTS / "report.md"
    EVIDENCE_DIR = RESULTS / "executor" / "evidence"
    KNOWLEDGE_VAULT = Path(args.vault)

    for p in (MAP_MD, MATRIX_MD, REPORT_MD):
        if not p.exists():
            print(f"[FAIL] 源文件缺失: {p}")
            return 1
    if args.check:
        print("[OK] 源文件齐全")
        return 0

    modules, features, reg = parse_map()
    pages = parse_matrix(reg)
    syn, linked = attach_note_section_files(pages, reg)  # 0.3.0：采证说明节截图挂载
    fam = attach_family_evidence(pages, reg)  # 0.3.0 二轮：命名归族 + dump 页面映射
    if syn or linked or fam:
        print(f"[OK] 采证挂载：合成状态 {syn} · 按序补挂 {linked} 节 · 家族归档 {fam} 文件")
    attach_evidence(features, modules, pages)
    defects = parse_defects(features)
    # X1 修复：证据同步必须先于消费（build_evidence/build_semantics 读的是 data/assets 拷贝）
    copied = sync_evidence()
    evidence = build_evidence(pages)
    evidence["modulePrefixes"] = build_module_prefixes(modules, pages)  # C 批：证据库模块过滤数据源
    semantics = build_semantics(pages)
    nav = build_nav()
    nav["pageJoin"] = build_nav_join(pages)
    nav["moduleEdges"] = build_module_edges(nav["edges"], nav["pageJoin"], features)  # S5：模块级图谱连线
    knowledge = build_knowledge()
    meta = build_meta(modules, features, pages, defects, reg)
    changelog = build_changelog()

    # 证据存在性过滤：剔除表格解析产生的文件名碎片
    for p in pages:
        for z in p["zones"]:
            for s in z["states"]:
                s["files"] = [f for f in s["files"] if (ASSETS_EVIDENCE / f).exists()]

    vdir = VERSIONS / VERSION
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "map.json").write_text(json.dumps(
        {"modules": modules, "features": features}, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "matrix.json").write_text(json.dumps(
        {"pages": pages}, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "defects.json").write_text(json.dumps(defects, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "semantics.json").write_text(json.dumps(semantics, ensure_ascii=False), encoding="utf-8")
    (vdir / "nav.json").write_text(json.dumps(nav, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "knowledge.json").write_text(json.dumps(knowledge, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "changelog.json").write_text(json.dumps(changelog, ensure_ascii=False, indent=1), encoding="utf-8")
    (vdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # 版本指针最后落盘：确保任何时刻读到的指针都指向内容完整的版本目录
    ptr_tmp = DATA / "current.json.tmp"
    ptr_tmp.write_text(json.dumps({"version": VERSION}, ensure_ascii=False), encoding="utf-8")
    ptr_tmp.replace(DATA / "current.json")

    # S5 图谱接入：社区聚合图 + swan-kg 知识图谱并入版本数据（缺失时优雅跳过）
    graphify_src = Path(args.graphify) / "community-graph.json"
    if graphify_src.exists():
        (vdir / "graphify.json").write_text(graphify_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[OK] graphify.json 并入（{graphify_src.stat().st_size // 1024} KB）")
    else:
        warnings.append(f"graphify 社区图缺失: {graphify_src}（跑 tools/build_graphify_view.py 生成）")
    kg_src = Path(args.kg)
    if kg_src.exists():
        (vdir / "kg.json").write_text(kg_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[OK] kg.json 并入（{kg_src.stat().st_size // 1024} KB）")
    else:
        warnings.append(f"知识图谱缺失: {kg_src}")
    vg_src = Path(args.vault_graph)
    if vg_src.exists():
        (vdir / "vault-graph.json").write_text(vg_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[OK] vault-graph.json 并入（{vg_src.stat().st_size // 1024} KB）")
    else:
        warnings.append(f"vault 图谱缺失: {vg_src}（跑 tools/build_vault_graph.py 生成）")
    # 用例人审（0.3.0 T5）：源文件字节级拷贝（源即产物，不重编码，保证与 delivery 快照可 diff）
    cases_src = Path(args.cases)
    if cases_src.exists():
        shutil.copyfile(cases_src, vdir / "cases.json")
        # 0.3.0 人审接口：把 data/review/decisions.json 的人审结果合并进产物
        # （decisions 是运行时用户数据；重编译不丢已审结论。version 不匹配则弃用防串版本）
        dec_path = DATA / "review" / "decisions.json"
        if dec_path.exists():
            try:
                dec = json.loads(dec_path.read_text(encoding="utf-8"))
                if dec.get("version") == VERSION:
                    merged = json.loads((vdir / "cases.json").read_text(encoding="utf-8"))
                    hit = 0
                    for c in merged.get("cases", []):
                        d = dec.get("cases", {}).get(c["id"])
                        if d:
                            c["status"] = d.get("status", c["status"])
                            if d.get("reason"):
                                c["reason"] = d["reason"]
                            if d.get("note"):
                                c["note"] = d["note"]
                            if d.get("reviewer"):
                                c["reviewer"] = d["reviewer"]
                            if d.get("decidedAt"):
                                c["decidedAt"] = d["decidedAt"]
                            hit += 1
                    (vdir / "cases.json").write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
                    print(f"[OK] 人审决策合并 {hit} 条（data/review/decisions.json）")
            except (json.JSONDecodeError, OSError) as e:
                warnings.append(f"人审决策合并失败（保留原始 cases.json）: {e}")
        print(f"[OK] cases.json 并入（{cases_src.stat().st_size // 1024} KB）")
    else:
        warnings.append(f"用例人审数据缺失: {cases_src}（用例人审视图将 404；源=sources/cases.source.json）")

    print(f"[OK] 模块 {meta['stats']['modules']} | 功能点 {meta['stats']['features']} | "
          f"功能区 {meta['stats']['zones']} | 状态 {meta['stats']['states']} | "
          f"BUG 候选 {meta['stats']['bugCandidates']} | O 项 {meta['stats']['oItems']} | "
          f"证据复制 {copied} 个新文件")
    if warnings:
        print("[WARN]", *warnings, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
