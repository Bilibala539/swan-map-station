#!/usr/bin/env python3
"""L1 白名单执行器(巡检自愈):用户已批准,超出即拒绝退出(exit 2)。

白名单全文(与 docs/agent模块档案/巡检机制.md §4 一致;增删条目属白名单变更,须先报主会话批准):
  pending-expire    pipeline_jobs pending 超 7 天          → POST /api/pipeline/jobs/<id>/cancel
  device-heartbeat  devices 心跳停超 7 天 且 未下线未禁用   → POST /api/devices/<id>/status {"status":"offline"}
  svc-restart       白名单 systemd 单元非 active            → sudo systemctl restart|start
                    (dashboard/scheduler/otp-gateway/dispatch/map-station=restart,nightly timer=start;
                     jenkins.service 不在白名单,L2 只告警)

安全设计:
  - 每动作执行前复核条件(状态已变则记 skipped,不动手),动作后复验结果
  - 每条真实执行写审计 JSONL(时间/对象/动作/依据/结果/证据),dry-run 不写审计
  - systemd 白名单以 ALLOWED_UNITS 为准,单元名不在表内一律拒绝;systemctl 前缀可用
    环境变量 PATROL_SYSTEMCTL 覆盖(本地自测注入桩,生产缺省 "sudo systemctl")

用法:
  python3 l1_actions.py pending-expire [--base-url URL] [--dry-run] [--audit PATH]
  python3 l1_actions.py device-heartbeat [--base-url URL] [--dry-run] [--audit PATH]
  python3 l1_actions.py svc-restart --unit uiautotest-scheduler.service [--dry-run] [--audit PATH]
  python3 l1_actions.py audit-list [--limit 20]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

THRESH = {"pending_l1_hours": 168.0, "device_l1_hours": 168.0}  # 与 patrol_checks.py 同步

ALLOWED_UNITS = {  # 单元名 → 动作词(仅 restart/start);范围以用户批准白名单为准
    "uiautotest-dashboard.service": "restart",
    "uiautotest-scheduler.service": "restart",
    "nightly-pipeline-trigger.timer": "start",
    # 2026-10-09 主会话裁定并入:同为应常驻平台服务,停→重启幂等
    "swan-otp-gateway.service": "restart",
    "uiautotest-dispatch.service": "restart",
    "swan-map-station.service": "restart",
}
DEFAULT_AUDIT = "/opt/uiautotest/logs/patrol_audit.jsonl"
SYSTEMCTL_PREFIX = os.environ.get("PATROL_SYSTEMCTL", "sudo systemctl").split()


def parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def http_request(url: str, method: str = "GET", payload: dict | None = None,
                 timeout: float = 8.0):
    """返回 (status, body dict);非 2xx 抛 RuntimeError(带状态码与截断响应)。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def items_of(body) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "jobs", "devices"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def write_audit(audit_path: Path, rule: str, obj: str, action: str, basis: str,
                result: str, evidence: dict) -> None:
    """审计一行:L1 每条真实执行必须落(时间/对象/动作/依据/结果)。"""
    line = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": rule, "object": obj, "action": action,
        "basis": basis, "result": result, "evidence": evidence,
    }, ensure_ascii=False)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def action_pending_expire(args, audit_path: Path, now: datetime) -> dict:
    base = args.base_url.rstrip("/")
    _, body = http_request(f"{base}/api/pipeline/jobs?status=pending&limit=200")
    rows = items_of(body)
    acted, skipped, failed = [], [], []
    for row in rows:
        created = parse_utc(row.get("created_at"))
        hours = (now - created).total_seconds() / 3600 if created else None
        if hours is None or hours < THRESH["pending_l1_hours"]:
            continue
        job_id = str(row.get("job_id"))
        basis = f"pending {hours/24:.1f}d > {THRESH['pending_l1_hours']/24:.0f}d"
        if args.dry_run:
            acted.append({"job_id": job_id, "basis": basis, "dry_run": True})
            continue
        try:
            # 复核:详情仍为 pending 才动手(状态已变属竞态,记 skipped)
            _, detail = http_request(f"{base}/api/pipeline/jobs/{job_id}")
            if str(detail.get("status")) != "pending":
                skipped.append({"job_id": job_id, "reason": f"status={detail.get('status')}"})
                continue
            http_request(f"{base}/api/pipeline/jobs/{job_id}/cancel", method="POST", payload={})
            write_audit(audit_path, "L1-pending-expire", job_id, "cancel", basis, "ok",
                        {"created_at": row.get("created_at"), "age_hours": round(hours, 1)})
            acted.append({"job_id": job_id, "basis": basis})
        except Exception as error:  # noqa: BLE001
            failed.append({"job_id": job_id, "error": str(error)})
            write_audit(audit_path, "L1-pending-expire", job_id, "cancel", basis,
                        f"failed: {error}", {})
    return {"rule": "L1-pending-expire", "acted": acted, "skipped": skipped, "failed": failed}


