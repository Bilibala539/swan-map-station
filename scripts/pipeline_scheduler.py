"""流水线定时调度器:常驻轮询平台的流水线配置,到点触发执行。

职责:
  1. 每 30 秒(可 --loop 调)拉一次 /api/pipeline-configs
  2. enabled=1 且 next_run_at <= now 的配置,POST /api/pipeline-configs/{id}/run
     (平台侧照 trigger 通道建 schedule 来源任务,执行 agent 认领后跑)
  3. 幂等:同一 config 同一分钟只触发一次(内存记录 last fired 分钟;
     进程重启后状态清零,极端情况下同分钟可能重复触发一次,可接受)
  4. 单次异常捕获继续循环,一次失败不退出

pythonw 下 sys.stdout 可能为 None,日志一律落
E:/uiautotest/logs/pipeline_scheduler.log(append),不依赖控制台。

用法:
  python scripts/pipeline_scheduler.py           # 常驻(部署机计划任务拉起)
  python scripts/pipeline_scheduler.py --once    # 扫一轮即退出(调试用)
  python scripts/pipeline_scheduler.py --loop 10 # 自定义轮询间隔

部署(主会话执行,本脚本不建计划任务):以 systemd uiautotest-scheduler.service 常驻于 .167(2026-09-22 起)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")
LOG_PATH = PROJECT_ROOT / "logs" / "pipeline_scheduler.log"
LOOP_SECONDS = 30


def log(message: str) -> None:
    """写日志文件(pythonw 下 stdout 是 None,print 前必须判空)。"""
    line = f"[pipeline-scheduler {datetime.now().isoformat(timespec='seconds')}] {message}"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        pass  # 日志目录不可写不能挡住调度本身
    if sys.stdout:  # pythonw 常驻时为 None
        print(line, flush=True)


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{PLATFORM_URL}{path}", timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{PLATFORM_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def tick(state: dict[str, str], now: datetime | None = None) -> int:
    """扫一轮到期配置并触发;state 记 config_id -> 已触发的分钟(幂等)。

    next_run_at 由平台实时计算(>= 当前分钟截断的最近命中),所以
    next_run_at <= now 只会在命中那一分钟内成立,不会补跑历史欠账。
    返回本次成功触发的配置数。
    """
    now = now or datetime.now()
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    data = _get("/api/pipeline-configs")
    fired = 0
    for config in data.get("items") or []:
        config_id = str(config.get("config_id") or "")
        if not config_id or not config.get("enabled"):
            continue
        next_run_at = str(config.get("next_run_at") or "")
        if not next_run_at:
            continue  # 非法参数/once 已过期 → 无下次执行时间
        try:
            due_at = datetime.fromisoformat(next_run_at)
        except ValueError:
            continue
        if due_at > now:
            continue
        if state.get(config_id) == minute_key:
            continue  # 同一分钟已触发过
        try:
            # source=schedule 让平台把 NOTES 记为 daily:<config_id>(定时自动构建),
            # 与页面"立即执行"的 pipeline:<config_id> 区分开(2026-09-29)
            result = _post(f"/api/pipeline-configs/{config_id}/run", {"source": "schedule"})
            state[config_id] = minute_key  # 触发成功才记,失败下轮重试
            job_id = (result.get("job") or {}).get("job_id", "?")
            log(f"fired config={config_id} name={config.get('name', '')} job={job_id}")
            fired += 1
        except Exception as error:  # noqa: BLE001 — 单个配置失败不影响其他
            log(f"fired FAILED config={config_id}: {error}")
    return fired


def main() -> int:
    parser = argparse.ArgumentParser(description="Swan 平台流水线定时调度器")
    parser.add_argument("--once", action="store_true", help="扫一轮即退出(调试用)")
    parser.add_argument("--loop", type=int, default=LOOP_SECONDS, help="轮询间隔秒数(默认 30)")
    args = parser.parse_args()
    log(f"启动: platform={PLATFORM_URL} mode={'once' if args.once else 'loop'} "
        f"interval={args.loop}s")
    state: dict[str, str] = {}
    while True:
        try:
            tick(state)
        except Exception as error:  # noqa: BLE001 — 单轮失败不退出,等下一轮
            log(f"tick error: {error}")
        if args.once:
            break
        time.sleep(args.loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
