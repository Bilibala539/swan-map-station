"""Nightly 触发器(M5 二期套件化):nightly 走平台流水线通道的引导与切换工具。

M5 二期前:旧 .166 计划任务 NightlyPipelineTrigger 每日 01:33 调本脚本直接 POST(历史);现 .167 由 systemd nightly-pipeline-trigger.timer 调本脚本 --legacy-trigger
/api/pipeline/trigger(现 --legacy-trigger 模式,固定跑 tests/flutter/test_case_driver.py)。
M5 二期起:nightly 改按"全量回归套件"触发——建套件 + pipeline_config(daily 01:33),
由常驻调度器 scripts/pipeline_scheduler.py 到点触发;本脚本改为幂等引导与切换工具。

子命令(互斥,缺省 bootstrap):
  (无参数)           bootstrap:幂等创建全量回归套件 + NIGHTLY-REGRESSION 配置(enabled=0)
  --run-now         白天验证:按配置立即建一个 schedule 任务跑真链路
  --enable          切换:配置 enabled=1(由主会话同步禁用旧计划任务)
  --disable         回滚:配置 enabled=0
  --legacy-trigger  旧通道直触发(回滚保险,即原计划任务行为)

切换步骤(与主会话约定,不停摆夜间回归):
  ①白天 bootstrap → ②白天 --run-now 验证链路(job 认领/门禁/用例)
  → ③向用户报备套件成员数与实测时长,确认后 --enable
    + 主会话 schtasks /change /disable NightlyPipelineTrigger
  → ④次日 01:33 查 logs/pipeline_scheduler.log 与平台"最近执行"。
  回滚 = --disable + 重启用计划任务,各一条命令。

套件成员口径(已确认"全量"):case_defs 中 case_type=auto 且 file_path 以
knowledge/swan/cases/driver/ 开头的用例(YAML 驱动自动用例);套件已存在时
不增删成员(内容调整走套件页,无代码依赖)。执行语义:套件聚合无 .py 成员
→ 执行 agent 回落默认 target=tests/flutter/test_case_driver.py,跑全部
driver YAML,与旧 nightly 覆盖一致。

部署:bootstrap/enable 由主会话在 .167 执行一次;本脚本不建计划任务。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

PLATFORM_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")

# 全量回归套件与 nightly 配置(固定 ID,幂等引导的 find-or-create 锚点)
SUITE_ID = "SUITE-NIGHTLY-REGRESSION"
SUITE_NAME = "Nightly 全量回归"
CONFIG_ID = "NIGHTLY-REGRESSION"
NIGHTLY_TIME = "01:33"  # 与原计划任务一致(原 .166 计划任务口径,.167 CST 同时刻)
CASE_PREFIX = "knowledge/swan/cases/driver/"  # YAML 驱动自动用例(sync_cases 的 auto 口径;执行时聚合无 .py 成员回落默认 target=driver,等效旧 nightly)

# 旧通道参数(回滚保险,保持原计划任务行为不变)
LEGACY_PAYLOAD = {
    "trigger_source": "nightly",
    "platform": "flutter",
    "case_filter": "tests/flutter/test_case_driver.py",
}


def _request(method: str, path: str, payload: dict | None) -> dict:
    """调平台 API;非 2xx 抛 RuntimeError(带服务端 error 明细)。"""
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"} if data is not None else {}
    request = urllib.request.Request(f"{PLATFORM_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"{method} {path} -> HTTP {error.code}: {detail}") from error


def list_nightly_case_ids() -> list[str]:
    """全量口径取用例:case_type=auto 且 file_path 在 tests/flutter/ 下;保序去重。"""
    # 服务端 limit 上限 1000(list_case_defs 钳制);触顶说明可能截断,提醒扩容
    data = _request("GET", "/api/case-defs?limit=1000", None)
    items = data.get("items") or []
    if len(items) >= 1000:
        print("[WARN] case_defs 返回已达 limit=1000 上限,套件成员可能不完整;"
              "请扩 list_case_defs 上限后重建套件")
    seen: set[str] = set()
    case_ids: list[str] = []
    for item in items:
        case_id = str(item.get("case_id") or "").strip()
        file_path = str(item.get("file_path") or "").strip()
        if not case_id or case_id in seen:
            continue
        if str(item.get("case_type") or "") != "auto":
            continue
        if not file_path.replace("\\", "/").startswith(CASE_PREFIX):
            continue
        seen.add(case_id)
        case_ids.append(case_id)
    return case_ids


def ensure_suite() -> tuple[dict, bool]:
    """find-or-create 全量回归套件;新建时按全量口径灌成员,已存在不动成员。"""
    try:
        suite = _request("GET", f"/api/suites/{SUITE_ID}", None)
        print(f"套件已存在: {SUITE_ID}(成员 {suite.get('case_count', '?')} 条,不增删;调整请走套件页)")
        return suite, False
    except RuntimeError:
        pass  # 404 → 继续创建
    member_ids = list_nightly_case_ids()
    if not member_ids:
        raise RuntimeError(
            f"case_defs 中没有 {CASE_PREFIX} 下的 auto 用例(先跑 scripts/sync_cases.py 同步用例)"
        )
    suite = _request("POST", "/api/suites", {
        "suite_id": SUITE_ID,
        "name": SUITE_NAME,
        "description": "M5 二期:夜间回归套件,成员=case_defs 中 tests/flutter/ 下全部 auto 用例",
        "suite_type": "regression",
    })
    _request("POST", f"/api/suites/{SUITE_ID}/cases", {"case_ids": member_ids})
    print(f"套件已创建: {SUITE_ID},灌入成员 {len(member_ids)} 条")
    return suite, True


def ensure_config(suite_id: str) -> tuple[dict, bool]:
    """find-or-create nightly 流水线配置;已存在则原样返回(不覆盖人工修改)。"""
    try:
        config = _request("GET", f"/api/pipeline-configs/{CONFIG_ID}", None)
        print(
            f"配置已存在: {CONFIG_ID}(enabled={config.get('enabled')}, "
            f"suite_id={config.get('suite_id') or '-'}, 不覆盖)"
        )
        return config, False
    except RuntimeError:
        pass  # 404 → 继续创建
    config = _request("POST", "/api/pipeline-configs", {
        "config_id": CONFIG_ID,
        "name": SUITE_NAME,
        "description": "M5 二期:夜间回归改走流水线通道,每日 01:33 按全量回归套件执行",
        "suite_id": suite_id,
        "include_build": 0,
        "schedule_type": "daily",
        "schedule_param": json.dumps({"time": NIGHTLY_TIME}, separators=(",", ":")),
        "enabled": 0,  # 三道闸第一道:先禁用,验证链路后再 --enable
        "platform": "flutter",
    })
    print(f"配置已创建: {CONFIG_ID}(enabled=0,daily {NIGHTLY_TIME},platform=flutter)")
    return config, True


def bootstrap() -> int:
    suite, _ = ensure_suite()
    config, _ = ensure_config(str(suite.get("suite_id") or SUITE_ID))
    next_run = str(config.get("next_run_at") or "(enabled=0 也会计算,待 --enable 后生效)")
    print(f"next_run_at 预览: {next_run}")
    print("下一步: --run-now 白天验证链路 → 向用户报备成员数与实测时长 → --enable + 禁旧计划任务")
    return 0


def set_enabled(on: bool) -> int:
    config = _request("PUT", f"/api/pipeline-configs/{CONFIG_ID}", {"enabled": 1 if on else 0})
    print(
        f"配置 {CONFIG_ID} enabled={config.get('enabled')},"
        f"next_run_at={config.get('next_run_at') or '-'}"
    )
    if on:
        print("提醒: 请同步禁用旧计划任务(schtasks /change /disable NightlyPipelineTrigger,主会话执行)")
    return 0


def run_now() -> int:
    result = _request("POST", f"/api/pipeline-configs/{CONFIG_ID}/run", {})
    job = result.get("job") or {}
    print(f"已触发任务: {job.get('job_id', '?')}(trigger_source={job.get('trigger_source', '?')})")
    return 0


def legacy_trigger() -> int:
    """旧通道直触发,保持原计划任务行为(回滚保险)。"""
    result = _request("POST", "/api/pipeline/trigger", LEGACY_PAYLOAD)
    print(f"nightly job triggered: {result.get('job_id', '?')}")
    return 0


def main() -> int:
    # (历史坑:.166 Windows 控制台非 UTF-8;.167 Linux 无此虑,保留无害)统一输出编码防中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Nightly 全量回归套件化:引导与切换工具")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-now", action="store_true", help="按配置立即建一个 schedule 任务")
    group.add_argument("--enable", action="store_true", help="配置 enabled=1(配合禁用旧计划任务)")
    group.add_argument("--disable", action="store_true", help="配置 enabled=0(回滚)")
    group.add_argument("--legacy-trigger", action="store_true", help="旧通道 /api/pipeline/trigger 直触发")
    args = parser.parse_args()
    try:
        if args.run_now:
            return run_now()
        if args.enable:
            return set_enabled(True)
        if args.disable:
            return set_enabled(False)
        if args.legacy_trigger:
            return legacy_trigger()
        return bootstrap()
    except RuntimeError as error:
        print(f"nightly_trigger 失败: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
