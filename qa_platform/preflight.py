"""会话前环境门禁(③ preflight)。

任何一次测试会话开始前执行。环境问题(设备掉线、验证码网关不通、
磁盘不足等)在这里拦截,归类为 ENV_BLOCKED——按用户要求,这类问题
按 bug 处理提单跟踪,不计入业务失败,也不许带病开跑浪费一轮。

用法:
    python -m qa_platform.preflight            # 人类可读输出,阻塞则 exit 2
    python -m qa_platform.preflight --json     # JSON 输出(平台调度侧调用)

检查项:
  1. adb 可用 + 目标设备在线(ANDROID_DEVICE_SERIAL 若设置则必须命中)
  2. otp-gateway 健康(OTP_GATEWAY_URL,默认 http://127.0.0.1:18080(167 本机,2026-09-22 迁移自 .166))
  3. 平台服务可达(DASHBOARD_URL,设置时才检查)
  4. output 所在磁盘剩余 ≥ 2GB
  5. 被测包已安装(SWAN_PACKAGE 设置时才检查)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# 阻塞级:不解决就不许开跑
BLOCKING_CHECKS = {"adb_available", "device_online", "otp_gateway", "disk_space"}

GATEWAY_URL = os.environ.get("OTP_GATEWAY_URL", "http://127.0.0.1:18080").rstrip("/")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").rstrip("/")
TARGET_SERIAL = os.environ.get("ANDROID_DEVICE_SERIAL", "").strip()
SWAN_PACKAGE = os.environ.get("SWAN_PACKAGE", "").strip()
MIN_FREE_GB = 2.0


def _http_ok(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return False, str(error)


def check_adb_available() -> tuple[bool, str]:
    try:
        proc = subprocess.run(["adb", "version"], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return True, proc.stdout.splitlines()[0].strip()
        return False, f"adb version 退出码 {proc.returncode}"
    except FileNotFoundError:
        return False, "adb 不在 PATH 中"
    except subprocess.TimeoutExpired:
        return False, "adb version 超时"


def check_device_online() -> tuple[bool, str]:
    try:
        proc = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15)
        online = []
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) == 2 and parts[1] == "device":
                online.append(parts[0])
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return False, f"adb devices 失败: {error}"
    if not online:
        return False, "无在线设备(adb devices 为空)"
    if TARGET_SERIAL and TARGET_SERIAL not in online:
        return False, f"目标设备 {TARGET_SERIAL} 不在线,当前在线: {', '.join(online)}"
    return True, f"在线: {', '.join(online)}"


def check_otp_gateway() -> tuple[bool, str]:
    ok, detail = _http_ok(f"{GATEWAY_URL}/healthz")  # otp-gateway 的健康端点是 /healthz
    return ok, f"{GATEWAY_URL} → {detail}"


def check_dashboard() -> tuple[bool, str]:
    if not DASHBOARD_URL:
        return True, "未设置 DASHBOARD_URL,跳过"
    ok, detail = _http_ok(f"{DASHBOARD_URL}/api/health")
    return ok, f"{DASHBOARD_URL} → {detail}"


def check_disk_space() -> tuple[bool, str]:
    usage = shutil.disk_usage(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    free_gb = usage.free / 1024 ** 3
    ok = free_gb >= MIN_FREE_GB
    return ok, f"剩余 {free_gb:.1f}GB(要求 ≥ {MIN_FREE_GB:.0f}GB)"


def check_app_installed() -> tuple[bool, str]:
    if not SWAN_PACKAGE:
        return True, "未设置 SWAN_PACKAGE,跳过"
    try:
        proc = subprocess.run(
            ["adb", "shell", "pm", "list", "packages", SWAN_PACKAGE],
            capture_output=True, text=True, timeout=20,
        )
        installed = SWAN_PACKAGE in proc.stdout
        return installed, ("已安装" if installed else f"包 {SWAN_PACKAGE} 未安装(需先装包)")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return False, f"adb 检查失败: {error}"


ALL_CHECKS = [
    ("adb_available", check_adb_available),
    ("device_online", check_device_online),
    ("otp_gateway", check_otp_gateway),
    ("dashboard", check_dashboard),
    ("disk_space", check_disk_space),
    ("app_installed", check_app_installed),
]

# 设备类检查:仅真机/模拟器平台需要;web 平台不碰 adb,跳过以免误阻塞
DEVICE_CHECKS = {"adb_available", "device_online", "app_installed"}


def run_preflight() -> dict:
    # 平台感知:web 用例(如流水线链路冒烟)不需要设备,设备类检查直接放行
    platform = os.environ.get("PLATFORM", "web").strip().lower()
    checks = [
        (name, func) for name, func in ALL_CHECKS
        if platform != "web" or name not in DEVICE_CHECKS
    ]
    results = []
    for name, func in checks:
        try:
            ok, detail = func()
        except Exception as error:  # noqa: BLE001 — 检查自身异常按失败算
            ok, detail = False, f"检查异常: {error}"
        results.append({
            "check": name,
            "ok": ok,
            "blocking": name in BLOCKING_CHECKS,
            "detail": detail,
        })
    blocked = [r for r in results if not r["ok"] and r["blocking"]]
    return {
        "verdict": "OK" if not blocked else "ENV_BLOCKED",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
        "blocked_by": [r["check"] for r in blocked],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="会话前环境门禁")
    parser.add_argument("--json", action="store_true", help="输出 JSON(平台调度侧)")
    args = parser.parse_args()

    # Windows 控制台默认 GBK,emoji/中文统一走 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    verdict = run_preflight()
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
    else:
        icon = "✅" if verdict["verdict"] == "OK" else "🚫"
        print(f"{icon} 环境门禁: {verdict['verdict']}")
        for item in verdict["results"]:
            mark = "✅" if item["ok"] else ("🚫 阻塞" if item["blocking"] else "⚠️ 非阻塞")
            print(f"  {mark} {item['check']}: {item['detail']}")
        if verdict["blocked_by"]:
            print(f"\n环境阻塞,禁止开跑。按平台规则:环境问题按 bug 处理,先提单/修复再跑。")

    return 0 if verdict["verdict"] == "OK" else 2


if __name__ == "__main__":
    sys.exit(main())
