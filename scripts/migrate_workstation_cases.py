"""用例域整合 P1(2026-10-13):工作站 1060 条文字用例一次性迁入测试平台 case_defs。

权威域切分后的正文迁移(见 docs/用例域整合计划.md):
  - 工作站(:8090)cases.json 是编译产物,迁移后正文唯一权威=测试平台 case_defs
  - case_id 沿用工作站编号(launch-NEG-001 式),不重编号
  - module=moduleL1 slug 化(m01~m18),modules 表登记中文名+排序
  - content=人读渲染(case_api_client._build_workbench_content 现有逻辑)
  - structured=工作站原始报文 JSON 全量(机器可读,P2 工作站接入时还原用)
  - human_confirmed:status=stored→1,其余 0(当前 1060 条全 pending → 全 0)
  - status=merged-into:* 的条目跳过(已并入其他用例,非独立用例)
  - 幂等:逐条 PUT /api/case-defs/{id}(upsert),重跑安全

用法:
  python scripts/migrate_workstation_cases.py --dry-run            # 只统计不写
  python scripts/migrate_workstation_cases.py                      # 本机跑,打 .167
  python scripts/migrate_workstation_cases.py --url http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ_ROOT))

from dashboard.case_api_client import _normalize_case  # noqa: E402  复用现有映射逻辑

_MODULE_L1_RE = re.compile(r"^M(\d+)\s*(.+)$")
_MERGED_RE = re.compile(r"^merged-into:")


def _http_json(url: str, method: str = "GET", payload: dict | None = None,
               retries: int = 3) -> dict:
    """JSON HTTP 小客户端(重试退避,与 sync_cases._post_or_put 同口径)。"""
    last_error: Exception | None = None
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(retries):
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            last_error = error
            time.sleep(0.5 * (attempt + 1))
    raise last_error


def _module_slug(module_l1: str) -> tuple[str, str]:
    """'M01 安装与首启' -> ('m01', '安装与首启');不匹配时归 ('misc', 原串) 并由调用方告警。"""
    text = str(module_l1 or "").strip()
    matched = _MODULE_L1_RE.match(text)
    if not matched:
        return "misc", text
    return f"m{int(matched.group(1)):02d}", matched.group(2).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="工作站文字用例迁移进测试平台 case_defs")
    parser.add_argument("--ws-url", default="http://192.168.12.167:8090",
                        help="工作站地址(API /api/review/cases)")
    parser.add_argument("--url", default="http://192.168.12.167:8080",
                        help="测试平台地址")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ws_base, dash_base = args.ws_url.rstrip("/"), args.url.rstrip("/")

    # 1) 拉工作站全量(含运行时合并的 status/gates)
    print(f"[1/4] 拉取工作站用例 {ws_base}/api/review/cases ...")
    merged = _http_json(f"{ws_base}/api/review/cases")
    raw_cases = merged.get("cases") or []
    print(f"      共 {len(raw_cases)} 条")
    skipped_merged = [c["id"] for c in raw_cases
                      if _MERGED_RE.match(str(c.get("status") or ""))]
    items = [c for c in raw_cases if c["id"] not in set(skipped_merged)]
    print(f"      跳过 merged-into {len(skipped_merged)} 条{skipped_merged[:5]},待迁移 {len(items)} 条")

    # 2) 模块登记(moduleL1 -> m01~m18 slug + 中文名,幂等:缺才建)
    modules_map: dict[str, tuple[str, str]] = {}  # moduleL1 原串 -> (slug, name_cn)
    for raw in items:
        l1 = str(raw.get("moduleL1") or "").strip()
        if l1 and l1 not in modules_map:
            modules_map[l1] = _module_slug(l1)
    existing = {m.get("slug") for m in _http_json(f"{dash_base}/api/modules").get("items") or []}
    to_create = []
    for l1, (slug, name_cn) in sorted(modules_map.items(), key=lambda kv: kv[1][0]):
        if slug not in existing:
            to_create.append({"slug": slug, "name_cn": name_cn, "project": "swan",
                              "sort_order": int(slug[1:]) if slug[1:].isdigit() else 99})
    print(f"[2/4] 模块登记:映射 {len(modules_map)} 个,平台缺 {len(to_create)} 个")
    if args.dry_run:
        for l1, (slug, name_cn) in sorted(modules_map.items(), key=lambda kv: kv[1][0]):
            mark = "" if slug in existing else "  [新建]"
            print(f"      {slug:6s} {name_cn}{mark}")
        to_create = []
    for payload in to_create:
        _http_json(f"{dash_base}/api/modules", "POST", payload)
        print(f"      + 模块 {payload['slug']} {payload['name_cn']}")

    # 3) 逐条规范化 + upsert
    print(f"[3/4] 迁移 {len(items)} 条 -> {dash_base} (dry-run={args.dry_run})")
    created = updated = failed = 0
    samples = []
    for index, raw in enumerate(items):
        try:
            case = _normalize_case(raw)
            slug, _ = _module_slug(str(raw.get("moduleL1") or ""))
            case["module"] = slug
            case["structured"] = json.dumps(raw, ensure_ascii=False)
            if args.dry_run:
                if len(samples) < 3:
                    samples.append(case)
                continue
            result = _http_json(f"{dash_base}/api/case-defs/{case['case_id']}", "PUT", case)
            # PUT 返回无 created/updated 区分,按 dry-run 外首跑口径统一计"成功"
            updated += 1
            if len(samples) < 3:
                samples.append(result)
        except (ValueError, urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            failed += 1
            bad_id = raw.get("id", "?") if isinstance(raw, dict) else "?"
            print(f"      [fail] {bad_id}: {error}")
    for case in samples:
        if args.dry_run:
            print(f"      样例 {case['case_id']}: {case['title'][:30]} | module={case['module']}"
                  f" | confirmed={case['human_confirmed']} | structured={len(case['structured'])}B")
    if not args.dry_run:
        print(f"      成功 {updated}, 失败 {failed}")

    # 4) 校验
    print("[4/4] 校验(dry-run 跳过)" if args.dry_run else "[4/4] 校验")
    if not args.dry_run:
        exported = _http_json(f"{dash_base}/api/case-defs/export")
        rows = exported.get("cases") or []
        from collections import Counter
        mods = Counter(r["module"] for r in rows)
        print(f"      平台 text 用例总数 {len(rows)}(含旧 REG-76)")
        print(f"      模块分布: {dict(sorted(mods.items()))}")
        empty_structured = [r["case_id"] for r in rows if not r.get("structured")]
        print(f"      structured 为空的行 {len(empty_structured)} 条"
              f"(应为旧 REG-76 条数)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
