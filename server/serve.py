#!/usr/bin/env python3
"""swan-map-web 站点服务器：前端静态产物 + 版本数据 + AIQA JSON API。

    python server/serve.py --host 0.0.0.0 --port 8090

路由：
    /                    前端 SPA（frontend/dist）
    /data/...            版本 JSON 与证据资产（data/ 目录，纯相对路径）
    /api/version         当前版本元数据
    /api/modules         模块清单
    /api/feature/<id>    按编号查询功能点（如 M03-F15）
    /api/zone/<id>       按编号查询功能区（含状态矩阵）
    /api/defects         缺陷候选与 O 项
    /api/changelog       版本变更记录

迁移：整站目录拷贝后以相同命令启动即可，无绝对路径依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

SITE_ROOT = Path(__file__).resolve().parents[1]
DIST = SITE_ROOT / "frontend" / "dist"
DATA = SITE_ROOT / "data"
VERSIONS = DATA / "versions"
REVIEW_DIR = DATA / "review"          # 人审持久化（用户数据，独立于编译产物，重编译不丢）
DECISIONS_PATH = REVIEW_DIR / "decisions.json"
AUDIT_PATH = REVIEW_DIR / "audit.jsonl"
_REVIEW_LOCK = threading.Lock()        # ThreadingHTTPServer 并发写保护

# ---------- 用例共库（2026-10-13 用例域整合 P2） ----------
# 唯一数据库 = 测试平台 /opt/uiautotest/output/dashboard.sqlite3 的 case_defs 表；
# 两个服务（:8080 测试平台 / :8090 本工作站）直连同一库，展示与增删改查打在同一张表上。
# structured 列 = 工作站原始报文全量 JSON（机器读，还原 /api/review/cases 条目）；
# review_meta 列 = 人审状态 JSON（status/reason/note/reviewer/decidedAt/gates）；
# content 列 = 人读渲染；human_confirmed 列 = status=stored 的冗余投影（SQL 可过滤）。
# 2026-10-15 快修：无 structured 行（REG 旧代 76 条 + auto 83 条）降级补显进
# /api/review/cases，工作站与测试平台两端 1:1 可搜；人审读写同样放开到全部行。
CASE_DB = Path(os.environ.get("SWAN_CASE_DB", "/opt/uiautotest/output/dashboard.sqlite3"))
CASE_PRIMARY_VER = os.environ.get("SWAN_CASE_PRIMARY_VER", "0.3.0")   # 走共库的版本；其余（0.2.0 demo）走旧文件只读
_CASE_SOURCE_TAG = "case-api"        # 与测试平台 case_api_client.SOURCE_TAG 同值：本来源行才从共库出


def _case_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CASE_DB), timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 共库 content 渲染（与测试平台 case_api_client 同口径内联，保持双列一致） ----------

def _as_text(value) -> str:
    return "" if value is None else str(value).strip()


def _render_workbench_steps(steps) -> str:
    """definition.steps[{no,action,locator,data,expect}] -> 「1. 动作(定位:x;数据:y)→ 预期:z」。"""
    if not isinstance(steps, list):
        return _as_text(steps)
    lines: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            lines.append(_as_text(step))
            continue
        no = str(step.get("no") or "").strip()
        action = _as_text(step.get("action"))
        detail = ";".join(
            f"{label}:{value}"
            for label, key in (("定位", "locator"), ("数据", "data"))
            for value in (_as_text(step.get(key)),)
            if value and value not in ("无", "—")
        )
        expect = _as_text(step.get("expect"))
        head = f"{no}. {action}" if no else action
        if detail:
            head += f"({detail})"
        if expect:
            head += f"→ 预期:{expect}"
        lines.append(head)
    return "\n".join(line for line in lines if line)


def _build_workbench_content(raw: dict) -> str:
    """structured 条目 -> 人读 content（步骤 + 总预期 + 脚本索引行）。"""
    definition = raw.get("definition") if isinstance(raw.get("definition"), dict) else {}
    parts: list[str] = []
    steps = _render_workbench_steps(definition.get("steps"))
    if steps:
        parts.append(f"步骤:\n{steps}")
    overall = _as_text(definition.get("overallExpect"))
    if overall:
        parts.append(f"预期:{overall}")
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    script_file = _as_text(script.get("file"))
    if script_file:
        tail = f"脚本:{script_file}"
        script_hash = _as_text(script.get("hash"))
        if script_hash:
            tail += f"(hash {script_hash[:12]})"
        parts.append(tail)
    return "\n\n".join(parts)


_MODULE_L1_RE = re.compile(r"^M(\d+)\s*(.+)$")


def _module_slug(module_l1: str) -> str:
    """'M01 安装与首启' -> 'm01'（与测试平台迁移脚本同规则）；不匹配返回原串。"""
    matched = _MODULE_L1_RE.match(str(module_l1 or "").strip())
    return f"m{int(matched.group(1)):02d}" if matched else str(module_l1 or "").strip()


_CASES_META: dict | None = None


def _cases_top_meta() -> dict:
    """旧 cases.json 顶层元数据（schema/note/policy/reviewer）：启动后首访读一次缓存。
    正文已不再来自编译产物，但顶层字段随文件保持，前端无感。"""
    global _CASES_META
    if _CASES_META is None:
        try:
            data = json.loads((VERSIONS / CASE_PRIMARY_VER / "cases.json").read_text(encoding="utf-8"))
            _CASES_META = {k: data.get(k) for k in ("schema", "note", "policy", "reviewer")}
        except (OSError, json.JSONDecodeError):
            _CASES_META = {k: None for k in ("schema", "note", "policy", "reviewer")}
    return _CASES_META


def _case_rows() -> list[dict]:
    """共库用例行 -> 工作站条目（structured 全量 + review_meta 叠加）。
    无 structured 行（REG 旧代 text / auto 脚本用例，共 159 条）降级渲染为伪条目，
    保证工作站与测试平台两端 1:1 可搜可看（2026-10-15 快修）。"""
    conn = _case_conn()
    try:
        module_names = {r["slug"]: r["name_cn"] for r in
                        conn.execute("SELECT slug, name_cn FROM modules")}
        rows = conn.execute(
            "SELECT case_id, case_type, module, title, priority, content, file_path,"
            " structured, review_meta FROM case_defs"
            " ORDER BY CASE WHEN case_type='auto' THEN 3 WHEN module LIKE 'reg-%' THEN 2 ELSE 1 END,"
            " COALESCE((SELECT sort_order FROM modules WHERE modules.slug=case_defs.module), 99), case_id"
        ).fetchall()
    finally:
        conn.close()
    cases: list[dict] = []
    for row in rows:
        try:
            raw = json.loads(row["structured"]) if row["structured"] else {}
        except json.JSONDecodeError:
            raw = {}
        if raw:
            raw.setdefault("id", row["case_id"])
        else:
            raw = _pseudo_entry(row, module_names)
        try:
            meta = json.loads(row["review_meta"]) if row["review_meta"] else {}
        except json.JSONDecodeError:
            meta = {}
        for key in ("status", "reason", "note", "reviewer", "decidedAt"):
            if key in meta and meta[key] is not None:
                raw[key] = meta[key]
        raw["gates"] = meta.get("gates") or {"definition": False, "script": False}
        cases.append(raw)
    return cases


_REG_SECTION_RE = re.compile(r"^(前置条件|测试步骤|预期结果)\s*[:：]\s*(.*)$")


def _parse_reg_content(content: str) -> dict:
    """REG 旧代三段式正文 -> definition（步骤按行拆分；xlsx 导入的行内 ' | ' 先折行）。"""
    sections: dict[str, list[str]] = {}
    cur = ""
    for line in _as_text(content).replace(" | ", "\n").splitlines():
        m = _REG_SECTION_RE.match(line.strip())
        if m:
            cur = m.group(1)
            sections.setdefault(cur, [])
            if m.group(2).strip():
                sections[cur].append(m.group(2).strip())
        elif cur:
            sections[cur].append(line.strip())
    steps = []
    for text in sections.get("测试步骤", []):
        if not text:
            continue
        steps.append({"no": len(steps) + 1, "action": re.sub(r"^\d+[、.．)]\s*", "", text),
                      "locator": "", "data": "", "expect": ""})
    return {"precondition": "\n".join(sections.get("前置条件", [])),
            "steps": steps,
            "overallExpect": "\n".join(sections.get("预期结果", [])),
            "cleanup": ""}


def _pseudo_entry(row, module_names: dict) -> dict:
    """无 structured 行 -> 工作站条目降级渲染：REG 按 reg-NN 模块组解析正文，auto 挂脚本索引。
    仅补显可搜可看；正文形态与工作站报文非同构，编辑请走测试平台。"""
    is_auto = row["case_type"] == "auto"
    if is_auto:
        module_l1 = "AUTO 自动化用例"
    else:
        slug = _as_text(row["module"])
        module_l1 = f"{slug.upper()} {module_names.get(slug, '')}".strip() if slug else "REG 旧代回归"
    file_path = _as_text(row["file_path"])
    return {
        "id": row["case_id"],
        "formerId": "",
        "title": _as_text(row["title"]),
        "priority": _as_text(row["priority"]),
        "level": "",
        "batch": "",
        "submitter": "legacy-import" if not is_auto else "py-tests",
        "submitTime": "",
        "estimate": "",
        "featureId": "",
        "status": "pending",
        "moduleL1": module_l1,
        "moduleL2": "",
        "channel": "auto" if is_auto else "legacy",
        "sla": "normal",
        "definition": _parse_reg_content(row["content"]) if not is_auto
                      else {"precondition": "", "steps": [], "overallExpect": "", "cleanup": ""},
        "script": {"file": file_path} if is_auto and file_path else None,
        "pitId": "",
        "dimension": "",
        "baseline": "",
        "dataLabel": "",
        "degraded": True,
    }


def _case_meta_map() -> tuple[dict, dict]:
    """共库 -> (cases 人审裁决 map, gates 门记录 map)，供 /api/review/state 组装。
    2026-10-15 起含全部行（REG 旧代/auto 降级补显后同样可人审）。"""
    conn = _case_conn()
    try:
        rows = conn.execute("SELECT case_id, review_meta FROM case_defs").fetchall()
    finally:
        conn.close()
    cases: dict = {}
    gates: dict = {}
    for row in rows:
        try:
            meta = json.loads(row["review_meta"]) if row["review_meta"] else {}
        except json.JSONDecodeError:
            meta = {}
        if any(k in meta for k in ("status", "reason", "note", "reviewer", "decidedAt")):
            cases[row["case_id"]] = {k: meta[k] for k in
                                     ("status", "reason", "note", "reviewer", "decidedAt") if k in meta}
        if meta.get("gates"):
            gates[row["case_id"]] = meta["gates"]
    return cases, gates


def _case_full_raw(cid: str) -> dict | None:
    """单条 structured 原始条目（不存在返回 None）。"""
    conn = _case_conn()
    try:
        row = conn.execute(
            "SELECT structured FROM case_defs WHERE case_id=? AND case_type='text'", (cid,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["structured"]:
        return None
    try:
        return json.loads(row["structured"])
    except json.JSONDecodeError:
        return None


def current_version() -> str:
    pointer = DATA / "current.json"
    if pointer.exists():
        try:
            return json.loads(pointer.read_text(encoding="utf-8"))["version"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    # 指针缺失/损坏 → 回退扫描版本目录取字典序最大（语义化版本下即最新）
    try:
        candidates = [d.name for d in VERSIONS.iterdir() if d.is_dir()]
        if candidates:
            return max(candidates)
    except OSError:
        pass
    return "0.2.0"


# ---------- 版本管理（0.3.1）：请求级版本切换 + 按版本隔离人审存储 ----------

_VER_RE = re.compile(r"[A-Za-z0-9._-]{1,32}")


def valid_version(v: str) -> bool:
    return bool(_VER_RE.fullmatch(v)) and (VERSIONS / v).is_dir()


def available_versions() -> list[str]:
    try:
        return sorted((d.name for d in VERSIONS.iterdir() if d.is_dir()), reverse=True)
    except OSError:
        return []


def load_json(name: str, ver: str | None = None):
    path = VERSIONS / (ver or current_version()) / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# ---------- 用例人审持久化（0.3.1 起按版本隔离） ----------
# 纪律：两道门全 view 才允许通过入库（服务端强制）；审计日志追加式不可撤销；
# decisions-<版本>.json 按版本独立存储（0.2.0 Demo 与 0.3.0 正式库互不干扰）；
# 旧单文件 decisions.json 首次访问时按用例 id 归属拆分迁移并备份。

def _review_path(ver: str) -> Path:
    return REVIEW_DIR / f"decisions-{ver}.json"


_LEGACY_MIGRATED = REVIEW_DIR / "decisions-legacy.bak.json"


def _migrate_legacy_review() -> None:
    """一次性迁移：旧 decisions.json 里的决定/门记录按用例 id 归属拆到各版本。"""
    if not DECISIONS_PATH.exists() or _LEGACY_MIGRATED.exists():
        return
    try:
        legacy = json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    migrated_any = False
    for ver in available_versions():
        ids = {c["id"] for c in (load_json("cases.json", ver) or {}).get("cases", [])}
        part = {
            "version": ver,
            "cases": {k: v for k, v in legacy.get("cases", {}).items() if k in ids},
            "gates": {k: v for k, v in legacy.get("gates", {}).items() if k in ids},
            "updated": legacy.get("updated"),
        }
        if part["cases"] or part["gates"]:
            try:
                _review_path(ver).write_text(json.dumps(part, ensure_ascii=False, indent=1), encoding="utf-8")
                migrated_any = True
            except OSError:
                return
    if migrated_any:
        try:
            DECISIONS_PATH.replace(_LEGACY_MIGRATED)
        except OSError:
            pass


def _review_load(ver: str) -> dict:
    _migrate_legacy_review()
    p = _review_path(ver)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": ver, "cases": {}, "gates": {}, "updated": None}


def _review_save(state: dict, ver: str) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    state["version"] = ver
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = _review_path(ver).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_review_path(ver))


def _audit_append(entry: dict, ver: str) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    entry["version"] = ver
    entry["time"] = datetime.now().isoformat(timespec="seconds")
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _review_audit_tail(n: int = 80) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _cases_index(ver: str) -> dict[str, dict]:
    cases = (load_json("cases.json", ver) or {}).get("cases", [])
    return {c["id"]: c for c in cases}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 注意：super().__init__ 会同步跑完整个请求（handle()），实例属性必须先于它就位
        self._head_only = False
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, fmt, *args):
        pass

    # B 批：HEAD 支持——监控健康检查不发 body，只看状态码与长度
    def do_HEAD(self):
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _json(self, obj, status: int = HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        # 审查修正：去掉 CORS 通配符——同源前端不需要；避免任意网页静默读取内部 QA 数据
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    # B 批：单区间 Range 支持（mp4 拖动进度条必需；JSON 等小文件无感直读）
    def _send_file(self, target: Path, ctype: str, cache: str):
        size = target.stat().st_size
        start, end, status = 0, size - 1, HTTPStatus.OK
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            try:
                s, e = rng[6:].split("-", 1)
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                else:
                    start, end = max(0, size - int(e)), size - 1
            except ValueError:
                start = 0
            if start >= size:
                body = b""
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = max(0, start), min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if self._head_only or length == 0:
            return
        with target.open("rb") as f:
            f.seek(start)
            self.wfile.write(f.read(length))

    # 请求级版本解析：?v= / ?version= 合法则用之，否则跟随 data/current.json 指针
    def _qver(self) -> str:
        v = (parse_qs(urlparse(self.path).query).get("v") or [None])[0]
        return v if v and valid_version(v) else current_version()

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        ver = self._qver()
        if path == "/api/versions":
            cur = current_version()
            items = []
            for v in available_versions():
                meta = load_json("meta.json", v) or {}
                cases = load_json("cases.json", v) or {}
                items.append({"version": v, "current": v == cur,
                              "date": meta.get("buildDate", ""), "demo": bool(meta.get("demo")),
                              "cases": len(cases.get("cases", [])),
                              "apk": (meta.get("badge") or {}).get("apk", "")})
            return self._json({"current": cur, "versions": items})
        if path == "/api/version":
            return self._json(load_json("meta.json", ver))
        # ── 用例人审接口（0.3.1：?v= 按版本取数；0.3.0 起正式版走共库）──
        if path == "/api/review/state":
            if ver == CASE_PRIMARY_VER:
                cases, gates = _case_meta_map()
                return self._json({"version": ver, "cases": cases, "gates": gates,
                                   "audit": _review_audit_tail()})
            with _REVIEW_LOCK:
                st = _review_load(ver)
            return self._json({"version": st.get("version"), "cases": st.get("cases", {}),
                               "gates": st.get("gates", {}), "audit": _review_audit_tail()})
        if path == "/api/review/cases":
            if ver == CASE_PRIMARY_VER:
                merged = dict(_cases_top_meta())
                merged["cases"] = _case_rows()
                return self._json(merged)
            cases = load_json("cases.json", ver)
            with _REVIEW_LOCK:
                st = _review_load(ver)
            merged = cases or {}
            for c in merged.get("cases", []):
                d = st.get("cases", {}).get(c["id"])
                if d:
                    c.update({k: d[k] for k in ("status", "reason", "note", "reviewer", "decidedAt") if k in d})
                c["gates"] = st.get("gates", {}).get(c["id"], {"definition": False, "script": False})
            return self._json(merged)
        if path == "/api/modules":
            m = load_json("map.json", ver) or {}
            return self._json(m.get("modules", []))
        if path == "/api/defects":
            return self._json(load_json("defects.json", ver))
        if path == "/api/changelog":
            return self._json(load_json("changelog.json", ver))
        if path.startswith("/api/feature/"):
            fid = path.rsplit("/", 1)[1].upper()
            m = load_json("map.json", ver) or {}
            feature = next((f for f in m.get("features", []) if f["id"] == fid), None)
            if feature is None:
                return self._json({"error": f"feature {fid} not found"}, HTTPStatus.NOT_FOUND)
            return self._json(feature)
        if path.startswith("/api/zone/"):
            zid = path.rsplit("/", 1)[1].upper()
            mx = load_json("matrix.json", ver) or {}
            for page in mx.get("pages", []):
                for zone in page.get("zones", []):
                    if zone["id"] == zid:
                        zone["_page"] = page["name"]
                        return self._json(zone)
            return self._json({"error": f"zone {zid} not found"}, HTTPStatus.NOT_FOUND)
        if path.startswith("/data/"):
            # 数据目录透传（JSON + 证据资产），带缓存头
            self.path = self.path  # SimpleHTTPRequestHandler 以 directory=DIST 运行，这里手工处理
            target = (DATA / path[len("/data/"):]).resolve()
            try:
                target.relative_to(DATA)
            except ValueError:
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            if target.is_file():
                ctype = {
                    ".json": "application/json; charset=utf-8",
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".mp4": "video/mp4", ".xml": "text/xml",
                    ".txt": "text/plain; charset=utf-8",
                    ".md": "text/markdown; charset=utf-8",
                }.get(target.suffix.lower(), "application/octet-stream")
                # JSON 禁缓存（数据会随编译更新）；媒体资产可缓存
                cache = "no-cache, no-store, must-revalidate" if target.suffix.lower() == ".json" else "public, max-age=3600"
                self._send_file(target, ctype, cache)
                return
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        # SPA 回退（/assets/ 走 dist 静态目录 = 前端构建产物，勿劫持）
        if not path.startswith("/api") and (DIST / "index.html").exists() and "." not in Path(path).name:
            self.path = "/index.html"
            # index.html 禁缓存：构建后包名变化，缓存旧 HTML 会导致白屏
            self._send_file(DIST / "index.html", "text/html; charset=utf-8", "no-cache, no-store, must-revalidate")
            return
        return super().do_GET()

    # ── 用例人审 POST 接口（0.3.0）──
    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1 << 20:
            raise ValueError("invalid body length")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self):
        path = unquote(self.path.split("?")[0])
        if not path.startswith("/api/review/"):
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        # 用例 CRUD：建用例（共库写 case_defs，非人审不强制 reviewer）
        if path == "/api/review/cases":
            return self._post_case_create()
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
        reviewer = str(body.get("reviewer") or "").strip()
        if not reviewer:
            return self._json({"error": "reviewer 必填"}, HTTPStatus.BAD_REQUEST)
        req_ver = str(body.get("version") or "")
        ver = req_ver if valid_version(req_ver) else current_version()
        # 共库改造：正式版人审直接写 case_defs；演示版本（旧编译产物）只读保留
        if ver != CASE_PRIMARY_VER:
            return self._json({"error": f"版本 {ver} 为演示数据，人审只读（共库仅 {CASE_PRIMARY_VER} 可写）"},
                              HTTPStatus.CONFLICT)
        with _REVIEW_LOCK:
            if path == "/api/review/gate":
                cid, gate = str(body.get("caseId") or ""), str(body.get("gate") or "")
                if gate not in ("definition", "script"):
                    return self._json({"error": "caseId/gate 非法"}, HTTPStatus.BAD_REQUEST)
                row = self._case_row(cid)
                if row is None:
                    return self._json({"error": "caseId 不存在"}, HTTPStatus.BAD_REQUEST)
                meta = row
                g = meta.setdefault("gates", {}) or {}
                g.setdefault("definition", False)
                g.setdefault("script", False)
                g[gate] = True
                meta["gates"] = g
                self._case_update_meta(cid, meta)
                _audit_append({"actor": reviewer, "action": "gate-view", "caseId": cid, "detail": f"门{'一' if gate == 'definition' else '二'}审阅"}, ver)
                return self._json({"ok": True, "gates": g})
            if path in ("/api/review/decision", "/api/review/batch"):
                ids = [str(x) for x in body.get("caseIds", [body.get("caseId")] or []) if x]
                if not ids:
                    return self._json({"error": "caseId 不存在"}, HTTPStatus.BAD_REQUEST)
                rows = self._case_rows_by_ids(ids)
                missing = [cid for cid in ids if cid not in rows]
                if missing:
                    return self._json({"error": "caseId 不存在"}, HTTPStatus.BAD_REQUEST)
                batches = {rows[cid][1].get("batch") for cid in ids}
                if len(batches) > 1:
                    return self._json({"error": f"批量仅支持同批次（当前 {len(batches)} 个批次）"}, HTTPStatus.CONFLICT)
                if body.get("pass"):
                    not_viewed = [cid for cid in ids
                                  if not rows[cid][0].get("gates", {}).get("definition")
                                  or not rows[cid][0].get("gates", {}).get("script")]
                    if not_viewed:
                        return self._json({"error": f"两道门未审阅完毕: {not_viewed[:5]}"}, HTTPStatus.CONFLICT)
                now = datetime.now().isoformat(timespec="seconds")
                status = "stored" if body.get("pass") else "rejected"
                for cid in ids:
                    meta = rows[cid][0]
                    meta["status"] = status
                    meta["reason"] = None if body.get("pass") else str(body.get("reason") or "")
                    meta["note"] = str(body.get("note") or "")
                    meta["reviewer"] = reviewer
                    meta["decidedAt"] = now
                    self._case_update_meta(cid, meta, human_confirmed=1 if status == "stored" else 0)
                for cid in ids:
                    _audit_append({"actor": reviewer, "action": "approve" if body.get("pass") else "reject",
                                   "caseId": cid,
                                   "detail": "两道门均已通过，已入库" if body.get("pass")
                                             else f"驳回（{body.get('reason') or '未填写原因'}）{('；' + body['note']) if body.get('note') else ''}"}, ver)
                return self._json({"ok": True, "decided": ids, "status": status})
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    # ── 共库读写辅助（case_defs 直连；行元组 = (meta, structured_raw)）──

    def _case_row(self, cid: str):
        conn = _case_conn()
        try:
            row = conn.execute(
                "SELECT review_meta FROM case_defs WHERE case_id=?",
                (cid,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            meta = json.loads(row["review_meta"]) if row["review_meta"] else {}
        except json.JSONDecodeError:
            meta = {}
        return meta

    def _case_rows_by_ids(self, ids: list[str]) -> dict:
        """批量取 (case_id -> (meta, structured_raw))；供决策批量校验。"""
        conn = _case_conn()
        out: dict = {}
        try:
            for cid in ids:
                row = conn.execute(
                    "SELECT structured, review_meta FROM case_defs WHERE case_id=?",
                    (cid,)).fetchone()
                if row is None:
                    continue
                try:
                    meta = json.loads(row["review_meta"]) if row["review_meta"] else {}
                    raw = json.loads(row["structured"]) if row["structured"] else {}
                except json.JSONDecodeError:
                    meta, raw = {}, {}
                out[cid] = (meta, raw)
        finally:
            conn.close()
        return out

    @staticmethod
    def _case_update_meta(cid: str, meta: dict, human_confirmed: int | None = None) -> None:
        """人审状态落库：UPDATE case_defs.review_meta（human_confirmed 为 status=stored 投影）。"""
        conn = _case_conn()
        try:
            if human_confirmed is None:
                conn.execute("UPDATE case_defs SET review_meta=? WHERE case_id=?",
                             (json.dumps(meta, ensure_ascii=False), cid))
            else:
                conn.execute("UPDATE case_defs SET review_meta=?, human_confirmed=? WHERE case_id=?",
                             (json.dumps(meta, ensure_ascii=False), human_confirmed, cid))
            conn.commit()
        finally:
            conn.close()

    _CASE_FIELDS = ("title", "priority", "level", "batch", "submitter", "estimate",
                    "featureId", "moduleL1", "moduleL2", "channel", "sla", "pitId",
                    "dimension", "baseline", "dataLabel", "formerId", "definition", "script")

    def _post_case_create(self):
        """建用例：写 case_defs（structured=完整条目，content=渲染，module=slug 化）。
        id 必填——编号终身制，仍由上游注册表/人工发放，服务端不自动发号。"""
        if current_version() != CASE_PRIMARY_VER:
            return self._json({"error": "当前版本非共库正式版"}, HTTPStatus.CONFLICT)
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
        cid = _as_text(body.get("id"))
        title = _as_text(body.get("title"))
        if not cid or not title:
            return self._json({"error": "id/title 必填"}, HTTPStatus.BAD_REQUEST)
        raw = {"id": cid, "title": title, "status": "pending",
               "submitTime": datetime.now().isoformat(timespec="seconds"),
               "gates": {"definition": False, "script": False}}
        for key in self._CASE_FIELDS:
            if key in body and body[key] is not None:
                raw[key] = body[key]
        now = datetime.now().isoformat(timespec="seconds")
        conn = _case_conn()
        try:
            exists = conn.execute("SELECT 1 FROM case_defs WHERE case_id=?", (cid,)).fetchone()
            if exists:
                return self._json({"error": f"用例已存在: {cid}"}, HTTPStatus.CONFLICT)
            try:
                conn.execute(
                    """INSERT INTO case_defs (
                        case_id, case_type, title, module, priority, source,
                        source_bug, file_path, content, linked_case_id,
                        link_strength, human_confirmed, biz_no, case_scope,
                        structured, review_meta, created_at, updated_at
                    ) VALUES (?, 'text', ?, ?, ?, 'case-api', '', '', ?, '', 'strong', 0, '', '', ?, '{}', ?, ?)""",
                    (cid, title, _module_slug(str(raw.get("moduleL1") or "")),
                     _as_text(raw.get("priority")), _build_workbench_content(raw),
                     json.dumps(raw, ensure_ascii=False), now, now))
                conn.commit()
            except sqlite3.IntegrityError:
                return self._json({"error": f"用例已存在: {cid}"}, HTTPStatus.CONFLICT)
        finally:
            conn.close()
        _audit_append({"actor": _as_text(body.get("submitter")) or "workstation",
                       "action": "create-case", "caseId": cid, "detail": f"新建文字用例：{title}"},
                      current_version())
        return self._json({"ok": True, "case": raw})

    def do_PUT(self):
        path = unquote(self.path.split("?")[0])
        if path.startswith("/api/review/cases/"):
            return self._put_case_update(path.rsplit("/", 1)[1])
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _put_case_update(self, cid: str):
        """改正文：白名单字段并入 structured，重算 content，同步 title/module/priority 列。
        人审状态（review_meta）不经此接口——由人审三件套管理。"""
        if current_version() != CASE_PRIMARY_VER:
            return self._json({"error": "当前版本非共库正式版"}, HTTPStatus.CONFLICT)
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
        with _REVIEW_LOCK:
            raw = _case_full_raw(cid)
            if raw is None:
                return self._json({"error": "caseId 不存在"}, HTTPStatus.BAD_REQUEST)
            for key in self._CASE_FIELDS:
                if key in body:
                    if body[key] is None:
                        raw.pop(key, None)
                    else:
                        raw[key] = body[key]
            now = datetime.now().isoformat(timespec="seconds")
            conn = _case_conn()
            try:
                conn.execute(
                    "UPDATE case_defs SET structured=?, content=?, title=?, module=?, priority=?, updated_at=?"
                    " WHERE case_id=? AND case_type='text'",
                    (json.dumps(raw, ensure_ascii=False), _build_workbench_content(raw),
                     _as_text(raw.get("title")), _module_slug(str(raw.get("moduleL1") or "")),
                     _as_text(raw.get("priority")), now, cid))
                conn.commit()
            finally:
                conn.close()
        _audit_append({"actor": _as_text(body.get("submitter")) or "workstation",
                       "action": "update-case", "caseId": cid,
                       "detail": "编辑正文：" + ",".join(sorted(k for k in body if k in self._CASE_FIELDS))},
                      current_version())
        return self._json({"ok": True, "caseId": cid})

    def do_DELETE(self):
        path = unquote(self.path.split("?")[0])
        if path.startswith("/api/review/cases/"):
            return self._delete_case(path.rsplit("/", 1)[1])
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _delete_case(self, cid: str):
        """删用例：清 case_defs 行 + 清两侧引用（套件行、其他用例的 linked_case_id）。
        执行历史（runs/cases）按编号留档不删。"""
        if current_version() != CASE_PRIMARY_VER:
            return self._json({"error": "当前版本非共库正式版"}, HTTPStatus.CONFLICT)
        with _REVIEW_LOCK:
            conn = _case_conn()
            try:
                row = conn.execute("SELECT title FROM case_defs WHERE case_id=? AND case_type='text'",
                                   (cid,)).fetchone()
                if row is None:
                    return self._json({"error": "caseId 不存在"}, HTTPStatus.NOT_FOUND)
                conn.execute("DELETE FROM case_defs WHERE case_id=?", (cid,))
                conn.execute("DELETE FROM suite_cases WHERE case_id=?", (cid,))
                conn.execute("UPDATE case_defs SET linked_case_id='' WHERE linked_case_id=?", (cid,))
                conn.commit()
            finally:
                conn.close()
        _audit_append({"actor": "workstation", "action": "delete-case", "caseId": cid,
                       "detail": f"删除用例：{row['title']}"}, current_version())
        return self._json({"ok": True, "caseId": cid})


class DualStackServer(ThreadingHTTPServer):
    """IPv4+IPv6 双栈：绑 :: 并关 V6ONLY，127.0.0.1 / [::1] / 局域网地址皆可访问。"""

    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()


def main() -> int:
    parser = argparse.ArgumentParser(description="swan-map-web 站点服务器")
    # 审查修正：默认仅本机（内部 QA 数据无鉴权）；局域网公开需显式 --host 0.0.0.0
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    if not DIST.exists():
        print(f"[FAIL] 前端未构建: {DIST}（先在 frontend/ 下 npm run build）")
        return 1
    if args.host in ("0.0.0.0", "::", "dual"):
        server = DualStackServer(("::", args.port), Handler)
        bind_note = "::（双栈 v4+v6）"
    else:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        bind_note = args.host
    print(f"Swan 功能地图工作站: http://{args.host}:{args.port}  "
          f"(数据: data/versions/{current_version()}，绑定: {bind_note})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
