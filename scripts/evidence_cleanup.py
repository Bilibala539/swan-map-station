"""证据保留清理(②证据规范的存储策略)。

规则:
  - 失败会话的证据 **永久保留**(bug 证据)
  - 通过/跳过会话的证据保留 7 天,过期清理
  - 平台查不到的孤儿目录按 30 天宽限清理
  - 空目录随时清理

证据目录: output/evidence/<run_tag>/(run_tag = 会话 run_id)
调度: 本机计划任务 EvidenceCleanup 每日执行(见 deploy 注释)
用法: python scripts/evidence_cleanup.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "output" / "evidence"

KEEP_PASSED_DAYS = 7
ORPHAN_DAYS = 30
# 平台地址:本机上报与查询都用它
PLATFORM_URL = ""


def _platform_url() -> str:
    url = PLATFORM_URL
    if not url:
        # scripts/ 下无配置时读环境变量,最后退回 .167 默认
        import os
        url = os.environ.get("DASHBOARD_URL", "http://192.168.12.167:8080")
    return url.rstrip("/")


def _run_status(run_tag: str) -> str | None:
    """查平台该会话的最终状态;查不到返回 None。"""
    url = f"{_platform_url()}/api/runs/{run_tag}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str(data.get("status") or "")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None  # 平台不可达时保守:按孤儿宽限处理,不误删失败证据


def cleanup(dry_run: bool = False) -> list[dict]:
    now = time.time()
    report = []
    if not EVIDENCE_ROOT.exists():
        return report
    for entry in sorted(EVIDENCE_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        age_days = (now - entry.stat().st_mtime) / 86400
        has_fail = any("FAIL_" in f.name for f in entry.rglob("*.png"))
        status = _run_status(entry.name)
        reason = None
        if status in {"failed", "error"} or has_fail:
            pass  # 失败证据永久保留
        elif status in {"passed", "skipped", "running"} and age_days > KEEP_PASSED_DAYS:
            reason = f"通过会话证据过期({age_days:.1f}天 > {KEEP_PASSED_DAYS}天)"
        elif status is None and age_days > ORPHAN_DAYS:
            reason = f"孤儿目录宽限过期({age_days:.1f}天 > {ORPHAN_DAYS}天)"
        if reason:
            report.append({"dir": entry.name, "action": "delete", "reason": reason})
            if not dry_run:
                shutil.rmtree(entry, ignore_errors=True)
        else:
            report.append({"dir": entry.name, "action": "keep",
                           "reason": f"status={status or 'unknown'}, age={age_days:.1f}d"})
        # 空目录清理
        if not any(entry.iterdir()):
            report.append({"dir": entry.name, "action": "delete", "reason": "空目录"})
            if not dry_run:
                entry.rmdir()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="证据保留清理")
    parser.add_argument("--dry-run", action="store_true", help="只打印不删除")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = cleanup(dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"[evidence-cleanup {mode} {datetime.now(timezone.utc).isoformat(timespec='seconds')}] {len(report)} 个目录:")
    for item in report:
        print(f"  {item['action']:>6} {item['dir']}: {item['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