def action_device_heartbeat(args, audit_path: Path, now: datetime) -> dict:
    base = args.base_url.rstrip("/")
    _, body = http_request(f"{base}/api/devices")
    rows = items_of(body)
    acted, skipped, failed = [], [], []
    for row in rows:
        beat = parse_utc(row.get("last_heartbeat"))
        status = str(row.get("status") or "")
        if beat is None:
            continue
        hours = (now - beat).total_seconds() / 3600
        if hours < THRESH["device_l1_hours"] or status in ("offline", "disabled"):
            continue  # 未达阈值,或已下线/禁用(重复标记无意义)
        device_id = str(row.get("device_id"))
        basis = f"heartbeat stale {hours/24:.1f}d > {THRESH['device_l1_hours']/24:.0f}d, status={status}"
        if args.dry_run:
            acted.append({"device_id": device_id, "basis": basis, "dry_run": True})
            continue
        try:
            # 复核:状态可能刚被 register 复活或人工处理
            _, detail = http_request(f"{base}/api/devices/{device_id}")
            detail_status = str(detail.get("status") or detail.get("device", {}).get("status") or "")
            if detail_status in ("offline", "disabled"):
                skipped.append({"device_id": device_id, "reason": f"now status={detail_status}"})
                continue
            http_request(f"{base}/api/devices/{device_id}/status", method="POST",
                         payload={"status": "offline"})
            write_audit(audit_path, "L1-device-heartbeat", device_id, "set-offline", basis,
                        "ok", {"last_heartbeat": row.get("last_heartbeat"),
                               "age_hours": round(hours, 1)})
            acted.append({"device_id": device_id, "basis": basis})
        except Exception as error:  # noqa: BLE001
            failed.append({"device_id": device_id, "error": str(error)})
            write_audit(audit_path, "L1-device-heartbeat", device_id, "set-offline", basis,
                        f"failed: {error}", {})
    return {"rule": "L1-device-heartbeat", "acted": acted, "skipped": skipped, "failed": failed}


def action_svc_restart(args, audit_path: Path) -> dict:
    unit = args.unit or ""
    if unit not in ALLOWED_UNITS:
        print(json.dumps({"rule": "L1-svc-restart", "refused": unit,
                          "reason": f"unit not in whitelist: {sorted(ALLOWED_UNITS)}"},
                         ensure_ascii=False))
        raise SystemExit(2)
    verb = ALLOWED_UNITS[unit]

    def systemctl(*cmd_args: str) -> str:
        result = subprocess.run(SYSTEMCTL_PREFIX + list(cmd_args), capture_output=True,
                                text=True, timeout=30)
        return (result.stdout or result.stderr or "").strip()

    state_now = systemctl("is-active", unit)
    if state_now == "active":
        return {"rule": "L1-svc-restart", "acted": [], "skipped":
                [{"unit": unit, "reason": "already active"}], "failed": []}
    basis = f"unit {unit} state={state_now} (whitelist verb={verb})"
    if args.dry_run:
        return {"rule": "L1-svc-restart", "acted":
                [{"unit": unit, "verb": verb, "basis": basis, "dry_run": True}],
                "skipped": [], "failed": []}
    subprocess.run(SYSTEMCTL_PREFIX + [verb, unit], capture_output=True, text=True,
                   timeout=60, check=False)
    time.sleep(2)
    state_after = systemctl("is-active", unit)
    ok = state_after == "active"
    write_audit(audit_path, "L1-svc-restart", unit, verb, basis,
                "ok" if ok else f"still {state_after}", {})
    return {"rule": "L1-svc-restart", "acted":
            [{"unit": unit, "verb": verb, "basis": basis, "state_after": state_after}],
            "skipped": [], "failed": [] if ok else [{"unit": unit, "error": f"still {state_after}"}]}


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 白名单执行器(超出白名单一律拒绝)")
    parser.add_argument("rule", choices=["pending-expire", "device-heartbeat",
                                         "svc-restart", "audit-list"])
    parser.add_argument("--base-url", default=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--unit", default="")
    parser.add_argument("--dry-run", action="store_true", help="只列出将执行的动作,不动手不写审计")
    parser.add_argument("--audit", default=DEFAULT_AUDIT, help="审计 JSONL 路径(自测可指临时文件)")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    audit_path = Path(args.audit)

    if args.rule == "audit-list":
        if not audit_path.is_file():
            print(json.dumps({"lines": 0, "items": []}, ensure_ascii=False))
            return 0
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        recent = [json.loads(line) for line in lines[-args.limit:] if line.strip()]
        print(json.dumps({"lines": len(lines), "items": recent}, ensure_ascii=False, indent=1))
        return 0

    if args.rule == "svc-restart":
        summary = action_svc_restart(args, audit_path)
    else:
        now = datetime.now(timezone.utc)
        if args.rule == "pending-expire":
            summary = action_pending_expire(args, audit_path, now)
        else:
            summary = action_device_heartbeat(args, audit_path, now)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
