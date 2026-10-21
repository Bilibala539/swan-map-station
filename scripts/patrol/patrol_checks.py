#!/usr/bin/env python3
"""平台巡检检查脚本(巡检机制 v1):只读采集 .167 平台侧状态,输出 JSON 供巡检 agent 判读。

在 .167 平台机以 jks 用户运行(计划任务经 run_patrol.sh 拉起无头 claude 后调用):
  python3 patrol_checks.py                                    # 全量检查
  python3 patrol_checks.py --base-url http://127.0.0.1:8080   # 本地自测指临时端口

设计约定:
  - 只读:不调任何写接口、不直接写数据库;状态变更一律由 l1_actions.py 在 L1 白名单内执行
  - 每个子检查独立 try/except,单项失败写进 JSON 对应节的 error 字段,不拖垮整体
  - 时间口径:平台所有时间戳为 UTC ISO(见 dashboard/server.py utc_now()),年龄统一按 UTC 计算
  - 阈值集中在 THRESHOLDS,与 docs/agent模块档案/巡检机制.md §3 一致,改动须同步档案
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---- 阈值(改这里须同步巡检机制.md §3;L1 阈值改动属白名单变更,须先报主会话)----
THRESHOLDS = {
    "pending_l1_hours": 168,   # pending 超 7 天 → L1 白名单取消
    "pending_l2_hours": 1,     # pending 最老超 1 小时 → L2(派发链路可能卡住)
    "device_l1_hours": 168,    # 心跳停超 7 天 → L1 白名单下线标记
    "device_l2_hours": 24,     # 心跳停超 1 天 → L2(执行机可能长期关机)
    "disk_warn_pct": 80,       # ≥80% L3 观察
    "disk_crit_pct": 90,       # ≥90% L2
    "apk_grace_min": 60,       # 登记后 N 分钟内归档未到不算缺失(上传中/宽限)
    "jenkins_consec_fail": 2,  # 连败 ≥2 次 → L2
}

# L1 白名单 systemd 单元(与 l1_actions.py ALLOWED_UNITS 严格一致;
# 2026-10-09 主会话裁定:otp-gateway/dispatch/map-station 并入,jenkins.service 维持只告警)
L1_UNITS = {
    "uiautotest-dashboard.service": "restart",
    "uiautotest-scheduler.service": "restart",
    "nightly-pipeline-trigger.timer": "start",
    "swan-otp-gateway.service": "restart",
    "uiautotest-dispatch.service": "restart",
    "swan-map-station.service": "restart",
}
# 只查不修的平台相关单元(L2 告警,重启权在对应模块/主会话)
CHECK_ONLY_UNITS = [
    "jenkins.service",
]

API_PROBES = ["/api/health", "/api/pipeline-configs", "/api/devices", "/api/apks?limit=1"]
JENKINS_JOBS = ["swan-build-android", "swan-autotest"]
JENKINS_NODE = "build-176"  # .176 remoting agent 节点名(经 master API 查,免登 .176)
DEFAULT_APKS_DIR = "/opt/uiautotest/output/apks"  # 平台归档副本目录(下载回源契约)


def parse_utc(value: str) -> datetime | None:
    """解析平台 UTC ISO 时间戳(容忍 Z 结尾/无时区/毫秒位差异);失败返回 None。"""
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


def age_hours(value: str, now: datetime) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def http_json(url: str, timeout: float = 8.0, auth: tuple[str, str] | None = None):
    """GET 返回 (status, body dict/list);非 2xx 抛 RuntimeError,body 尽量带错误文本。"""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"non-JSON body: {raw[:120]}") from error


def items_of(body) -> list:
    """平台列表接口统一 {items:[...]},防御性兼容裸数组。"""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "jobs", "devices", "apks"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def section(name: str, state: dict, fn) -> None:
    """子检查包装:异常转 error 字段,不中断整体。"""
    try:
        state[name] = fn()
    except Exception as error:  # noqa: BLE001 - 巡检必须整体存活
        state[name] = {"error": f"{type(error).__name__}: {error}"}


# ---------------------------------------------------------------- 各子检查

def check_services() -> list:
    """systemd 单元状态(is-active + is-enabled);timer 服务体 dead 属正常,查的是 timer。"""
    rows = []
    for unit in list(L1_UNITS) + CHECK_ONLY_UNITS:
        active = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        enabled = subprocess.run(
            ["systemctl", "is-enabled", unit], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        rows.append(
            {
                "unit": unit,
                "active": active == "active",
                "active_state": active,
                "enabled": enabled,
                "l1_whitelisted": unit in L1_UNITS,
                "l1_verb": L1_UNITS.get(unit),
            }
        )
    return rows


def check_apis(base_url: str, timeout: float) -> list:
    rows = []
    for path in API_PROBES:
        url = base_url.rstrip("/") + path
        started = datetime.now(timezone.utc)
        try:
            status, _ = http_json(url, timeout=timeout)
            rows.append({"path": path, "ok": 200 <= status < 300, "code": status,
                         "ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)})
        except Exception as error:  # noqa: BLE001
            rows.append({"path": path, "ok": False, "code": None,
                         "error": f"{type(error).__name__}: {error}"})
    return rows


def check_pending(base_url: str, timeout: float, now: datetime) -> dict:
    _, body = http_json(base_url.rstrip("/") + "/api/pipeline/jobs?status=pending&limit=200", timeout)
    rows = items_of(body)
    aged = []
    for row in rows:
        hours = age_hours(row.get("created_at"), now)
        item = {
            "job_id": row.get("job_id"),
            "age_hours": round(hours, 2) if hours is not None else None,
            "trigger_source": row.get("trigger_source"),
            "jenkins_job": row.get("jenkins_job") or "",
            "created_at": row.get("created_at"),
        }
        if hours is not None and hours >= THRESHOLDS["pending_l1_hours"]:
            aged.append(item)
    oldest = max((item["age_hours"] for item in aged), default=None)
    if oldest is None:
        all_ages = [age_hours(row.get("created_at"), now) for row in rows]
        all_ages = [value for value in all_ages if value is not None]
        oldest = round(max(all_ages), 2) if all_ages else 0.0
    return {
        "pending_count": len(rows),
        "oldest_hours": oldest,
        "l1_candidates": aged,  # >7d,由 l1_actions.py pending-expire 处置
    }


def check_devices(base_url: str, timeout: float, now: datetime) -> dict:
    _, body = http_json(base_url.rstrip("/") + "/api/devices", timeout)
    rows = items_of(body)
    stale = []
    for row in rows:
        hours = age_hours(row.get("last_heartbeat"), now)
        status = str(row.get("status") or "")
        item = {
            "device_id": row.get("device_id"),
            "status": status,
            "heartbeat_age_hours": round(hours, 2) if hours is not None else None,
        }
        # L1 只标记"还没下线"的僵尸设备;已 offline/disabled 的重复标记无意义
        if (hours is not None and hours >= THRESHOLDS["device_l1_hours"]
                and status not in ("offline", "disabled")):
            stale.append(item)
    return {"device_count": len(rows), "l1_candidates": stale}


def check_disk() -> list:
    rows = []
    for mount in ("/", "/opt"):
        usage = shutil.disk_usage(mount)
        pct = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
        rows.append({"mount": mount, "pct": pct,
                     "free_gb": round(usage.free / 1024**3, 1)})
    return rows


def check_apks(base_url: str, timeout: float, now: datetime, apks_dir: Path) -> dict:
    _, body = http_json(base_url.rstrip("/") + "/api/apks?limit=200", timeout)
    rows = items_of(body)
    missing = []
    for row in rows:
        apk_id = str(row.get("apk_id") or "")
        if not apk_id:
            continue
        file_path = str(row.get("file_path") or "").strip()
        archived = (apks_dir / f"{apk_id}.apk").is_file()
        # file_path 是登记时构建机本地路径,跨机场景在 .167 不可达,归档副本才是平台契约
        on_disk = archived or (bool(file_path) and Path(file_path).is_file())
        if on_disk:
            continue
        hours = age_hours(row.get("registered_at"), now)
        age_min = None if hours is None else round(hours * 60, 1)
        # 宽限期内视为"归档在途"(上传中),不报缺失
        if age_min is not None and age_min > THRESHOLDS["apk_grace_min"]:
            missing.append({"apk_id": apk_id, "registered_at": row.get("registered_at"),
                            "age_min": age_min, "file_path": file_path})
    return {"rows": len(rows), "archive_dir": str(apks_dir), "missing_archive": missing}


def _jenkins_auth(args) -> tuple[str, str] | None:
    user = args.jenkins_user or os.environ.get("JENKINS_USER", "")
    token = args.jenkins_token or os.environ.get("JENKINS_API_TOKEN", "")
    if user and token:
        return (user, token)
    return None


def check_jenkins(args, timeout: float) -> dict:
    auth = _jenkins_auth(args)
    if auth is None:
        return {"skipped": "no JENKINS_USER/JENKINS_API_TOKEN credentials"}
    base = args.jenkins_base_url.rstrip("/")
    jobs = []
    for job in JENKINS_JOBS:
        _, body = http_json(
            f"{base}/job/{job}/api/json?tree=builds[number,result]{{0,8}}", timeout, auth
        )
        builds = body.get("builds") or []
        consecutive = 0
        for build in builds:  # Jenkins builds[0] 为最新
            result = build.get("result")
            if result is None:       # 构建中不计
                break
            if result == "FAILURE":
                consecutive += 1
            else:
                break
        last_number = builds[0].get("number") if builds else None
        last_result = builds[0].get("result") if builds else None
        jobs.append({"job": job, "checked_builds": len(builds), "last_number": last_number,
                     "last_result": last_result, "consecutive_failures": consecutive})
    _, computers = http_json(
        f"{base}/computer/api/json?tree=computer[displayName,offline,offlineCauseReason]", timeout, auth
    )
    node = next((c for c in computers.get("computer", [])
                 if c.get("displayName") == JENKINS_NODE), None)
    return {
        "jobs": jobs,
        "node": {"name": JENKINS_NODE,
                 "online": bool(node) and not node.get("offline", True),
                 "offline_cause": (node or {}).get("offlineCauseReason") or ""},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="平台巡检查看(只读,输出 JSON)")
    parser.add_argument("--base-url", default=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--jenkins-base-url", default=os.environ.get("JENKINS_BASE_URL", "http://127.0.0.1:8082"))
    parser.add_argument("--jenkins-user", default="")
    parser.add_argument("--jenkins-token", default="")
    parser.add_argument("--apks-dir", default=DEFAULT_APKS_DIR)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    state: dict = {
        "ts": now.isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "thresholds": THRESHOLDS,
    }
    section("services", state, check_services)
    section("apis", state, lambda: check_apis(args.base_url, args.timeout))
    section("pipeline_jobs", state, lambda: check_pending(args.base_url, args.timeout, now))
    section("devices", state, lambda: check_devices(args.base_url, args.timeout, now))
    section("disk", state, check_disk)
    section("apks", state, lambda: check_apks(args.base_url, args.timeout, now, Path(args.apks_dir)))
    section("jenkins", state, lambda: check_jenkins(args, args.timeout))

    # 登录闸适配(2026-10-09 平台上浏览器面登录闸,机器通道逐条登记 MACHINE_EXEMPT):
    # 巡检三个列表读(GET devices/apks/pipeline jobs)未登记时返回 401,记 skipped 不算
    # 异常发现,登记后自动恢复;L1 两个 POST 走既有前缀豁免不受影响。
    def _mark_auth_skipped(value):
        if isinstance(value, dict) and "HTTP 401" in str(value.get("error", "")):
            return {"skipped": "平台登录闸 401(机器通道待登记 MACHINE_EXEMPT)"}
        if isinstance(value, list):
            return [_mark_auth_skipped(item) for item in value]
        return value

    for key in ("apis", "pipeline_jobs", "devices", "apks"):
        state[key] = _mark_auth_skipped(state[key])
    print(json.dumps(state, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
