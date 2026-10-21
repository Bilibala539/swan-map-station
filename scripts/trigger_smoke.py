"""打包→冒烟联动(M5 二期):swan-build-android 打包成功后自动触发冒烟测试。

用法(Jenkins swan-build-android job 的 batch 步骤,在 build_swan_apk.py 成功后调用):
  /opt/uiautotest/.venv/bin/python scripts\\trigger_smoke.py --suite SMKE --apk-id <apk_id> [--platform flutter]

流程:
  1. GET  {平台}/api/suites/<suite>/case-paths 聚合套件成员为 case_filter
     (平台已把 YAML 驱动用例映射为 test_case_driver node id,.py 原样保留)
  2. POST {平台}/api/pipeline/trigger(trigger_source=build-downstream)
     平台侧自动走 Jenkins 派发链路(try_jenkins_dispatch → swan-autotest),
     派发失败自动留 pending 由本地 PipelineAgent 轮询兜底
  3. 打印 job_id 与 Jenkins 派发结果

防环说明:本脚本只触发"测试"下游(平台 JENKINS_TEST_JOB 必须只指向测试 job,
默认 swan-autotest,严禁配成打包 job);swan-autotest 的构建步骤只跑
pipeline_agent --job-id,不回调任何触发动作,链路天然无环。

失败保护:打包 job 的 batch 里必须用 exit 0 保护包裹本脚本——
  /opt/uiautotest/.venv/bin/python scripts\\trigger_smoke.py ... || echo [WARN] smoke trigger failed
  冒烟触发失败只打告警,不影响打包 job 的结果标记。

退出码:0 成功;2 参数/套件/成员问题(套件不存在、无可用 .py 成员);
1 网络/平台错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://192.168.12.167:8080").rstrip("/")


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """请求平台 API,返回 (status, body);HTTPError 由调用方处理。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{DASHBOARD_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def aggregate_case_filter(suite: str) -> str:
    """拉套件成员路径并聚合成 pytest 目标串;套件不存在/无 .py 成员时明确报错(exit 2)。"""
    try:
        _, body = _request("GET", f"/api/suites/{suite}/case-paths")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"[trigger-smoke] 错误: 冒烟套件不存在: {suite!r}"
                  f"(平台 {DASHBOARD_URL};请检查 --suite 参数或先在平台建套件)", file=sys.stderr)
            raise SystemExit(2) from None
        raise
    py_targets = list(body.get("paths", []))
    if not py_targets:
        print(f"[trigger-smoke] 错误: 套件 {suite!r} 没有可用脚本成员"
              f"(平台 case-paths 返回为空),无法聚合 pytest 目标", file=sys.stderr)
        raise SystemExit(2)
    return " ".join(py_targets)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="打包成功后触发冒烟测试(M5 打包→冒烟联动)")
    parser.add_argument("--suite", default="SMKE",
                        help="冒烟套件 ID(平台 test_suites 主键,默认 SMKE)")
    parser.add_argument("--platform", default="flutter", help="执行平台(默认 flutter)")
    parser.add_argument("--branch", default="", help="代码分支(透传任务)")
    parser.add_argument("--apk-id", dest="apk_id", default="",
                        help="打包登记的 apk_id(build_swan_apk.py 登记产物回填),透传任务关联执行会话")
    args = parser.parse_args()

    case_filter = aggregate_case_filter(args.suite)
    try:
        status, job = _request("POST", "/api/pipeline/trigger", {
            "trigger_source": "build-downstream",
            "platform": args.platform,
            "branch": args.branch,
            "case_filter": case_filter,
            "apk_id": args.apk_id,
        })
    except urllib.error.HTTPError as error:
        # HTTPError 是 URLError 子类,必须先于 URLError 捕获。平台主动拒绝
        # (409=formal 包不跑 UI 自动化的平台策略,2026-09-29)属预期跳过:
        # 打包 job 照常成功,控制台留一行跳过说明,不染红不告警。
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace").strip()
        except OSError:
            pass
        print(f"[trigger-smoke] 平台返回 HTTP {error.code}: {detail}", file=sys.stderr)
        if error.code == 409:
            print("[trigger-smoke] 跳过冒烟: 平台策略规定 UI 自动化仅用测试服包"
                  "(formal 包不触发下游冒烟),打包结果不受影响")
            return 0
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"[trigger-smoke] 错误: 平台不可达({DASHBOARD_URL}): {error}", file=sys.stderr)
        return 1
    if status not in (200, 201):
        print(f"[trigger-smoke] 错误: 平台返回 HTTP {status}", file=sys.stderr)
        return 1

    job_id = job.get("job_id")
    jenkins_job = job.get("jenkins_job") or ""
    jenkins_build = job.get("jenkins_build")
    result = job.get("result") or {}
    if jenkins_job:
        if jenkins_build:
            print(f"[trigger-smoke] 冒烟任务已派发 Jenkins: job={job_id} "
                  f"jenkins={jenkins_job} build={jenkins_build}")
        else:
            print(f"[trigger-smoke] 冒烟任务已入 Jenkins 队列: job={job_id} "
                  f"jenkins={jenkins_job} queue_id={result.get('jenkins_queue_id')} "
                  f"(build 号待 Jenkins 分配)")
    elif result.get("jenkins_trigger_failed"):
        print(f"[trigger-smoke] 冒烟任务留 pending 本地兜底: job={job_id} "
              f"(Jenkins 派发失败: {result['jenkins_trigger_failed']})")
    else:
        print(f"[trigger-smoke] 冒烟任务留 pending 本地兜底: job={job_id} "
              f"(平台未配置 Jenkins 凭据,由本地 PipelineAgent 轮询执行)")
    print(f"[trigger-smoke] case_filter={case_filter}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
