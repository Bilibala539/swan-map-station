from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .client import DashboardClient

_active_reporter: "DashboardReporter | None" = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DashboardReporter:
    def __init__(self) -> None:
        self.client = DashboardClient()
        self.run_id = os.environ.get("DASHBOARD_RUN_ID") or f"pytest-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.started_at = _now()
        self.case_results: dict[str, dict[str, Any]] = {}
        self.case_started: dict[str, float] = {}
        self.enabled = self.client.enabled

    def start(self) -> None:
        if not self.enabled:
            return
        platform = os.environ.get("PLATFORM", "android").lower()
        # web 平台同样上报(流水线链路冒烟等无设备用例也要有执行会话记录)
        if platform not in {"android", "ios", "flutter", "pc", "web"}:
            self.enabled = False
            return
        self.client.post(
            "/api/runs",
            {
                "run_id": self.run_id,
                "platform": platform,
                "app_version": os.environ.get("APP_VERSION", ""),
                "apk_id": os.environ.get("SWAN_APK_ID", ""),
                "device": os.environ.get("ANDROID_DEVICE_SERIAL", socket.gethostname()),
                "jenkins_build": os.environ.get("BUILD_TAG", os.environ.get("BUILD_NUMBER", "")),
                "started_at": self.started_at,
                "metadata": {"source": "pytest", "host": socket.gethostname()},
            },
        )

    def report(self, report: Any) -> None:
        if not self.enabled:
            return
        nodeid = str(report.nodeid)
        if report.when == "setup":
            self.case_started[nodeid] = time.monotonic()
            return
        if report.when != "call":
            return
        status = "passed" if report.passed else "skipped" if report.skipped else "failed"
        started = self.case_started.get(nodeid, time.monotonic() - report.duration)
        short_name = nodeid.split("::")[-1]
        log_parts = [part for part in [getattr(report, "capstdout", ""), getattr(report, "capstderr", "")] if part]
        error = getattr(report, "longreprtext", "") if status == "failed" else ""
        result = {
            "case_id": nodeid,
            "name": short_name,
            "platform": os.environ.get("PLATFORM", "android").lower(),
            "status": status,
            "started_at": datetime.fromtimestamp(time.time() - (time.monotonic() - started), timezone.utc).isoformat(timespec="seconds"),
            "ended_at": _now(),
            "duration_seconds": round(float(report.duration), 3),
            "log": "\n".join(log_parts)[-20000:],
            "error": error[-20000:] if error else "",
            "artifacts": {
                "jenkins_build": os.environ.get("BUILD_TAG", ""),
                "apk_id": os.environ.get("SWAN_APK_ID", ""),
                "evidence_dir": os.environ.get("EVIDENCE_DIR", ""),
            },
        }
        self.case_results[nodeid] = result
        self.client.post(f"/api/runs/{self.run_id}/cases", result)

    def finish(self) -> None:
        if not self.enabled:
            return
        statuses = [result["status"] for result in self.case_results.values()]
        status = "failed" if any(value in {"failed", "error"} for value in statuses) else "passed"
        self.client.post(
            f"/api/runs/{self.run_id}/complete",
            {"status": status, "ended_at": _now()},
        )


def pytest_configure(config: Any) -> None:
    global _active_reporter
    reporter = DashboardReporter()
    config._dashboard_reporter = reporter
    _active_reporter = reporter
    reporter.start()


def pytest_runtest_logreport(report: Any) -> None:
    if _active_reporter is not None:
        _active_reporter.report(report)


def pytest_runtest_setup(item: Any) -> None:
    if _active_reporter is not None:
        _active_reporter.case_started[str(item.nodeid)] = time.monotonic()


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if _active_reporter is not None:
        _active_reporter.finish()
