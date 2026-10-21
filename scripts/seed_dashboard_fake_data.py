from __future__ import annotations

import argparse
import json
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone


def post(base_url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def seed_run(base_url: str, platform: str, index: int, passed_count: int, failed_count: int) -> str:
    run_id = f"fake-{platform}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{index}-{uuid.uuid4().hex[:5]}"
    started = datetime.now(timezone.utc) - timedelta(hours=index * 3)
    post(base_url, "/api/runs", {
        "run_id": run_id,
        "platform": platform,
        "app_version": f"2.6.{index}",
        "device": "fake-device-01" if platform == "android" else "iPhone Simulator",
        "jenkins_build": f"example-{index}",
        "started_at": started.isoformat(timespec="seconds"),
        "metadata": {"source": "fake-data", "suite": "P0"},
    })
    total = passed_count + failed_count
    for case_index in range(total):
        passed = case_index < passed_count
        case_started = started + timedelta(seconds=case_index * 95)
        post(base_url, f"/api/runs/{run_id}/cases", {
            "case_id": f"{platform}-fake-{index}-{case_index + 1}",
            "name": f"{'登录主流程' if case_index == 0 else '核心业务检查'} · {platform.upper()} · {case_index + 1}",
            "platform": platform,
            "status": "passed" if passed else "failed",
            "started_at": case_started.isoformat(timespec="seconds"),
            "ended_at": (case_started + timedelta(seconds=90 + case_index)).isoformat(timespec="seconds"),
            "duration_seconds": 90 + case_index,
            "log": f"[fake] {platform} case {case_index + 1}\nOpenCV: {'passed' if passed else 'template mismatch'}\nOCR: {'passed' if passed else 'text not found'}",
            "error": "" if passed else "视觉断言失败：预期文本未识别",
            "artifacts": {"screenshot": f"artifacts/{run_id}/case-{case_index + 1}.png", "ocr": f"artifacts/{run_id}/case-{case_index + 1}.json"},
        })
    post(base_url, f"/api/runs/{run_id}/complete", {"status": "passed" if failed_count == 0 else "failed"})
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed fake dashboard history")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    run_ids = [
        seed_run(args.url, "android", 1, 5, 0),
        seed_run(args.url, "android", 2, 3, 2),
        seed_run(args.url, "ios", 3, 4, 1),
    ]
    print("Created fake runs:")
    print("\n".join(run_ids))


if __name__ == "__main__":
    main()

