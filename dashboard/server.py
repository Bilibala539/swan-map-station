from __future__ import annotations

import argparse
import html
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "output" / "dashboard.sqlite3"
# APK 平台归档:跨机登记(构建机≠登记机)时登记行的 file_path 指向构建机本地路径,
# 本机回源不可达——构建机在登记后把产物字节上传到此处,下载/二维码回源归档副本
APK_ARCHIVE_DIR = PROJECT_ROOT / "output" / "apks"


def _load_dotenv_local() -> None:
    """加载项目根 .env.local(部署机专属配置:禅道凭据等,不进 git)。

    已存在的环境变量优先,文件值只作补充。
    """
    env_file = PROJECT_ROOT / ".env.local"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_local()

# jenkins_client 与本文件同目录;直接运行本文件时它不在 sys.path,补上(两种运行方式都通)
_DASHBOARD_DIR = str(Path(__file__).resolve().parent)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)
# 包风格 import(from dashboard.zentao_client import ...)依赖项目根在 sys.path;
# -m 形态 cwd 自带,脚本直跑形态补上,同上两种运行方式都通
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import jenkins_client  # noqa: E402  (依赖 _load_dotenv_local 注入的 JENKINS_* 环境变量)

# 平台取值集合：flutter = Flutter App(跑在 Android 设备上,旧称,存量配置保留兼容;
# 2026-10-15 起新配置用 flutter-android/flutter-ios 细分安卓/iOS 包),pc = 桌面端,web = 浏览器端
VALID_PLATFORMS = {"android", "ios", "flutter", "flutter-android", "flutter-ios", "pc", "web"}
# 安装包服务器类型：formal = 正式服包，test = 测试服包(存量库默认 test)
VALID_SERVER_TYPES = ("formal", "test")
# 安装包 OS 类型：android = 安卓包，ios = iOS 包。登记 payload 缺省时按 apk_id
# 前缀推断(swan-ios- 前缀 = ios)——macOS 构建机(build-238)的登记脚本不在本仓
# 管控，契约必须向后兼容(旧脚本不带 os_type 也能落对类型)
VALID_OS_TYPES = ("android", "ios")
IOS_APK_ID_PREFIX = "swan-ios-"


def infer_apk_os_type(apk_id: str) -> str:
    """按 apk_id 前缀推断安装包 OS 类型(swan-ios- → ios,其余 → android)。"""
    return "ios" if str(apk_id or "").startswith(IOS_APK_ID_PREFIX) else "android"
# GitLab push webhook → 自动打包映射(repo 的 path_with_namespace → 分支 → server_type)。
# 表外组合一律 skip;dev→test(测试服/profile)、master→formal(正式服/release,2026-09-22
# 用户拍板只监听这两分支),GitLab 侧分支过滤仅省流(两分支填不进一个过滤框,留空即可)。
WEBHOOK_BUILD_MAP = {
    "swan-app/swan-audio/hivi-network-flutterapp": {"dev": "test", "master": "formal"},
    # core 仓是 app 的构建期依赖(app pubspec 以 path: ../hivi_network_flutter_core
    # 引用,打包脚本每次 fresh clone):core 合入同样要重出包验证(2026-09-23 加)。
    # 同分支名驱动 app 仓对应分支构建;COMMIT 参数语义是"app 仓要签出的 commit",
    # core 的 sha 不适用——仅 WEBHOOK_APP_REPOS 内的仓传 COMMIT 并做同 commit 去重。
    "swan-app/swan-audio/hivi_network_flutter_core": {"dev": "test", "master": "formal"},
}
# COMMIT 透传/同 commit 去重按 app 仓 commit 口径(apks.commit_hash 记 app commit)
WEBHOOK_APP_REPOS = {"swan-app/swan-audio/hivi-network-flutterapp"}

# ---- 平台登录与 AI 任务看板(2026-09-29 v1.1,设计见 docs/AI任务与平台登录设计.md)----
# 预设账号:users 空表时由 initialize() 播种;后续账号走设置弹窗添加,不做删除只停用
PRESET_USERS = ("yumiao", "fushenghao", "wuhouju", "menglingzhao")
# 姓氏全拼(登录头像卡/边栏头像显示用,设计 v1.2 §14);预设账号后端直供,
# 非预设账号由前端回退规则生成(用户名首段),不入库
PRESET_SURNAMES = {"yumiao": "yu", "fushenghao": "fu", "wuhouju": "wu", "menglingzhao": "meng"}
AUTH_COOKIE = "swan_user"
AUTH_COOKIE_MAX_AGE = 7 * 24 * 3600  # 固定 7 天,到期 401 重进(设计 §2.2)
# 登录闸豁免的公开 auth 端点(presets 供登录页头像卡;me 是探测本身)
AUTH_PUBLIC_PATHS = {
    "/api/auth/presets", "/api/auth/login", "/api/auth/logout", "/api/auth/me",
}
# 机器对机器通道:带不了浏览器 cookie,逐一登记豁免(规范 5.6 的"全站 401"
# 只闸浏览器面)。每条注明调用方;新增机器通道必须先在此登记,不得为省事放开整个方法。
# 内网信任模型与"先不做安全"的拍板一致;逐通道加 token 加固列为后续演进项。
MACHINE_EXEMPT_EXACT = {
    ("GET", "/api/health"),                # 各机探活
    ("POST", "/api/runs"),                 # pytest 插件 / seed_dashboard_fake_data.py 会话上报
    ("POST", "/api/apks"),                 # build_swan_apk.py / register_apk.py 打包登记
    ("POST", "/api/builds"),               # build_swan_apk.py 主动打包触发
    ("POST", "/api/webhooks/gitlab"),      # GitLab webhook(自带 X-GitLab-Token 校验)
    ("POST", "/api/pipeline/trigger"),     # trigger_smoke.py / nightly_trigger.py 套件触发
    ("POST", "/api/pipeline/claim"),       # pipeline_agent.py 任务认领
    ("GET", "/api/pipeline-configs"),      # pipeline_scheduler.py 定时轮询配置
    ("POST", "/api/devices/register"),     # qa_platform/device_pool.py 设备登记/心跳
    ("POST", "/api/devices/acquire"),      # 同上:用例会话领设备
    ("GET", "/api/case-defs"),             # nightly_trigger.py 组每日套件
    ("GET", "/api/case-defs/export"),      # 工作站 serve.py 正文拉取(用例域整合 P2,2026-10-13)
    ("GET", "/api/modules"),               # migrate_workstation_cases.py 模块对账(用例域整合 P1)
    ("POST", "/api/modules"),              # 同上:18 个 moduleL1 slug 模块登记(幂等,缺才建)
    ("POST", "/api/suites"),               # nightly_trigger.py 套件维护
    ("GET", "/api/tasks/queue"),           # dispatch_worker.py 领活(新机器通道)
    ("POST", "/api/dispatch/update"),      # dispatch_worker.py 回写(分析/疑问/合并/终态)
    ("GET", "/api/devices"),                   # patrol_checks.py 巡检设备心跳
    ("GET", "/api/apks"),                      # patrol_checks.py 巡检包登记vs归档一致性
    ("GET", "/api/pipeline/jobs"),             # patrol_checks.py 巡检 pending 年龄
}
MACHINE_EXEMPT_PREFIX = {
    ("POST", "/api/runs/"),                # pytest 插件 cases/complete 上报
    ("GET", "/api/runs/"),                 # evidence_cleanup.py 孤儿证据判定
    ("POST", "/api/pipeline/jobs/"),       # pipeline_agent.py finish;cancel 页面触发但同前缀
    ("GET", "/api/pipeline/jobs/"),        # pipeline_agent.py 轮询任务详情
    ("POST", "/api/pipeline-configs/"),    # pipeline_scheduler.py 到点 run
    ("POST", "/api/devices/"),             # device_pool.py release/status
    ("PUT", "/api/case-defs/"),            # sync_cases.py 用例同步(浏览器编辑同走 PUT,一并放开)
    ("GET", "/api/case-defs/"),            # backfill_review.py 回填前拉全字段(防 PUT 清空)
    ("GET", "/api/suites/"),               # trigger_smoke.py case-paths;nightly 套件读取
    ("POST", "/api/suites/"),              # nightly_trigger.py 套件成员维护
    ("GET", "/api/apks/"),                 # pipeline_agent.py 按 apk_id 下载安装包
    ("POST", "/api/apks/"),                # build_swan_apk.py 构建产物归档上传(登记成功后推 APK 字节)
}
# AI 任务状态机(设计 §3.1/§3.2;merge_status 是任务属性,不是状态)
TASK_STATUSES = ("pending_analysis", "questioning", "running", "done", "failed", "cancelled")
TASK_ACTIVE_STATUSES = ("pending_analysis", "questioning", "running")
TASK_TERMINAL_STATUSES = ("done", "failed", "cancelled")
# 竖版看板五列写死顺序(设计 v1.2 §A2;cancelled 不上板,终态卡片只见 done/failed)
TASK_BOARD_STATUSES = ("pending_analysis", "questioning", "running", "done", "failed")
TASK_BOARD_COLUMN_MAX = 50  # 每列最多返回条数(设计 v1.2 §A8)
TASK_TYPES = ("code_change", "test_run", "troubleshoot", "env_maint", "other")
TASK_RISKS = ("high", "mid", "low")
TARGET_MACHINES = ("167", "176")


class DuplicateUserError(ValueError):
    """设置弹窗添加账号:用户名与既有账号重复(路由层转 409)。"""


class TaskStateError(ValueError):
    """任务状态机非法迁移(dispatch_worker 通道,路由层转 409)。"""


class RunNotFinalizedError(RuntimeError):
    """run 尚未结束(running),不允许回写禅道测试单(路由层转 409)。"""


def apk_file_on_disk(apk: dict[str, Any]) -> bool:
    """登记行对应的 APK 是否可回源:file_path 本机存在,或平台归档副本在
    (跨机登记时构建机文件不可达,下载回源 output/apks/<apk_id>.apk)。"""
    file_path = str(apk.get("file_path") or "").strip()
    if file_path and Path(file_path).is_file():
        return True
    apk_id = str(apk.get("apk_id") or "").strip()
    return bool(apk_id) and (APK_ARCHIVE_DIR / f"{apk_id}.apk").is_file()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_status(value: Any) -> str:
    status = str(value or "error").strip().lower()
    aliases = {
        "pass": "passed",
        "passed": "passed",
        "success": "passed",
        "fail": "failed",
        "failed": "failed",
        "skip": "skipped",
        "skipped": "skipped",
        "error": "error",
        "broken": "error",
    }
    return aliases.get(status, "error")


def json_object(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps({"value": value}, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _stale_cutoff() -> str:
    """busy 设备的自愈阈值:4 小时前(ISO 时间字符串可直接比较)。"""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(timespec="seconds")


# running 任务超时抢回阈值(小时),可用环境变量覆盖。
# 依据:实测单 job 全程最长约 2 分钟(nightly 单文件含 --lf 两轮,2026-09-19 日志),
# 默认 1h 约为实测峰值的 45 倍;远小于设备 4h 自愈阈值,两级防线不打架
RUNNING_TIMEOUT_HOURS = float(os.environ.get("RUNNING_TIMEOUT_HOURS") or "1")


def _running_cutoff() -> str:
    """running 任务抢回阈值:RUNNING_TIMEOUT_HOURS 小时前(ISO 字符串可直接比较)。"""
    return (datetime.now(timezone.utc)
            - timedelta(hours=RUNNING_TIMEOUT_HOURS)).isoformat(timespec="seconds")


# cancelling 任务等待 agent 回报 cancelled 的超时(分钟),可用环境变量覆盖。
# 依据:watchdog 5s 轮询一次旗标 + pytest 进程组终止秒级完成,5 分钟≈60 倍余量;
# agent 离线不回报时由 _force_cancel_stale 按此兜底强制取消
CANCEL_STALE_MINUTES = float(os.environ.get("CANCEL_STALE_MINUTES") or "5")


def _cancel_stale_cutoff() -> str:
    """cancelling 任务强制取消阈值:CANCEL_STALE_MINUTES 分钟前(ISO 字符串可直接比较)。"""
    return (datetime.now(timezone.utc)
            - timedelta(minutes=CANCEL_STALE_MINUTES)).isoformat(timespec="seconds")


def _reap_log(message: str, filename: str = "pipeline_reaper.log") -> None:
    """流水线执行侧独立日志文件(常驻/无控制台下 stdout 不可靠)。"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / filename, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{utc_now()}] {message}\n")


def try_jenkins_dispatch(store, job: dict) -> dict:
    """建任务后尝试派给 Jenkins 构建(M3;Jenkins 侧 job: 参数化,参数 JOB_ID)。

    成功:回填 jenkins_job + 短轮询队列拿 build 号回填(队列积压拿不到则只记
    queue_id 不阻塞);任务状态语义不变(仍 pending,等 Jenkins 启动的
    pipeline_agent --job-id 来定向认领)。
    失败/未配凭据:任务留 pending 走本地 PipelineAgent 轮询兜底,原因记 result_json。
    """
    jenkins_job = os.environ.get("JENKINS_TEST_JOB", "swan-autotest").strip() or "swan-autotest"
    if not jenkins_client.config():
        return job  # 未配置 Jenkins 凭据:静默走本地通道(本地开发/未部署 Jenkins 的机器)
    job_id = job["job_id"]
    # 端透传(2026-10-15):DEVICE_OS 随构建参数下发(任务关联套件的端;''=不限端)。
    # 权威通道仍是认领响应(job dict 含 device_os)——Jenkins 参数是构建记录上的
    # 派发回执;job 未参数化 DEVICE_OS 时 Jenkins 静默忽略,不影响旧 job 定义。
    outcome = jenkins_client.trigger(jenkins_job, {
        "JOB_ID": job_id,
        "DEVICE_OS": str(job.get("device_os") or ""),
    })
    if not outcome.get("ok"):
        _reap_log(f"job={job_id} Jenkins 派发失败,留 pending 本地兜底: {outcome.get('error')}",
                  filename="jenkins_dispatch.log")
        return store.mark_jenkins_dispatch(job_id, trigger_error=str(outcome.get("error")))
    queue_id = outcome.get("queue_id")
    build = None
    if queue_id:
        for _ in range(3):  # 常态下 executor 空闲时 executable.number 立即出现;积压不等待
            item = jenkins_client.queue_item(queue_id)
            if item.get("ok") and item.get("build"):
                build = item["build"]
                break
            time.sleep(1)
    updated = store.mark_jenkins_dispatch(job_id, jenkins_job=jenkins_job,
                                          jenkins_build=build, queue_id=queue_id)
    _reap_log(f"job={job_id} 已派发 Jenkins jenkins_job={jenkins_job} "
              f"queue_id={queue_id} build={build}",
              filename="jenkins_dispatch.log")
    return updated


def _created_ms(job: dict) -> float | None:
    """任务 created_at(UTC ISO)转 epoch 毫秒,与 Jenkins 构建 timestamp 同单位;解析失败返回 None。"""
    try:
        return datetime.fromisoformat(
            str(job.get("created_at") or "").replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _jenkins_build_times(entry: dict) -> tuple[str, str]:
    """Jenkins 构建起止(entry 含 timestamp/duration,epoch 毫秒)转 UTC ISO 秒级。

    对账补行与惰性收尾共用(2026-09-24 修"创建时间=结束时间"):落库用构建
    真实起止,不再落"平台知晓时刻"。缺 timestamp 或构建中(duration<=0)时
    终值给空串,调用方兜底。
    """
    ts = entry.get("timestamp")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "", ""
    start = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    duration = entry.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        return start.isoformat(timespec="seconds"), ""
    end = start + timedelta(milliseconds=duration)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _collect_group_candidates(jenkins_job: str, pipeline_value: str,
                              since_ms: float, kind: str = "pipeline") -> list[tuple[int, float]]:
    """列 jenkins_job 最近构建,筛本组候选:NOTES 匹配且 timestamp >= since_ms,按 timestamp 升序。

    NOTES 触发时 quote 编码(build_swan_apk.py 同规 unquote 解码);列表倒序,
    遇早于下限的构建提前收兵省参数查询。查询失败/无匹配返回 [] → 调用方保持 pending。
    kind=pipeline:NOTES 为 pipeline:<pipeline_value>(立即执行)或 daily:<同值>
    (定时任务,2026-09-29 起区分来源,认领两前缀等价);
    kind=webhook(2026-09-24):NOTES 为 webhook 前缀(裸 webhook 或 webhook:<pusher>)。
    """
    listing = jenkins_client.list_builds(jenkins_job)
    if not listing.get("ok"):
        return []
    expected = f"pipeline:{pipeline_value}"
    expected_daily = f"daily:{pipeline_value}"

    def _match(notes: str) -> bool:
        if kind == "webhook":
            return notes == "webhook" or notes.startswith("webhook:")
        return notes == expected or notes == expected_daily

    matched: list[tuple[int, float]] = []
    for entry in listing.get("builds") or []:
        number, timestamp = entry.get("number"), entry.get("timestamp")
        if not isinstance(number, int) or not isinstance(timestamp, (int, float)):
            continue
        if timestamp < since_ms:
            break  # 倒序:更早的构建不可能属于组内任何任务
        params = jenkins_client.build_parameters(jenkins_job, number)
        if params.get("ok") and _match(unquote(str((params.get("params") or {}).get("NOTES") or ""))):
            matched.append((number, timestamp))
    matched.sort(key=lambda pair: pair[1])
    return matched


def resolve_build_pipeline_jobs(store) -> None:
    """打包型流水线(trigger_source='build'/'webhook')任务的惰性收尾。

    build/webhook 任务均无 agent 回报,靠列表读取时向 Jenkins 回查闭环
    (webhook 行 2026-09-24 加入,候选匹配 NOTES 走 webhook 前缀,build 行
    匹配 pipeline:<config_id> 或 daily:<config_id>):
      - 有构建号 → 查构建结果,终态落库;
      - 只有 queue_id(还在排队)→ 先查队列拿构建号回填,结果下次读取再查;
      - 队列项已被 Jenkins 清掉(构建完成/取消后 404,2026-09-23 补)→
        取组内候选中最早未被占用的构建号,拿不到保持 pending。
    同 config 多任务按 (jenkins_job, pipeline_value) 分组、组内 created_at 升序,
    队列项被清的任务组内先到先得取未占用候选——老任务对老构建、新任务对新构建,
    防同 config 的回查任务抢走后来触发的构建(2026-09-23 线上错配修正;queue
    存活的任务直接拿自己队列项的构建号并同样计入占用)。
    查询失败静默跳过,下次列表再试,不阻塞页面;只扫最近 50 条内的 pending。

    末尾再跑一遍 Jenkins 对账(_reconcile_jenkins_builds,2026-09-24):
    Jenkins 最近构建全量同步到流水线页——手动构建/历史构建补行、进行中的
    构建补"运行中"态,用户要求 Jenkins 上有的任务平台都要能看到。
    """
    if not jenkins_client.config():
        return
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for job in store.list_pipeline_jobs(status="pending", limit=50):
        source = str(job.get("trigger_source") or "")
        if source not in ("build", "webhook"):  # webhook 行 2026-09-24 起同路闭环
            continue
        jenkins_job = str(job.get("jenkins_job") or "")
        if not jenkins_job:
            continue  # 触发失败的任务触发时就落 failed,这里只会是历史脏数据
        pipeline_value = str(((job.get("result") or {}).get("build") or {}).get("pipeline") or "").strip()
        # webhook 行无 config_id,分组键多一维 kind,回查按 NOTES webhook 前缀
        kind = "webhook" if source == "webhook" else "pipeline"
        groups.setdefault((jenkins_job, kind, pipeline_value), []).append(job)
    for (jenkins_job, kind, pipeline_value), members in groups.items():
        # created_at 秒级精度,同秒双触发的先后由 queue_id(Jenkins 全局递增)保序
        members.sort(key=lambda j: (str(j.get("created_at") or ""),
                                    (j.get("result") or {}).get("jenkins_queue_id") or 0))
        used: set[int] = set()

        def _settle(job: dict, build: int, mark: bool) -> None:
            """回填构建号(可选)并查构建结果,终态落库;构建中/查不到留给下次。"""
            if mark:
                store.mark_jenkins_dispatch(job["job_id"], jenkins_job=jenkins_job,
                                            jenkins_build=build)
            used.add(build)
            status = jenkins_client.job_status(jenkins_job, build)
            if not status.get("ok") or status.get("building"):
                return  # 查不到/构建中 → 下次再看
            outcome = str(status.get("result") or "")
            end_at = _jenkins_build_times(status)[1]  # 构建真实结束时刻;缺数据为空串
            if outcome == "SUCCESS":
                store.finish_build_pipeline_job(job["job_id"], "succeeded",
                                                dict(job.get("result") or {}),
                                                finished_at=end_at)
            elif outcome in ("FAILURE", "UNSTABLE", "ABORTED"):
                store.finish_build_pipeline_job(
                    job["job_id"], "failed",
                    {**(job.get("result") or {}), "error": f"Jenkins 构建结果: {outcome}"},
                    finished_at=end_at)

        # 第一遍:已有构建号/队列项存活的任务先闭环——不依赖候选列表,queue 存活组零
        # 回查开销;拿到的构建号计入占用,防同组回查任务抢走
        recheck: list[dict] = []
        for job in members:
            try:
                build = job.get("jenkins_build")
                if build:
                    _settle(job, build, mark=False)
                    continue
                queue_id = (job.get("result") or {}).get("jenkins_queue_id")
                if not queue_id:
                    continue
                item = jenkins_client.queue_item(queue_id)
                if item.get("ok") and item.get("build"):
                    _settle(job, item["build"], mark=True)
                elif item.get("ok"):
                    continue  # build=None:真在排队,构建还没影,不参与回查
                else:
                    recheck.append(job)  # 404/网络失败:队列项多半已被 Jenkins 清掉,交第二遍回查
            except Exception as error:  # noqa: BLE001 — 单任务查询失败不影响列表
                _reap_log(f"resolve build job={job.get('job_id')} 失败: {error}",
                          filename="jenkins_dispatch.log")
        # 第二遍:队列项被清的任务组内有序回查——created_at 升序先到先得,取候选中
        # 最早未被占用的构建号(跨夜挂死的根因即此);无候选/任务缺时间锚不瞎猜
        if recheck:
            anchored = [ms for ms in (_created_ms(j) for j in recheck) if ms is not None]
            try:
                candidates = (_collect_group_candidates(jenkins_job, pipeline_value,
                                                        min(anchored), kind)
                              if anchored and (kind == "webhook" or pipeline_value) else [])
            except Exception as error:  # noqa: BLE001 — 候选查询失败只放弃本轮回查
                candidates = []
                _reap_log(f"resolve build 组 {jenkins_job}/{pipeline_value} 候选查询失败: {error}",
                          filename="jenkins_dispatch.log")
            for job in recheck:  # members 已排过序,recheck 保持 created_at 升序
                try:
                    if not candidates or _created_ms(job) is None:
                        continue
                    build = next((number for number, _ts in candidates
                                  if number not in used), None)
                    if not build:
                        continue
                    _settle(job, build, mark=True)
                except Exception as error:  # noqa: BLE001 — 单任务查询失败不影响列表
                    _reap_log(f"resolve build job={job.get('job_id')} 失败: {error}",
                              filename="jenkins_dispatch.log")
    try:
        _reconcile_jenkins_builds(store)
    except Exception as error:  # noqa: BLE001 — 对账失败不影响列表
        _reap_log(f"reconcile jenkins builds 失败: {error}",
                  filename="jenkins_dispatch.log")


def _reconcile_jenkins_builds(store) -> None:
    """Jenkins↔平台打包任务对账(2026-09-24):swan-build-android 最近构建全量同步。

    与 resolve_build_pipeline_jobs 的按行回查互补,补三类口子:
      - Jenkins 界面直接点的手动构建,平台从未落行 → 新建行;
      - 落行机制上线前已存在的构建(历史回填) → 按 NOTES 归类补行;
      - 行已绑定构建号但还挂 pending(构建进行中) → 补"运行中"态。
    无主构建优先按 NOTES 认领挂起行(webhook 前缀 / pipeline:<id> 精确,
    行创建不得晚于构建开始,created_at 升序先到先得同回查口径),认领不到
    才新建。全部条件更新幂等;查询失败静默返回,下次列表再试。并发读列表
    理论上可能重复补行(无 jenkins_build 唯一约束),入口的
    dedupe_pipeline_build_rows 会在下次读取时自愈去重。
    """
    build_job = os.environ.get("JENKINS_BUILD_JOB", "").strip() or "swan-build-android"
    store.dedupe_pipeline_build_rows(build_job)  # 先治存量重复行(见 store 方法注释)
    listing = jenkins_client.list_builds(build_job)
    if not listing.get("ok"):
        return
    builds: dict[int, dict] = {}
    for entry in listing.get("builds") or []:
        number = entry.get("number")
        if isinstance(number, int):
            builds[number] = entry
    if not builds:
        return
    recent = store.list_pipeline_jobs(limit=50)
    # 构建号占用必须全量查、不能走 recent 窗口:补录行 created_at=构建开始
    # 时间,按 created_at 排序永远沉在窗口外,窗口判占用会反复补行(2026-09-28
    # 线上 #1 积 9 行,见 bound_jenkins_builds)。占用只认同一 Jenkins 任务的
    # 行:测试执行行(swan-autotest)的 jenkins_build 是另一个任务的构建号,
    # 和打包构建号撞号不能算占用(2026-09-24 线上验证发现:swan-autotest 的
    # 19 号行顶掉了打包 19 号)。
    bound = store.bound_jenkins_builds(build_job)

    def _settle(job_id: str, result: dict, number: int, entry: dict) -> None:
        """按构建状态落 运行中/终态(条件更新先到先得;排队瞬态留给下次)。

        终态带构建真实结束时间(2026-09-24):对账落库一律用 Jenkins 构建
        自身的起止,不再用同步时刻。"""
        _start_at, end_at = _jenkins_build_times(entry)
        if entry.get("building"):
            store.mark_build_pipeline_running(job_id, build_job, number)
        elif entry.get("result") == "SUCCESS":
            store.finish_build_pipeline_job(job_id, "succeeded", result,
                                            finished_at=end_at)
        elif entry.get("result") in ("FAILURE", "UNSTABLE", "ABORTED"):
            store.finish_build_pipeline_job(
                job_id, "failed",
                {**result, "error": f"Jenkins 构建结果: {entry.get('result')}"},
                finished_at=end_at)

    # 1) 已绑定构建号的行:构建中补 running,出结果补终态(按行回查只闭环不置 running)
    for job in recent:
        if (str(job.get("trigger_source") or "") not in ("build", "webhook")
                or job.get("status") not in ("pending", "running")):
            continue
        number = job.get("jenkins_build")
        entry = builds.get(number) if isinstance(number, int) else None
        if entry:
            _settle(job["job_id"], dict(job.get("result") or {}), number, entry)

    # 2) 无主构建:按 NOTES 认领挂起行,认领不到新建行(手动/外部构建)
    open_rows = sorted(
        (j for j in recent
         if str(j.get("trigger_source") or "") in ("build", "webhook")
         and j.get("status") == "pending" and not j.get("jenkins_build")),
        key=lambda j: (str(j.get("created_at") or ""),
                       (j.get("result") or {}).get("jenkins_queue_id") or 0))
    taken: set[str] = set()
    for number in sorted(builds):
        if number in bound:
            continue
        entry = builds[number]
        params = jenkins_client.build_parameters(build_job, number)
        bp = (params.get("params") or {}) if params.get("ok") else {}
        notes = unquote(str(bp.get("NOTES") or ""))
        ts = entry.get("timestamp")
        adopted = None
        if isinstance(ts, (int, float)):
            for job in open_rows:  # created_at 升序先到先得,同回查口径
                if job["job_id"] in taken:
                    continue
                created_ms = _created_ms(job)
                if created_ms is None or ts < created_ms:
                    continue  # 构建早于任务创建,不可能是它的
                if str(job.get("trigger_source") or "") == "webhook":
                    matched = notes == "webhook" or notes.startswith("webhook:")
                else:
                    expected = str(((job.get("result") or {}).get("build") or {}).get("pipeline") or "")
                    # pipeline:<id>(立即执行)与 daily:<id>(定时任务)都归属 build 任务行
                    matched = bool(expected) and notes in (f"pipeline:{expected}",
                                                           f"daily:{expected}")
                if matched:
                    adopted = job
                    taken.add(job["job_id"])
                    break
        # 归类:webhook 前缀 → webhook 行;pipeline:/daily:<id> → build 行;其余为手动构建
        meta: dict[str, Any] = {"branch": str(bp.get("BRANCH") or ""),
                                "server_type": str(bp.get("SERVER_TYPE") or "")}
        if notes == "webhook" or notes.startswith("webhook:"):
            source = "webhook"
            meta["notes"] = notes
        elif notes.startswith("pipeline:") or notes.startswith("daily:"):
            source = "build"
            meta["notes"] = notes
            meta["pipeline"] = notes.removeprefix("pipeline:").removeprefix("daily:")
        else:
            source = "build"
            meta["notes"] = notes or "jenkins-manual"
        if adopted is not None:
            store.mark_jenkins_dispatch(adopted["job_id"], jenkins_job=build_job,
                                        jenkins_build=number)
            _settle(adopted["job_id"], dict(adopted.get("result") or {}), number, entry)
            continue
        job = store.create_pipeline_job({"trigger_source": source, "platform": "flutter",
                                         "branch": meta["branch"], "case_filter": "",
                                         "apk_id": "",
                                         "created_at": _jenkins_build_times(entry)[0]})
        store.mark_jenkins_dispatch(job["job_id"], jenkins_job=build_job,
                                    jenkins_build=number, result_extra={"build": meta})
        _settle(job["job_id"], {"build": meta}, number, entry)

    # 3) 存量修复(2026-09-24):首版对账补行把 created_at/finished_at 落成同一
    #    同步时刻("创建时间=结束时间"的由来)。补行病的签名是"行创建时刻晚于
    #    构建开始"(原生任务必然先建行后派发,创建不会晚于构建开始),只修这类
    #    行;正常行(含同秒闭环的原生行)一律不动,防止误伤。
    for job in recent:
        if (str(job.get("trigger_source") or "") not in ("build", "webhook")
                or job.get("status") not in PIPELINE_JOB_FINISHED_STATUSES
                or not isinstance(job.get("jenkins_build"), int)):
            continue
        entry = builds.get(job["jenkins_build"])
        if not entry:
            continue
        start_at, end_at = _jenkins_build_times(entry)
        created_ms = _created_ms(job)
        if not start_at or created_ms is None or created_ms <= entry["timestamp"] + 1000:
            continue  # 缺时间锚/行创建早于构建开始:正常行,不动
        store.fix_reconciled_job_times(job["job_id"], start_at, end_at)


# 包管理对账用控制台行(2026-09-28):build_swan_apk.py 的检出/登记/归档输出。
# sha256 捕获兼容新旧脚本:旧版只打 12 位前缀+省略号,新版打全量 64 位。
_APK_REG_OK_RE = re.compile(
    r"^登记成功 apk_id=(\S+) sha256=([0-9a-f]+)…? server_type=(\S+)\s*$", re.M)
_APK_REG_FAIL_RE = re.compile(r"^登记失败:.*产物仍在 ([^\s)]+)\)?\s*$", re.M)
_APK_ARCHIVE_RE = re.compile(r"^归档成功 bytes=(\d+) → (\S+)\s*$", re.M)
_APK_CHECKOUT_RE = re.compile(r"^检出完成 commit=([0-9a-f]{7,40})\s*$", re.M)
_APK_MODE_RE = re.compile(
    r"^== 步骤 2/3 flutter build apk --(profile|release) ==\s*$", re.M)
_APK_STEP3_RE = re.compile(
    r"^== 步骤 3/3 登记平台\(\S+ server_type=(\w+)\)==\s*$", re.M)


def reconcile_apks_from_jenkins(store) -> None:
    """包管理↔Jenkins 对账(2026-09-28):swan-build-android 的 SUCCESS 构建补录 apks。

    背景:apks 行由打包脚本最后一步 POST /api/apks 直写,该步失败(平台
    瞬断等)则 Jenkins SUCCESS 构建在包管理永久缺行——Jenkins/流水线页/
    包管理三处历史要求一致,不可缺不可错(流水线页 2026-09-24 已有对账,
    此处补齐包管理侧)。从构建入参+控制台文本反向补录:
      - 只认 SUCCESS;FAILURE/UNSTABLE/进行中一律不动(失败本就不应有包行);
      - 新行整行插入;已存在行仅回填空缺 jenkins_job/jenkins_build(手跑包行
        的关联修复),其余字段绝不覆盖(register_apk_if_absent);
      - 登记成功路径:字段来自控制台(sha256 新旧脚本格式都认,旧版仅 12 位
        前缀,如需全量可由归档文件现算);
      - 登记失败路径(平台瞬断的缺口本体):apk_id/产物路径取自"产物仍在"
        行,file_hash 留空,notes 注明产物在构建机未上传平台;
      - 惰性触发:GET /api/apks 列表时跑。稳态开销=list_builds 一次,控制台
        只对 Jenkins 号未被 apks 覆盖的构建拉取;单条补录异常跳过不阻塞。
    """
    build_job = os.environ.get("JENKINS_BUILD_JOB", "").strip() or "swan-build-android"
    if not jenkins_client.config():
        return
    listing = jenkins_client.list_builds(build_job)
    if not listing.get("ok"):
        return
    # 覆盖判定全量查,不走 list_apks 窗口(同 bound_jenkins_builds 的窗口教训:
    # 补录行 registered_at=构建开始时间,行多了以后沉出窗口,会反复拉控制台
    # 重算;register_apk_if_absent 幂等保证不会重复插行,差的是无谓开销)
    covered = store.apk_bound_builds(build_job)
    for entry in listing.get("builds") or []:
        if entry.get("building") or entry.get("result") != "SUCCESS":
            continue
        number = entry.get("number")
        if not isinstance(number, int) or number in covered:
            continue
        console = jenkins_client.console_text(build_job, number)
        if not console.get("ok"):
            continue
        text = str(console.get("text") or "")
        params = jenkins_client.build_parameters(build_job, number)
        build_params = (params.get("params") or {}) if params.get("ok") else {}
        start_at, _ = _jenkins_build_times(entry)
        mode_match = _APK_MODE_RE.search(text)
        step3 = _APK_STEP3_RE.search(text)
        checkout = _APK_CHECKOUT_RE.search(text)
        registered = _APK_REG_OK_RE.search(text)
        if registered:
            archive = _APK_ARCHIVE_RE.search(text)
            payload = {
                "apk_id": registered.group(1),
                "branch": str(build_params.get("BRANCH") or ""),
                "commit_hash": checkout.group(1)[:7] if checkout else "",
                "flavor": mode_match.group(1) if mode_match else "",
                "source": "jenkins-reconcile",
                "server_type": registered.group(3),
                "build_time": start_at,
                "registered_at": start_at,
                "jenkins_job": build_job,
                "jenkins_build": str(number),
                "file_path": archive.group(2) if archive else "",
                "file_hash": registered.group(2),
                "notes": f"对账补录:Jenkins 构建 #{number},字段由控制台解析",
            }
        else:
            missed = _APK_REG_FAIL_RE.search(text)
            if not missed:
                continue  # 登记步骤之前就失败/旧版脚本无登记行:无从补录
            path = missed.group(1)
            payload = {
                "apk_id": os.path.basename(path).removesuffix(".apk"),
                "branch": str(build_params.get("BRANCH") or ""),
                "commit_hash": checkout.group(1)[:7] if checkout else "",
                "flavor": mode_match.group(1) if mode_match else "",
                "source": "jenkins-reconcile",
                # 参数查询失败也不能默认 test(formal 会错标):回退脚本步骤 3 行
                "server_type": str(build_params.get("SERVER_TYPE")
                                   or (step3.group(1) if step3 else "")).strip().lower(),
                "build_time": start_at,
                "registered_at": start_at,
                "jenkins_job": build_job,
                "jenkins_build": str(number),
                "file_path": path,
                "file_hash": "",
                "notes": (f"对账补录:Jenkins 构建 #{number} 当时登记失败"
                          "(产物仍在构建机,未上传平台,file_hash 缺)"),
            }
        try:
            store.register_apk_if_absent(payload)
        except ValueError:
            continue  # server_type 等字段异常的构建:跳过,不影响其余补录


def _reap_timeout_jobs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """把 running 超过阈值的任务标记 error 并释放其占用设备(幂等,可重复调用)。

    触发时机:claim 前惰性回收——任一执行 agent 轮询即触发,与 busy 设备 4h 自愈
    同为惰性模式。所有 UPDATE 带状态条件,并发/重复执行均无副作用。
    """
    cutoff = _running_cutoff()
    stale = connection.execute(
        """SELECT job_id, claimed_by, claimed_at FROM pipeline_jobs
           WHERE status='running' AND claimed_at < ?""",
        (cutoff,),
    ).fetchall()
    for row in stale:
        job_id = row["job_id"]
        connection.execute(
            """UPDATE pipeline_jobs SET status='error', finished_at=?, result_json=?
               WHERE job_id=? AND status='running' AND claimed_at < ?""",
            (utc_now(), json.dumps({
                "reaped": True,
                "reason": f"running 超时(>{RUNNING_TIMEOUT_HOURS:g}h)被平台抢回",
                "claimed_by": row["claimed_by"],
                "claimed_at": row["claimed_at"],
            }, ensure_ascii=False), job_id, cutoff),
        )
        released = connection.execute(
            """UPDATE devices SET status='online_idle', current_run='', busy_since=NULL,
               last_heartbeat=? WHERE status='busy' AND current_run=?""",
            (utc_now(), f"pipe-{job_id}"),
        ).rowcount
        _reap_log(f"job={job_id} running 超时抢回(claimed_by={row['claimed_by']} "
                  f"claimed_at={row['claimed_at']}),释放设备 {released} 台")
    return [dict(row) for row in stale]


def _force_cancel_stale(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """把 cancelling 超时无回报的任务强制落 cancelled 并释放设备(幂等,可重复调用)。

    取消兜底:agent 离线/崩溃时收不到它的 finish(status=cancelled),按
    CANCEL_STALE_MINUTES 超时由平台强制取消。触发时机与 _reap_timeout_jobs 同
    (claim 前惰性回收)。UPDATE 带状态条件,与 agent 迟到的 finish 竞态先到先得,
    后到方无副作用。
    """
    cutoff = _cancel_stale_cutoff()
    stale = connection.execute(
        """SELECT job_id, claimed_by, cancel_requested_at FROM pipeline_jobs
           WHERE status='cancelling' AND cancel_requested_at < ?""",
        (cutoff,),
    ).fetchall()
    for row in stale:
        job_id = row["job_id"]
        connection.execute(
            """UPDATE pipeline_jobs SET status='cancelled', finished_at=?, result_json=?
               WHERE job_id=? AND status='cancelling'""",
            (utc_now(), json.dumps({
                "forced_cancel": True,
                "reason": f"取消中超时(>{CANCEL_STALE_MINUTES:g} 分钟无 agent 回报)被平台强制取消",
                "claimed_by": row["claimed_by"],
                "cancel_requested_at": row["cancel_requested_at"],
            }, ensure_ascii=False), job_id),
        )
        released = connection.execute(
            """UPDATE devices SET status='online_idle', current_run='', busy_since=NULL,
               last_heartbeat=? WHERE status='busy' AND current_run=?""",
            (utc_now(), f"pipe-{job_id}"),
        ).rowcount
        _reap_log(f"job={job_id} cancelling 超时强制取消(cancel_requested_at="
                  f"{row['cancel_requested_at']}),释放设备 {released} 台")
    return [dict(row) for row in stale]


# ---- runs 会话孤儿收尾:执行 agent 失联时,由平台代管把卡死的 running 会话收尾 ----

# 执行 agent 上报的 run_id 约定为 pipe-{job_id}(scripts/pipeline_agent.py 经
# DASHBOARD_RUN_ID 落库),runs 侧据此反查所属 pipeline job,不依赖 job 侧改动。
_PIPELINE_RUN_ID_PREFIX = "pipe-"
# job 终态集合:与 finish_pipeline_job 的校验一致;error 亦含 _reap_timeout_jobs 超时抢回,
# cancelled 含用户取消与 _force_cancel_stale 强制取消(任务取消功能 M-B)
PIPELINE_JOB_FINISHED_STATUSES = ("succeeded", "failed", "env_blocked", "error", "cancelled")


def _sweep_log(message: str) -> None:
    """会话代管收尾写独立日志(常驻/无控制台下 stdout 不可靠,对齐 _reap_log)。"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "runs_sweeper.log", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{utc_now()}] {message}\n")


# ==================== 流水线配置:频率解析与下次执行时间(纯函数,便于单测) ====================

PIPELINE_SCHEDULE_TYPES = ("once", "daily", "interval", "weekly")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: Any) -> tuple[int, int]:
    """校验 'HH:MM'(24 小时制)并拆成 (hour, minute);非法抛 ValueError。"""
    text = str(value or "").strip()
    match = _HHMM_RE.match(text)
    if not match:
        raise ValueError(f"time 必须是合法的 HH:MM(24 小时制),收到: {value!r}")
    return int(match.group(1)), int(match.group(2))


def parse_run_at(value: Any) -> datetime:
    """解析 once.run_at,接受 'YYYY-MM-DD HH:MM' 或 ISO 格式;返回本地 naive 分钟精度。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("once.run_at 必填,格式 YYYY-MM-DD HH:MM")
    try:
        moment = datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        raise ValueError(f"run_at 时间格式非法(应为 YYYY-MM-DD HH:MM): {value!r}") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.replace(second=0, microsecond=0)


def validate_schedule_param(schedule_type: str, param: dict[str, Any]) -> None:
    """校验各频率的参数结构,非法抛 ValueError(建/改配置时把关)。"""
    if schedule_type == "once":
        parse_run_at(param.get("run_at"))
    elif schedule_type == "daily":
        parse_hhmm(param.get("time"))
    elif schedule_type == "interval":
        try:
            every_days = int(param.get("every_days"))
        except (TypeError, ValueError):
            raise ValueError("interval.every_days 必须是正整数") from None
        if every_days < 1:
            raise ValueError("interval.every_days 必须 >= 1")
        parse_hhmm(param.get("time"))
    elif schedule_type == "weekly":
        weekdays = param.get("weekdays")
        if not isinstance(weekdays, list) or not weekdays:
            raise ValueError("weekly.weekdays 必须是非空数组(0=周日 … 6=周六)")
        try:
            normalized = {int(w) for w in weekdays}
        except (TypeError, ValueError):
            raise ValueError("weekly.weekdays 必须是 0-6 的整数数组") from None
        if not normalized.issubset(set(range(7))):
            raise ValueError("weekly.weekdays 取值必须落在 0-6(0=周日)")
        parse_hhmm(param.get("time"))


def validate_pipeline_config(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化流水线配置字段;schedule_param 统一落成紧凑 JSON 字符串。"""
    config_id = str(payload.get("config_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not config_id:
        raise ValueError("config_id 必填")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", config_id):
        raise ValueError("config_id 只能包含字母/数字/._- 且长度 1-64")
    if not name:
        raise ValueError("name 必填")
    platform = str(payload.get("platform") or "flutter-android").strip().lower()
    if platform == "flutter":
        # 旧称归一化(2026-10-15):flutter=安卓真机旧称,新写入一律落 flutter-android
        platform = "flutter-android"
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform 必须是 {sorted(VALID_PLATFORMS)} 之一")
    schedule_type = str(payload.get("schedule_type") or "").strip().lower()
    if schedule_type not in PIPELINE_SCHEDULE_TYPES:
        raise ValueError(f"schedule_type 必须是 {'/'.join(PIPELINE_SCHEDULE_TYPES)}")
    raw_param = payload.get("schedule_param")
    if isinstance(raw_param, str):
        try:
            param = json.loads(raw_param or "{}")
        except json.JSONDecodeError:
            raise ValueError("schedule_param 不是合法 JSON") from None
    else:
        param = raw_param or {}
    if not isinstance(param, dict):
        raise ValueError("schedule_param 必须是 JSON 对象")
    validate_schedule_param(schedule_type, param)
    # kind=suite 测试流水线(原语义);kind=build 打包流水线:到点直接触发打包 job,
    # build_param 记 分支/环境;套件/平台/执行前先打包对打包流水线无意义,一律置空
    kind = str(payload.get("kind") or "suite").strip().lower()
    if kind not in ("suite", "build"):
        raise ValueError("kind 必须是 suite/build 之一")
    build_param = "{}"
    if kind == "build":
        raw_build = payload.get("build_param")
        if isinstance(raw_build, str):
            try:
                build = json.loads(raw_build or "{}")
            except json.JSONDecodeError:
                raise ValueError("build_param 不是合法 JSON") from None
        else:
            build = raw_build or {}
        if not isinstance(build, dict):
            raise ValueError("build_param 必须是 JSON 对象")
        build_branch = str(build.get("branch") or "dev").strip()
        # 与 /api/builds 同一白名单:BRANCH 原样拼进打包机批处理命令行,防注入(坑 15)
        if not re.fullmatch(r"[A-Za-z0-9._/\-]+", build_branch):
            raise ValueError("branch 只允许字母/数字/._/与-字符")
        build_server = str(build.get("server_type") or "test").strip().lower()
        if build_server not in VALID_SERVER_TYPES:
            raise ValueError(f"server_type 必须是 {'/'.join(VALID_SERVER_TYPES)} 之一")
        build_param = json.dumps({"branch": build_branch, "server_type": build_server},
                                 ensure_ascii=False)
    return {
        "config_id": config_id,
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "suite_id": "" if kind == "build" else str(payload.get("suite_id") or "").strip(),
        "include_build": 0 if kind == "build" else (
            1 if payload.get("include_build") in (1, True, "1", "true", "on") else 0),
        "schedule_type": schedule_type,
        "schedule_param": json.dumps(param, ensure_ascii=False),
        "enabled": 0 if payload.get("enabled") in (0, False, "0", "false", "off") else 1,
        "platform": platform,
        "kind": kind,
        "build_param": build_param,
    }


# ==================== 测试套件(M4 P1):校验纯函数 ====================

SUITE_TYPES = ("smoke", "regression", "custom")

# 套件「端」枚举(2026-10-15):设备 OS 维度,执行时按端过滤设备,跑错比漏跑严重。
# 存英文枚举入库,UI 显示 安卓/iOS/鸿蒙;注意荣耀 MagicOS 是安卓端,不是鸿蒙端。
SUITE_OS_TYPES = ("android", "ios", "harmony")

# 已知模块 slug 的官方中文名。种子/自动登记时用它替代"中文名=slug"的占位,
# 避免模块管理里出现一排英文占位名;用户改名后不会被覆盖(见 initialize 的迁移条件)。
MODULE_CN_SEED = {
    "fullregression": "全量回归",
    "login": "登录",
}


def validate_test_suite(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化测试套件字段:suite_id/name 必填,suite_type/os_type 限定枚举。"""
    suite_id = str(payload.get("suite_id") or "").strip()
    if not suite_id:
        raise ValueError("suite_id 必填")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name 必填")
    suite_type = str(payload.get("suite_type") or "regression").strip().lower()
    if suite_type not in SUITE_TYPES:
        raise ValueError(f"suite_type 必须是 {'/'.join(SUITE_TYPES)} 之一")
    # 端缺省 android(与存量迁移同语义:旧套件一律安卓端)
    os_type = str(payload.get("os_type") or "android").strip().lower()
    if os_type not in SUITE_OS_TYPES:
        raise ValueError(f"os_type 必须是 {'/'.join(SUITE_OS_TYPES)} 之一")
    return {
        "suite_id": suite_id,
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "suite_type": suite_type,
        "os_type": os_type,
        "enabled": 0 if payload.get("enabled") in (0, False, "0", "false", "off") else 1,
    }


def _normalize_priority(value: Any) -> str:
    """优先级归一(UI 规范 2.3):统一四档 P0-P3;旧档 L1/L2/L3 入库映射,非标值归 P3。"""
    priority = str(value or "").strip().upper()
    if not priority:
        return ""
    priority = {"L1": "P1", "L2": "P2", "L3": "P3"}.get(priority, priority)
    return priority if priority in {"P0", "P1", "P2", "P3"} else "P3"


def compute_next_run_at(
    schedule_type: str,
    schedule_param: str | dict,
    now: datetime | None = None,
    interval_anchor: str = "1970-01-01",
) -> str:
    """计算下次执行时间(本地 naive ISO 字符串,分钟精度);无法计算返回 ''。

    语义:返回 >= now(截断到分钟)的最近一次命中时刻。因此调度器按
    next_run_at <= now 触发时,天然只在"命中那一分钟"内生效,不会补跑陈旧欠账。
    interval 以 interval_anchor(默认 1970-01-01)为相位锚点,保证同参数计算确定。
    weekly.weekdays 用前端约定 0=周日 … 6=周六,内部换算成 Python weekday()。
    """
    if isinstance(schedule_param, str):
        try:
            param = json.loads(schedule_param or "{}")
        except json.JSONDecodeError:
            return ""
    else:
        param = schedule_param or {}
    if not isinstance(param, dict):
        return ""
    base = (now or datetime.now()).replace(second=0, microsecond=0)
    if base.tzinfo is not None:
        base = base.astimezone().replace(tzinfo=None)
    try:
        if schedule_type == "once":
            target = parse_run_at(param.get("run_at"))
            return target.isoformat(timespec="seconds") if target >= base else ""
        if schedule_type == "daily":
            hour, minute = parse_hhmm(param.get("time"))
            candidate = base.replace(hour=hour, minute=minute)
            if candidate < base:
                candidate += timedelta(days=1)  # 当天时刻已过 → 明天同一时刻(跨天)
            return candidate.isoformat(timespec="seconds")
        if schedule_type == "interval":
            every_days = int(param.get("every_days"))
            if every_days < 1:
                return ""
            hour, minute = parse_hhmm(param.get("time"))
            anchor_date = date.fromisoformat(str(interval_anchor)[:10])
            for offset in range(every_days + 1):
                day = base.date() + timedelta(days=offset)
                if (day - anchor_date).days % every_days == 0:
                    candidate = datetime.combine(day, dt_time(hour, minute))
                    if candidate >= base:
                        return candidate.isoformat(timespec="seconds")
            return ""
        if schedule_type == "weekly":
            hour, minute = parse_hhmm(param.get("time"))
            weekdays = {((int(w) - 1) % 7) for w in param.get("weekdays") or []}
            for offset in range(8):  # 7 天内必命中,留 1 天余量(跨周边界)
                day = base.date() + timedelta(days=offset)
                if day.weekday() in weekdays:
                    candidate = datetime.combine(day, dt_time(hour, minute))
                    if candidate >= base:
                        return candidate.isoformat(timespec="seconds")
            return ""
    except (ValueError, TypeError):
        return ""
    return ""


class DashboardStore:
    def __init__(self, database_path: str | Path = DEFAULT_DATABASE):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._active_users: set[str] | None = None  # 启用用户名缓存,写操作时置 None
        self._test_method_cache: dict[str, tuple[float, str]] = {}  # 脚本路径 -> (mtime, 首个 def test_*)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    app_version TEXT NOT NULL DEFAULT '',
                    device TEXT NOT NULL DEFAULT '',
                    jenkins_build TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    total_cases INTEGER NOT NULL DEFAULT 0,
                    passed_cases INTEGER NOT NULL DEFAULT 0,
                    failed_cases INTEGER NOT NULL DEFAULT 0,
                    skipped_cases INTEGER NOT NULL DEFAULT 0,
                    error_cases INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    zentao_task_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cases (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    log TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    artifacts_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (run_id, case_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_cases_run_id ON cases(run_id);
                CREATE INDEX IF NOT EXISTS idx_cases_case_id ON cases(case_id);
                """
            )
            # 轻量迁移：老库补列（runs.apk_id / runs.zentao_task_id 禅道测试单单号,M6）
            run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if "apk_id" not in run_columns:
                connection.execute("ALTER TABLE runs ADD COLUMN apk_id TEXT NOT NULL DEFAULT ''")
            if "zentao_task_id" not in run_columns:
                connection.execute("ALTER TABLE runs ADD COLUMN zentao_task_id TEXT NOT NULL DEFAULT ''")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS apks (
                    apk_id TEXT PRIMARY KEY,
                    app_version TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    commit_hash TEXT NOT NULL DEFAULT '',
                    flavor TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    file_path TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    server_type TEXT NOT NULL DEFAULT 'test',
                    os_type TEXT NOT NULL DEFAULT 'android',
                    build_time TEXT,
                    jenkins_job TEXT NOT NULL DEFAULT '',
                    jenkins_build TEXT NOT NULL DEFAULT '',
                    registered_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_apks_registered_at ON apks(registered_at DESC);
                """
            )
            # server_type/os_type/build_time/jenkins_* 为后加列:存量库补列(存量包全是
            # 开发/测试产出,归测试服;os_type 缺省 android,swan-ios- 前缀回填 ios,只在
            # 加列那一次执行,避免每次启动覆写显式登记值;build_time 可空;jenkins_* 记录
            # 触发该包构建的 Jenkins job 与 build 号,空=无构建来源)
            apk_columns = {row[1] for row in connection.execute("PRAGMA table_info(apks)")}
            if "server_type" not in apk_columns:
                connection.execute(
                    "ALTER TABLE apks ADD COLUMN server_type TEXT NOT NULL DEFAULT 'test'"
                )
            if "build_time" not in apk_columns:
                connection.execute("ALTER TABLE apks ADD COLUMN build_time TEXT")
            if "jenkins_job" not in apk_columns:
                connection.execute(
                    "ALTER TABLE apks ADD COLUMN jenkins_job TEXT NOT NULL DEFAULT ''"
                )
            if "jenkins_build" not in apk_columns:
                connection.execute(
                    "ALTER TABLE apks ADD COLUMN jenkins_build TEXT NOT NULL DEFAULT ''"
                )
            if "os_type" not in apk_columns:
                connection.execute(
                    "ALTER TABLE apks ADD COLUMN os_type TEXT NOT NULL DEFAULT 'android'"
                )
                connection.execute(
                    "UPDATE apks SET os_type='ios' WHERE apk_id LIKE 'swan-ios-%'"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'flutter',
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'online_idle',
                    current_run TEXT NOT NULL DEFAULT '',
                    busy_since TEXT,
                    last_assigned_at TEXT,
                    last_heartbeat TEXT,
                    registered_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
                CREATE TABLE IF NOT EXISTS pipeline_jobs (
                    job_id TEXT PRIMARY KEY,
                    trigger_source TEXT NOT NULL DEFAULT 'manual',
                    branch TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'flutter',
                    case_filter TEXT NOT NULL DEFAULT '',
                    apk_id TEXT,
                    device_os TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    finished_at TEXT,
                    jenkins_job TEXT NOT NULL DEFAULT '',
                    jenkins_build INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs(status);
                CREATE TABLE IF NOT EXISTS case_defs (
                    case_id TEXT PRIMARY KEY,
                    case_type TEXT NOT NULL DEFAULT 'auto',
                    title TEXT NOT NULL DEFAULT '',
                    module TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    source_bug TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    linked_case_id TEXT NOT NULL DEFAULT '',
                    link_strength TEXT NOT NULL DEFAULT 'strong',
                    human_confirmed INTEGER NOT NULL DEFAULT 0,
                    biz_no TEXT NOT NULL DEFAULT '',
                    case_scope TEXT NOT NULL DEFAULT '',
                    structured TEXT NOT NULL DEFAULT '',
                    review_meta TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_case_defs_module ON case_defs(module);
                """
            )
            # link_strength 为后加列:存量库补列(默认 strong,sync_cases.py 同步时会按来源纠正)
            existing_cols = {row[1] for row in connection.execute("PRAGMA table_info(case_defs)")}
            if "link_strength" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN link_strength TEXT NOT NULL DEFAULT 'strong'"
                )
            # human_confirmed 为后加列(2026-09-29):「经人审工作站两道门确认」标志,
            # 0=未确认 1=已确认;仅 API 拉取(status=stored)显式写 1,其余来源缺省 0
            if "human_confirmed" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN human_confirmed INTEGER NOT NULL DEFAULT 0"
                )
            # biz_no 为后加列(2026-09-30):平台有义展示编号(wifi-001 式),仅 API
            # 拉取管线按模块分配;存量行留空,下次重拉自动补号,展示回退原始编号
            if "biz_no" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN biz_no TEXT NOT NULL DEFAULT ''"
                )
            # case_scope 为后加列(2026-10-09):用例范围分类,''=未分类/full=全量用例/
            # regression=回归用例。存量 REG- 前缀用例经用户确认全是回归用例,补列时
            # 一次性归类(仅补列分支内执行一次,幂等;新建库走上方 DDL 无存量行无需回填)
            if "case_scope" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN case_scope TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "UPDATE case_defs SET case_scope='regression' WHERE case_id LIKE 'REG-%'"
                )
            # structured 为后加列(2026-10-13,用例域整合 P1):工作站原始报文 JSON
            # (definition/gates/formerId 全量),content 的机器可读对应物——
            # content 给人看(渲染文本),structured 给机器用(工作站还原 cases/0.3 格式)
            if "structured" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN structured TEXT NOT NULL DEFAULT ''"
                )
            # review_meta 为后加列(2026-10-13,用例域整合 P2 共库):工作站人审状态
            # JSON(status/reason/note/reviewer/decidedAt/gates)——原 decisions-<版本>.json
            # 的库内化;human_confirmed 是其 status='stored' 的冗余投影(SQL 可过滤)。
            # 工作站 serve.py 共库后直写本列,不再落文件
            if "review_meta" not in existing_cols:
                connection.execute(
                    "ALTER TABLE case_defs ADD COLUMN review_meta TEXT NOT NULL DEFAULT '{}'"
                )
            # 流水线定时配置(调度器每 30s 轮询,到点 POST {id}/run 建 schedule 任务)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pipeline_configs (
                    config_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    suite_id TEXT NOT NULL DEFAULT '',
                    include_build INTEGER NOT NULL DEFAULT 0,
                    schedule_type TEXT NOT NULL,
                    schedule_param TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'flutter',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # platform 为后加列:存量库补列(默认 flutter,兼容老配置)
            existing_cols = {row[1] for row in connection.execute("PRAGMA table_info(pipeline_configs)")}
            if "platform" not in existing_cols:
                connection.execute(
                    "ALTER TABLE pipeline_configs ADD COLUMN platform TEXT NOT NULL DEFAULT 'flutter'"
                )
            # kind/build_param 为后加列(打包型流水线):kind=suite 测试流水线(原语义),
            # kind=build 到点直接触发打包 job 出包,build_param 记 分支/环境
            if "kind" not in existing_cols:
                connection.execute(
                    "ALTER TABLE pipeline_configs ADD COLUMN kind TEXT NOT NULL DEFAULT 'suite'"
                )
            if "build_param" not in existing_cols:
                connection.execute(
                    "ALTER TABLE pipeline_configs ADD COLUMN build_param TEXT NOT NULL DEFAULT '{}'"
                )
            # cancel_requested/cancel_requested_at 为后加列:任务取消功能(M-B),
            # 后者为 cancelling 超时强制取消的计时起点(_force_cancel_stale)
            existing_cols = {row[1] for row in connection.execute("PRAGMA table_info(pipeline_jobs)")}
            if "cancel_requested" not in existing_cols:
                connection.execute(
                    "ALTER TABLE pipeline_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
                )
            if "cancel_requested_at" not in existing_cols:
                connection.execute(
                    "ALTER TABLE pipeline_jobs ADD COLUMN cancel_requested_at TEXT"
                )
            # 平台口径迁移(2026-10-15):flutter(安卓真机旧称)统一改 flutter-android。
            # 每次启动幂等执行——旧调用点(对账补行/nightly bootstrap)再写入 flutter
            # 也会在下一轮启动被归一;runs 执行历史不动(记录当时实际运行环境)
            connection.execute(
                "UPDATE pipeline_configs SET platform='flutter-android' WHERE platform='flutter'")
            connection.execute(
                "UPDATE pipeline_jobs SET platform='flutter-android' WHERE platform='flutter'")
            # 测试套件(M4 P1):套件定义 + 套件↔用例多对多成员(seq 决定执行顺序)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS test_suites (
                    suite_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    suite_type TEXT NOT NULL DEFAULT 'regression',
                    os_type TEXT NOT NULL DEFAULT 'android',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suite_cases (
                    suite_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (suite_id, case_id),
                    FOREIGN KEY (suite_id) REFERENCES test_suites(suite_id) ON DELETE CASCADE,
                    FOREIGN KEY (case_id) REFERENCES case_defs(case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_suite_cases_case_id ON suite_cases(case_id);
                """
            )
            # os_type 为后加列(2026-10-15,套件引入「端」):存量库补列,存量套件
            # 默认 android(与新建缺省同语义);枚举校验在 validate_test_suite
            existing_cols = {row[1] for row in connection.execute("PRAGMA table_info(test_suites)")}
            if "os_type" not in existing_cols:
                connection.execute(
                    "ALTER TABLE test_suites ADD COLUMN os_type TEXT NOT NULL DEFAULT 'android'"
                )
            # 功能模块表(模块管理):slug 主键 + 中文名;建表时用现有用例的 distinct module 自动播种
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS modules (
                    slug TEXT PRIMARY KEY,
                    name_cn TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT 'swan',
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            connection.execute(
                """INSERT OR IGNORE INTO modules (slug, name_cn)
                   SELECT DISTINCT module, module FROM case_defs WHERE module != ''"""
            )
            # 一次性存量迁移:早期种子把中文名写成了 slug,这里只把"未翻译"
            # (name_cn=slug)的行替换为官方中文名,用户改过的名字不动,幂等可重复执行
            for slug, name_cn in MODULE_CN_SEED.items():
                connection.execute(
                    "UPDATE modules SET name_cn=? WHERE slug=? AND name_cn=slug",
                    (name_cn, slug),
                )
            # 轻量迁移:老库补 Jenkins 派发两列(M3);新库 DDL 已含,补列仅对存量库生效
            # (放所有建表 executescript 之后,pipeline_jobs 表此时必然存在)
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(pipeline_jobs)")}
            if "jenkins_job" not in job_columns:
                connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN jenkins_job TEXT NOT NULL DEFAULT ''")
            # device_os 为后加列(2026-10-15,任务带「端」):''=不限端(旧行为,全设备),
            # 建任务时从套件 os_type 解析;存量行一律 '',认领响应透传执行端
            if "device_os" not in job_columns:
                connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN device_os TEXT NOT NULL DEFAULT ''")
            if "jenkins_build" not in job_columns:
                connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN jenkins_build INTEGER")
            # 平台登录与 AI 任务看板(v1.1,设计 §8):users=登录身份+企微映射,
            # sessions 表有意不做(cookie 即身份,无服务端会话状态可存)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    wecom_userid TEXT NOT NULL DEFAULT '',
                    surname TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    submitter TEXT NOT NULL,
                    desired_time TEXT,
                    status TEXT NOT NULL DEFAULT 'pending_analysis',
                    paused_phase TEXT,
                    task_type TEXT,
                    risk TEXT,
                    target_machine TEXT,
                    analysis_note TEXT,
                    branch_name TEXT,
                    worktree_path TEXT,
                    merge_status TEXT,
                    report_md TEXT,
                    error_message TEXT,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
                CREATE TABLE IF NOT EXISTS task_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    asked_at TEXT NOT NULL,
                    answered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_task_questions_task ON task_questions(task_id);
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);
                """
            )
            # 预设账号种子:users 空表时写入 4 个预设账号("部署侧种子"的落地形态,
            # 幂等可重复执行;否则空 users 表等于全平台锁死,无人能登录也无人能加账号)
            if connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
                for username in PRESET_USERS:
                    connection.execute(
                        "INSERT INTO users (username, wecom_userid, surname, status, created_at)"
                        " VALUES (?, '', ?, 'active', ?)",
                        (username, PRESET_SURNAMES[username], utc_now()),
                    )
            # 轻量迁移:老库补 users.surname(v1.2 头像姓氏全拼);存量库 ALTER 补列后
            # 对预设账号回填空姓氏(新库 DDL 已含,补列仅对存量库生效,幂等)
            user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
            if "surname" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN surname TEXT NOT NULL DEFAULT ''")
            for username, surname in PRESET_SURNAMES.items():
                connection.execute(
                    "UPDATE users SET surname=? WHERE username=? AND surname=''",
                    (surname, username),
                )

    # ---- 平台登录(users 表;cookie 即身份,逐请求校验在册且启用)----

    def auth_check(self, username: str) -> str | None:
        """cookie 里的用户名 → 在册且启用返回原名,否则 None(名单缓存,写操作失效)。"""
        if self._active_users is None:
            with self.transaction() as connection:
                rows = connection.execute(
                    "SELECT username FROM users WHERE status='active'").fetchall()
            self._active_users = {row["username"] for row in rows}
        return username if username in self._active_users else None

    def list_presets(self) -> list[dict[str, Any]]:
        """登录页头像卡名单:仅启用账号(停用即退出名单,设计 §2.4)。"""
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT username, surname FROM users WHERE status='active'"
                " ORDER BY created_at").fetchall()
        return [{"username": row["username"], "surname": row["surname"] or ""} for row in rows]

    @staticmethod
    def _serialize_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "username": row["username"],
            "wecom_userid": row["wecom_userid"],
            "surname": row["surname"] or "",
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def list_users(self) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._serialize_user(row) for row in rows]

    def get_user(self, username: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            raise KeyError(f"用户 {username} 不存在")
        return self._serialize_user(row)

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        username = str(payload.get("username") or "").strip()
        wecom_userid = str(payload.get("wecom_userid") or "").strip()
        if not username:
            raise ValueError("用户名必填")
        if len(username) > 40 or re.search(r"\s", username):
            raise ValueError("用户名须在 40 字以内且不含空白字符")
        if len(wecom_userid) > 64:
            raise ValueError("企微 userid 64 字以内")
        with self.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
            if exists:
                raise DuplicateUserError("用户名已存在")
            connection.execute(
                "INSERT INTO users (username, wecom_userid, status, created_at)"
                " VALUES (?, ?, 'active', ?)",
                (username, wecom_userid, utc_now()),
            )
        self._active_users = None
        return self.get_user(username)

    def update_user(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        """改企微映射/停用启用;不做删除(历史任务挂在用户名上,设计 §2.4)。"""
        updates: dict[str, str] = {}
        if "wecom_userid" in payload:
            wecom_userid = str(payload.get("wecom_userid") or "").strip()
            if len(wecom_userid) > 64:
                raise ValueError("企微 userid 64 字以内")
            updates["wecom_userid"] = wecom_userid
        if "status" in payload:
            status = str(payload.get("status") or "").strip()
            if status not in ("active", "disabled"):
                raise ValueError("status 只接受 active/disabled")
            updates["status"] = status
        if not updates:
            raise ValueError("无可更新字段(wecom_userid / status)")
        with self.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE users SET {', '.join(f'{k}=?' for k in updates)} WHERE username=?",
                (*updates.values(), username),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"用户 {username} 不存在")
        self._active_users = None  # 停用/启用下一次请求即生效(设计自查 #2)
        return self.get_user(username)

    # ---- AI 任务看板(tasks;状态机见设计 §3.2,页面只读,仅提交/答复可写)----

    TASK_TYPE_CN = {"code_change": "代码修改", "test_run": "测试执行",
                    "troubleshoot": "问题排查", "env_maint": "环境维护", "other": "其他"}
    TASK_RISK_CN = {"high": "高风险", "mid": "中风险", "low": "低风险"}
    TASK_MACHINE_CN = {"167": "167 平台机", "176": "176 构建机"}

    def _next_task_id(self, connection: sqlite3.Connection) -> str:
        """TASK-YYYYMMDD-NNN 日序号(本地日期;流水产物,用户不可自定义)。"""
        prefix = f"TASK-{datetime.now().strftime('%Y%m%d')}-"
        row = connection.execute(
            "SELECT task_id FROM tasks WHERE task_id LIKE ? ORDER BY task_id DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        seq = int(row["task_id"].rsplit("-", 1)[1]) + 1 if row else 1
        return f"{prefix}{seq:03d}"

    def _task_event(self, connection: sqlite3.Connection, task_id: str,
                    event_type: str, actor: str, detail: str = "") -> None:
        connection.execute(
            "INSERT INTO task_events (task_id, event_type, actor, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (task_id, event_type, actor, detail, utc_now()),
        )

    @classmethod
    def _serialize_task(cls, row: sqlite3.Row, detail: bool = False) -> dict[str, Any]:
        item = {
            "task_id": row["task_id"],
            "title": row["title"],
            "status": row["status"],
            "task_type": row["task_type"],
            "risk": row["risk"],
            "target_machine": row["target_machine"],
            "submitter": row["submitter"],
            "tokens_total": int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0),
            "created_at": row["created_at"],
        }
        if detail:
            item.update({
                "description": row["description"],
                "desired_time": row["desired_time"],
                "paused_phase": row["paused_phase"],
                "analysis_note": row["analysis_note"],
                "branch_name": row["branch_name"],
                "worktree_path": row["worktree_path"],
                "merge_status": row["merge_status"],
                "report_md": row["report_md"],
                "error_message": row["error_message"],
                "tokens_in": int(row["tokens_in"] or 0),
                "tokens_out": int(row["tokens_out"] or 0),
                "updated_at": row["updated_at"],
                "finished_at": row["finished_at"],
            })
        return item

    @staticmethod
    def _serialize_question(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "status": row["status"],
            "asked_at": row["asked_at"],
            "answered_at": row["answered_at"],
        }

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        description = str(payload.get("description") or "").strip()
        submitter = str(payload.get("submitter") or "").strip()
        desired_time = str(payload.get("desired_time") or "").strip()
        if not submitter:
            raise ValueError("submitter 必填(取当前登录人)")
        if not title:
            raise ValueError("标题必填")
        if len(title) > 60:
            raise ValueError("标题 60 字以内")
        if not description:
            raise ValueError("详细描述必填")
        if len(description) > 5000:
            raise ValueError("详细描述 5000 字以内")
        if desired_time:
            try:
                datetime.fromisoformat(desired_time)
            except ValueError as error:
                raise ValueError("期望完成时间格式不合法") from error
        now = utc_now()
        for _ in range(5):  # 日序号并发撞号重试(ThreadingHTTPServer 下两提交同秒)
            task_id = None
            with self.transaction() as connection:
                try:
                    task_id = self._next_task_id(connection)
                    connection.execute(
                        "INSERT INTO tasks (task_id, title, description, submitter,"
                        " desired_time, status, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, 'pending_analysis', ?, ?)",
                        (task_id, title, description, submitter,
                         desired_time or None, now, now),
                    )
                except sqlite3.IntegrityError:
                    continue  # 重取序号再试
                self._task_event(connection, task_id, "submitted", submitter, "提交任务")
            return self.get_task(task_id)
        raise RuntimeError("任务号生成连续冲突,请重试")

    def list_tasks(self, bucket: str = "", status: str = "", q: str = "",
                   page: int = 1, pagesize: int = 10, viewer: str = "") -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            if status not in TASK_STATUSES:
                raise ValueError(f"未知状态 {status}")
            where.append("status=?")
            params.append(status)
        elif bucket == "active":
            where.append(
                f"status IN ({', '.join('?' * len(TASK_ACTIVE_STATUSES))})")
            params.extend(TASK_ACTIVE_STATUSES)
        elif bucket == "done":
            where.append(
                f"status IN ({', '.join('?' * len(TASK_TERMINAL_STATUSES))})")
            params.extend(TASK_TERMINAL_STATUSES)
        elif bucket == "mine":
            # 需我处理=疑问待答且提交人是当前登录人(工作流口径,非权限,设计 §2.5)
            where.append("status='questioning' AND submitter=?")
            params.append(viewer)
        if q:
            where.append("(title LIKE ? OR task_id LIKE ? OR submitter LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        page = max(1, page)
        pagesize = min(max(1, pagesize), 200)
        with self.transaction() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS n FROM tasks{clause}", params).fetchone()["n"]
            rows = connection.execute(
                f"SELECT * FROM tasks{clause} ORDER BY created_at DESC, task_id DESC"
                f" LIMIT ? OFFSET ?",
                (*params, pagesize, (page - 1) * pagesize),
            ).fetchall()
        return {
            "items": [self._serialize_task(row) for row in rows],
            "total": total, "page": page, "pagesize": pagesize,
        }

    def task_stats(self, viewer: str = "") -> dict[str, int]:
        """统计卡四格(全员同口径=全部数据;待我处理按登录人)。"""
        with self.transaction() as connection:
            def count(sql: str, *bind: Any) -> int:
                return int(connection.execute(sql, bind).fetchone()[0])
            return {
                "active": count(
                    f"SELECT COUNT(*) FROM tasks WHERE status IN"
                    f" ({', '.join('?' * len(TASK_ACTIVE_STATUSES))})",
                    *TASK_ACTIVE_STATUSES),
                "mine": count(
                    "SELECT COUNT(*) FROM tasks WHERE status='questioning' AND submitter=?",
                    viewer),
                "done": count("SELECT COUNT(*) FROM tasks WHERE status='done'"),
                "tokens_total": count(
                    "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM tasks"),
            }

    def task_board(self, q: str = "", viewer: str = "") -> dict[str, Any]:
        """竖版看板单请求(v1.2 §A9):五列分组+各列计数+统计卡一次拿全;
        /api/tasks 列表接口保持原样(worker 通道与既有用例都不动)。"""
        where: list[str] = []
        params: list[Any] = []
        if q:
            where.append("(title LIKE ? OR task_id LIKE ? OR submitter LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks{clause} ORDER BY created_at DESC, task_id DESC",
                params,
            ).fetchall()
            count_rows = connection.execute(
                f"SELECT status, COUNT(*) AS n FROM tasks{clause} GROUP BY status",
                params,
            ).fetchall()
        # counts=当前搜索下的全量计数(列头计数与列尾"共 N 条"都用它,§A3/§A8)
        counts = {row["status"]: int(row["n"]) for row in count_rows}
        groups: dict[str, list[dict[str, Any]]] = {s: [] for s in TASK_BOARD_STATUSES}
        for row in rows:  # 已按 created_at 倒序,组内天然有序(§A8)
            column = groups.get(row["status"])
            if column is None or len(column) >= TASK_BOARD_COLUMN_MAX:
                continue
            column.append({
                "task_id": row["task_id"], "title": row["title"], "status": row["status"],
                "task_type": row["task_type"], "risk": row["risk"],
                "target_machine": row["target_machine"], "submitter": row["submitter"],
                "merge_status": row["merge_status"], "created_at": row["created_at"],
                "finished_at": row["finished_at"],
            })
        return {"groups": groups, "counts": counts, "stats": self.task_stats(viewer)}

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"任务 {task_id} 不存在")
            questions = connection.execute(
                "SELECT * FROM task_questions WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
        item = self._serialize_task(row, detail=True)
        item["questions"] = [self._serialize_question(q) for q in questions]
        item["events"] = [dict(e) for e in events]
        return item

    def answer_task(self, task_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            raise ValueError("答复内容必填")
        if len(answer) > 5000:
            raise ValueError("答复内容 5000 字以内")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"任务 {task_id} 不存在")
            if row["status"] != "questioning":
                raise TaskStateError(f"任务不在疑问待答状态(当前 {row['status']})")
            open_question = connection.execute(
                "SELECT id FROM task_questions WHERE task_id=? AND status='open'"
                " ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
            if open_question is None:
                raise TaskStateError("没有待答复的疑问")
            resume = row["paused_phase"] or "pending_analysis"
            connection.execute(
                "UPDATE task_questions SET answer=?, status='answered', answered_at=?"
                " WHERE id=?", (answer, utc_now(), open_question["id"]))
            connection.execute(
                "UPDATE tasks SET status=?, paused_phase=NULL, updated_at=? WHERE task_id=?",
                (resume, utc_now(), task_id))
            resume_cn = "待分析" if resume == "pending_analysis" else "执行中"
            self._task_event(connection, task_id, "answered", actor,
                             f"已答复,任务回到{resume_cn}")
        return self.get_task(task_id)

    def task_usage(self) -> list[dict[str, Any]]:
        """按人用量(全员可见;按 token 合计倒序;无任务账号也列出)。"""
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT u.username,
                       COUNT(t.task_id) AS submitted,
                       COALESCE(SUM(CASE WHEN t.status IN
                           ('pending_analysis','questioning','running')
                           THEN 1 ELSE 0 END), 0) AS running,
                       COALESCE(SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END), 0) AS done,
                       COALESCE(SUM(CASE WHEN t.status='failed' THEN 1 ELSE 0 END), 0) AS failed,
                       COALESCE(SUM(t.tokens_in + t.tokens_out), 0) AS tokens_total
                FROM users u LEFT JOIN tasks t ON t.submitter = u.username
                GROUP BY u.username
                ORDER BY tokens_total DESC, submitted DESC, u.username
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- dispatch worker 内部通道(设计 §9:worker 内部通道由后端自定)----

    def dispatch_queue(self) -> dict[str, Any]:
        """活跃任务全量(含问答历史),worker 据此决策:分析/答复后续跑/盯执行。"""
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE status IN"
                f" ({', '.join('?' * len(TASK_ACTIVE_STATUSES))}) ORDER BY created_at",
                TASK_ACTIVE_STATUSES,
            ).fetchall()
            items = []
            for row in rows:
                item = self._serialize_task(row, detail=True)
                item["questions"] = [self._serialize_question(q) for q in connection.execute(
                    "SELECT * FROM task_questions WHERE task_id=? ORDER BY id",
                    (row["task_id"],)).fetchall()]
                # 终态企微群消息 @发布人用(用户台账只有 worker 内部通道可见)
                wecom = connection.execute("SELECT wecom_userid FROM users WHERE username=?",
                                           (row["submitter"],)).fetchone()
                item["submitter_wecom_userid"] = wecom["wecom_userid"] if wecom else None
                items.append(item)
        return {"items": items}

    def dispatch_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        """worker 回写:按 action 走状态机(设计 §3.2)。非法迁移抛 TaskStateError→409。"""
        action = str(payload.get("action") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id 必填")
        try:
            tokens_in = max(0, int(payload.get("tokens_in") or 0))
            tokens_out = max(0, int(payload.get("tokens_out") or 0))
        except (TypeError, ValueError) as error:
            raise ValueError("tokens_in/tokens_out 须为非负整数") from error
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"任务 {task_id} 不存在")
            status = row["status"]
            token_sql = ", tokens_in=tokens_in+?, tokens_out=tokens_out+?"
            token_params: tuple[Any, ...] = ()
            if tokens_in or tokens_out:
                token_params = (tokens_in, tokens_out)

            def run_update(sets: str, params: tuple[Any, ...]) -> None:
                sql = f"UPDATE tasks SET {sets}{token_sql if token_params else ''}, updated_at=? WHERE task_id=?"
                connection.execute(sql, (*params, *token_params, now, task_id))

            if action == "analyze":
                if status != "pending_analysis":
                    raise TaskStateError(f"任务状态为 {status},不能写入分析结论")
                task_type = str(payload.get("task_type") or "").strip()
                risk = str(payload.get("risk") or "").strip()
                target_machine = str(payload.get("target_machine") or "").strip()
                if task_type not in TASK_TYPES:
                    raise ValueError(f"task_type 只接受 {'/'.join(TASK_TYPES)}")
                if risk not in TASK_RISKS:
                    raise ValueError(f"risk 只接受 {'/'.join(TASK_RISKS)}")
                if target_machine and target_machine not in TARGET_MACHINES:
                    raise ValueError(f"target_machine 只接受 {'/'.join(TARGET_MACHINES)}")
                analysis_note = str(payload.get("analysis_note") or "").strip()
                run_update(
                    "task_type=?, risk=?, target_machine=?, analysis_note=?",
                    (task_type, risk, target_machine or None, analysis_note or None))
                self._task_event(connection, task_id, "analyzed", "大领导",
                                 f"分析完成:{self.TASK_TYPE_CN[task_type]}/{self.TASK_RISK_CN[risk]}")
            elif action == "start":
                if status != "pending_analysis":
                    raise TaskStateError(f"任务状态为 {status},不能开始执行")
                merge_status = "pending" if row["task_type"] == "code_change" else None
                run_update("status=?, paused_phase=NULL, merge_status=?", ("running", merge_status))
                machine_cn = self.TASK_MACHINE_CN.get(str(row["target_machine"] or ""), "")
                self._task_event(connection, task_id, "started", "小领导",
                                 f"开始执行({machine_cn})" if machine_cn else "开始执行")
            elif action == "question":
                if status not in ("pending_analysis", "running"):
                    raise TaskStateError(f"任务状态为 {status},不能提出疑问")
                question = str(payload.get("question") or "").strip()
                if not question:
                    raise ValueError("question 必填")
                if len(question) > 2000:
                    raise ValueError("question 2000 字以内")
                connection.execute(
                    "UPDATE tasks SET status='questioning', paused_phase=?, updated_at=?"
                    " WHERE task_id=?", (status, now, task_id))
                connection.execute(
                    "INSERT INTO task_questions (task_id, question, status, asked_at)"
                    " VALUES (?, ?, 'open', ?)", (task_id, question, now))
                self._task_event(connection, task_id, "question", "大领导", question)
            elif action == "progress":
                detail = str(payload.get("detail") or "").strip()
                if not detail:
                    raise ValueError("detail 必填")
                run_update("status=status", ())  # 不改状态,只累计 token + 事件
                self._task_event(connection, task_id, "progress",
                                 str(payload.get("actor") or "小领导"), detail)
            elif action == "merge":
                event_type = str(payload.get("event") or "").strip()
                if event_type not in ("merge_conflict", "merge_retry"):
                    raise ValueError("event 只接受 merge_conflict/merge_retry")
                if status != "running":
                    raise TaskStateError(f"任务状态为 {status},不能记合并事件")
                detail = str(payload.get("detail") or "").strip() or (
                    "合并冲突,AI 处理中" if event_type == "merge_conflict" else "AI 重试合并")
                run_update("merge_status=?", ("conflict",))
                self._task_event(connection, task_id, event_type, "小领导", detail)
            elif action == "finish":
                if status != "running":
                    raise TaskStateError(f"任务状态为 {status},不能收尾")
                ok = bool(payload.get("ok"))
                report_md = str(payload.get("report_md") or "").strip()
                error_message = str(payload.get("error_message") or "").strip()
                branch_name = str(payload.get("branch_name") or "").strip() or None
                worktree_path = str(payload.get("worktree_path") or "").strip() or None
                merge_status = str(payload.get("merge_status") or "").strip() or None
                if merge_status and merge_status not in ("merged", "pending", "conflict", "abandoned"):
                    raise ValueError("merge_status 非法")
                if ok:
                    run_update(
                        "status=?, finished_at=?, report_md=?, branch_name=?,"
                        " worktree_path=?, merge_status=?, error_message=NULL",
                        ("done", now, report_md or None, branch_name, worktree_path,
                         merge_status or ("merged" if row["task_type"] == "code_change" else None)))
                    self._task_event(connection, task_id, "finished", "大领导", "执行完成")
                else:
                    if not error_message:
                        raise ValueError("失败收尾须带 error_message")
                    if merge_status == "abandoned":
                        self._task_event(connection, task_id, "merge_abandon",
                                         "小领导", "合并冲突未解决,AI 放弃合并")
                    run_update(
                        "status=?, finished_at=?, report_md=?, branch_name=?,"
                        " worktree_path=?, merge_status=?, error_message=?",
                        ("failed", now, report_md or None, branch_name, worktree_path,
                         merge_status, error_message))
                    self._task_event(connection, task_id, "finished", "大领导",
                                     f"执行失败:{error_message[:80]}")
            elif action == "note":
                # 只追加事件不改状态(终态任务也可记):企微通知失败等旁路日志用
                detail = str(payload.get("detail") or "").strip()
                if not detail:
                    raise ValueError("detail 必填")
                self._task_event(connection, task_id,
                                 str(payload.get("event_type") or "note"),
                                 str(payload.get("actor") or "系统"), detail)
            else:
                raise ValueError(f"未知 action {action}")
        return self.get_task(task_id)

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("run_id") or "").strip()
        platform = str(payload.get("platform") or "").strip().lower()
        if not run_id:
            raise ValueError("run_id is required")
        if platform not in VALID_PLATFORMS:
            raise ValueError(f"platform must be one of {sorted(VALID_PLATFORMS)}")
        started_at = str(payload.get("started_at") or utc_now())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, platform, status, app_version, device, jenkins_build,
                    apk_id, started_at, metadata_json
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    platform=excluded.platform,
                    app_version=excluded.app_version,
                    device=excluded.device,
                    jenkins_build=excluded.jenkins_build,
                    apk_id=excluded.apk_id,
                    metadata_json=excluded.metadata_json
                """,
                (
                    run_id,
                    platform,
                    str(payload.get("app_version") or ""),
                    str(payload.get("device") or ""),
                    str(payload.get("jenkins_build") or ""),
                    str(payload.get("apk_id") or ""),
                    started_at,
                    json_object(payload.get("metadata")),
                ),
            )
        return self.get_run(run_id)

    def upsert_case(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("case_id is required")
        status = normalize_status(payload.get("status"))
        started_at = str(payload.get("started_at") or utc_now())
        with self.transaction() as connection:
            run = connection.execute(
                "SELECT platform FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            connection.execute(
                """
                INSERT INTO cases (
                    run_id, case_id, name, platform, status, started_at, ended_at,
                    duration_seconds, log, error, artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, case_id) DO UPDATE SET
                    name=excluded.name,
                    platform=excluded.platform,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    duration_seconds=excluded.duration_seconds,
                    log=excluded.log,
                    error=excluded.error,
                    artifacts_json=excluded.artifacts_json
                """,
                (
                    run_id,
                    case_id,
                    str(payload.get("name") or case_id),
                    str(payload.get("platform") or run["platform"]),
                    status,
                    started_at,
                    payload.get("ended_at"),
                    float(payload.get("duration_seconds") or 0),
                    str(payload.get("log") or ""),
                    str(payload.get("error") or ""),
                    json_object(payload.get("artifacts")),
                ),
            )
        return self.get_case(run_id, case_id)

    def complete_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ended_at = str(payload.get("ended_at") or utc_now())
        with self.transaction() as connection:
            run = connection.execute(
                "SELECT started_at FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM cases WHERE run_id = ? GROUP BY status",
                (run_id,),
            ).fetchall()
            counts = {row["status"]: int(row["count"]) for row in rows}
            total = sum(counts.values())
            passed = counts.get("passed", 0)
            failed = counts.get("failed", 0)
            skipped = counts.get("skipped", 0)
            errors = counts.get("error", 0)
            # 取消是独立终态(M-B2):不走别名归一,也不做下方 passed→failed 覆盖——
            # 取消≠执行结论,取消前已上报的用例计数如实保留,只是会话结论记 cancelled
            if str(payload.get("status") or "").strip().lower() in {"cancelled", "cancel"}:
                status = "cancelled"
            else:
                status = normalize_status(payload.get("status"))
                if status == "passed" and (failed or errors):
                    status = "failed"
            if status not in {"passed", "failed", "skipped", "error", "cancelled"}:
                status = "failed" if failed or errors else "passed"
            duration = float(payload.get("duration_seconds") or 0)
            if duration <= 0:
                duration = self.duration_between(run["started_at"], ended_at)
            connection.execute(
                """
                UPDATE runs SET status=?, ended_at=?, duration_seconds=?, total_cases=?,
                    passed_cases=?, failed_cases=?, skipped_cases=?, error_cases=?
                WHERE run_id=?
                """,
                (status, ended_at, duration, total, passed, failed, skipped, errors, run_id),
            )
        return self.get_run(run_id)

    def _sweep_orphan_runs(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        """流水线会话孤儿收尾(幂等):run 卡 running 而其 job 已终态 → 代管收尾。

        背景:执行 agent 崩溃或任务被 _reap_timeout_jobs 超时抢回时,关联 run 收不到
        complete 会永远卡 running。对齐 _stale_cutoff/_reap_timeout_jobs 的惰性模式,
        由 runs 查询路径触发。关联不依赖 job 侧改动:执行 agent 的 run_id 约定为
        pipe-{job_id},前缀反查即可。收尾口径:
        - job succeeded 且有用例:按 cases 聚合定状态(与 complete_run 一致)
        - job cancelled(M-B2):定 cancelled(取消≠执行结论,对齐平台强制取消口径)
        - job 失败/env_blocked/被抢回,或 succeeded 却无用例上报:定 error
        - ended_at 取 job.finished_at(真实终态时刻);reaped 等关联信息写入 metadata_json
        UPDATE 带 status='running' 条件,并发/重复执行均无副作用。
        """
        finished = ",".join("?" * len(PIPELINE_JOB_FINISHED_STATUSES))
        orphans = connection.execute(
            f"""SELECT r.run_id, r.started_at, r.metadata_json,
                       j.status AS job_status, j.finished_at AS job_finished_at,
                       j.result_json AS job_result_json
                FROM runs r JOIN pipeline_jobs j
                  ON j.job_id = substr(r.run_id, {len(_PIPELINE_RUN_ID_PREFIX) + 1})
                WHERE r.status='running' AND r.run_id LIKE '{_PIPELINE_RUN_ID_PREFIX}%'
                  AND j.status IN ({finished})""",
            PIPELINE_JOB_FINISHED_STATUSES,
        ).fetchall()
        swept: list[dict[str, Any]] = []
        for row in orphans:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM cases WHERE run_id = ? GROUP BY status",
                (row["run_id"],),
            ).fetchall()
            counts = {item["status"]: int(item["count"]) for item in rows}
            total = sum(counts.values())
            passed = counts.get("passed", 0)
            failed = counts.get("failed", 0)
            skipped = counts.get("skipped", 0)
            errors = counts.get("error", 0)
            job_result = json.loads(row["job_result_json"] or "{}")
            reaped = bool(job_result.get("reaped"))
            if row["job_status"] == "succeeded" and total:
                status = "failed" if (failed or errors) else "passed"
                reason = "执行 agent 失联,pipeline job 已成功,按已有用例聚合收尾"
            elif row["job_status"] == "succeeded":
                status = "error"
                reason = "执行 agent 失联,pipeline job 已成功但无用例上报,会话由平台代管收尾"
            elif row["job_status"] == "cancelled":
                # 任务已取消而 run 收不到 agent 的 complete(M-B2):对齐"取消≠执行结论",
                # 会话记 cancelled(取消前已上报用例计数如实保留),不误收 error 拉脏统计
                status = "cancelled"
                if job_result.get("forced_cancel"):
                    reason = "执行 agent 失联,pipeline job 取消超时已被平台强制取消,会话由平台代管收尾"
                else:
                    reason = "执行 agent 失联,pipeline job 已取消,会话由平台代管收尾"
            else:
                status = "error"
                if reaped:
                    reason = "执行 agent 失联,pipeline job 已被平台超时抢回(reaped),会话由平台代管收尾"
                else:
                    reason = f"执行 agent 失联,pipeline job 已终态({row['job_status']}),会话由平台代管收尾"
            ended_at = str(row["job_finished_at"] or utc_now())
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["orphan_swept"] = {
                "job_id": row["run_id"].removeprefix(_PIPELINE_RUN_ID_PREFIX),
                "job_status": row["job_status"],
                "job_finished_at": row["job_finished_at"],
                "reaped": reaped,
                "reason": reason,
                "swept_at": utc_now(),
            }
            updated = connection.execute(
                """
                UPDATE runs SET status=?, ended_at=?, duration_seconds=?, total_cases=?,
                    passed_cases=?, failed_cases=?, skipped_cases=?, error_cases=?,
                    metadata_json=?
                WHERE run_id=? AND status='running'
                """,
                (status, ended_at, self.duration_between(row["started_at"], ended_at),
                 total, passed, failed, skipped, errors,
                 json.dumps(metadata, ensure_ascii=False), row["run_id"]),
            ).rowcount
            if updated:
                _sweep_log(f"run={row['run_id']} 会话孤儿收尾 → {status}"
                           f"(job_status={row['job_status']}, reaped={reaped})")
                swept.append({"run_id": row["run_id"], "status": status,
                              "reaped": reaped, "reason": reason})
        return swept

    def sync_run_zentao(self, run_id: str) -> dict[str, Any]:
        """把执行会话结果回写禅道测试单(M6;仅页面按钮人工触发,禁止任何自动回写)。

        zentao_task_id 为空 → create_testtask 建单并回存单号;非空 → update_testtask
        更新 desc(幂等:重复点击=更新同一单)。REST v1 无用例级回写端点,结果以
        汇总 HTML 写进 desc。project/product 从环境读(部署机 .env.local),未配置抛
        ValueError(400);running 未完成抛 RunNotFinalizedError(409);cancelled 不回写
        (2026-09-22 拍板:取消≠执行结论,会话无结论可写,要结果就重跑)。
        """
        run = self.get_run(run_id)
        if run["status"] == "running":
            raise RunNotFinalizedError(f"会话 {run_id} 尚未结束,完成后才能回写禅道")
        if run["status"] == "cancelled":
            raise RunNotFinalizedError(f"已取消的执行会话不回写禅道(会话 {run_id} 被取消,无执行结论)")
        project_id = str(os.environ.get("ZENTAO_PROJECT_ID") or "").strip()
        product_id = str(os.environ.get("ZENTAO_PRODUCT_ID") or "").strip()
        if not project_id or not product_id:
            raise ValueError("需在平台机 .167 .env.local 配置 ZENTAO_PROJECT_ID 与 ZENTAO_PRODUCT_ID")
        cases = run.get("cases") or []
        source = str((run.get("metadata") or {}).get("source") or "-")
        status_cn = {"passed": "通过", "failed": "失败", "error": "错误", "skipped": "跳过", "cancelled": "已取消"}
        lines = [
            f"<p><strong>执行会话:</strong>{html.escape(str(run_id))}"
            f"(来源:{html.escape(source)},{html.escape(str(run.get('platform') or ''))} 平台)</p>",
            f"<p><strong>结果汇总:</strong>共 {int(run.get('total_cases') or 0)} 条,"
            f"通过 {int(run.get('passed_cases') or 0)},失败 {int(run.get('failed_cases') or 0)},"
            f"错误 {int(run.get('error_cases') or 0)},跳过 {int(run.get('skipped_cases') or 0)};"
            f"会话状态 {html.escape(status_cn.get(str(run.get('status')), str(run.get('status'))))}</p>",
            f"<p><strong>环境:</strong>包体 {html.escape(str(run.get('apk_id') or '-'))},"
            f"设备 {html.escape(str(run.get('device') or '-'))},"
            f"构建 {html.escape(str(run.get('jenkins_build') or '-'))}</p>",
        ]
        if cases:
            lines.append("<p><strong>用例清单:</strong></p><ul>")
            max_list = 50  # nightly 上百条时 desc 过长,截断并指向平台页
            for case in cases[:max_list]:
                st = str(case.get("status") or "")
                lines.append(f"<li>[{html.escape(status_cn.get(st, st))}] "
                             f"{html.escape(str(case.get('name') or ''))}</li>")
            if len(cases) > max_list:
                lines.append(f"<li>…等共 {len(cases)} 条(清单过长截断,明细见平台页)</li>")
            lines.append("</ul>")
        desc = "".join(lines)

        from dashboard.zentao_client import ZentaoError, create_testtask, update_testtask
        task_id = str(run.get("zentao_task_id") or "").strip()
        if task_id:
            update_testtask(int(task_id), desc)
            return {"zentao_task_id": int(task_id), "created": False}
        data = create_testtask(int(project_id), int(product_id),
                               f"[Swan] 自动化执行会话 {run_id}",
                               str(run.get("jenkins_build") or "trunk"), desc)
        new_id = str(data.get("id") or "").strip()
        if not new_id:
            raise ZentaoError(f"禅道建测试单响应缺少 id: {json.dumps(data, ensure_ascii=False)[:200]}")
        with self.transaction() as connection:
            connection.execute("UPDATE runs SET zentao_task_id=? WHERE run_id=?", (new_id, run_id))
        return {"zentao_task_id": int(new_id), "created": True}

    # ==================== 设备池 ====================

    def register_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise ValueError("device_id is required")
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM devices WHERE device_id=?", (device_id,)
            ).fetchone()
            # 心跳/注册刷新在线时间;busy/disabled 的设备注册(执行机自报)不覆盖其状态
            if row is None:
                connection.execute(
                    """
                    INSERT INTO devices (
                        device_id, name, platform, model, status, last_heartbeat,
                        registered_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        str(payload.get("name") or ""),
                        str(payload.get("platform") or "flutter"),
                        str(payload.get("model") or ""),
                        str(payload.get("status") or "online_idle"),
                        now, now,
                        json_object(payload.get("metadata")),
                    ),
                )
            else:
                keep_status = row["status"] if row["status"] in {"busy", "disabled"} else "online_idle"
                if keep_status == row["status"]:
                    # busy/disabled 的设备注册(执行机自报)不覆盖其状态与 run 占用
                    connection.execute(
                        """
                        UPDATE devices SET name=?, platform=?, model=?, status=?,
                            last_heartbeat=?, metadata_json=?
                        WHERE device_id=?
                        """,
                        (
                            str(payload.get("name") or ""),
                            str(payload.get("platform") or "flutter"),
                            str(payload.get("model") or ""),
                            keep_status,
                            now,
                            json_object(payload.get("metadata")),
                            device_id,
                        ),
                    )
                else:
                    # 空闲刷新或 offline 复活:执行机 adb 在线才会注册,即在线的铁证;
                    # 复活时清 run 占用残留,避免 release 归属校验错乱
                    connection.execute(
                        """
                        UPDATE devices SET name=?, platform=?, model=?, status=?,
                            current_run='', busy_since=NULL,
                            last_heartbeat=?, metadata_json=?
                        WHERE device_id=?
                        """,
                        (
                            str(payload.get("name") or ""),
                            str(payload.get("platform") or "flutter"),
                            str(payload.get("model") or ""),
                            keep_status,
                            now,
                            json_object(payload.get("metadata")),
                            device_id,
                        ),
                    )
        return self.get_device(device_id)

    def get_device(self, device_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"device not found: {device_id}")
        return self._serialize_device(row)

    @staticmethod
    def _serialize_device(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item

    def list_devices(self) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
        return [self._serialize_device(row) for row in rows]

    def acquire_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        """领取一台空闲设备:平台匹配 + 最久未用优先(平均分配)。"""
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        platform = str(payload.get("platform") or "flutter").strip().lower()
        prefer = [str(s) for s in (payload.get("prefer") or [])]
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM devices WHERE status='online_idle' AND platform=?",
                (platform,),
            ).fetchall()
            if not rows:
                # 自愈:busy 超 4 小时且心跳停滞的设备视为 stale(执行机崩溃/拔线),回收重用
                connection.execute(
                    """
                    UPDATE devices SET status='online_idle', current_run='', busy_since=NULL
                    WHERE status='busy' AND platform=? AND busy_since < ?
                    """,
                    (platform, _stale_cutoff()),
                )
                rows = connection.execute(
                    "SELECT * FROM devices WHERE status='online_idle' AND platform=?",
                    (platform,),
                ).fetchall()
            if not rows:
                raise LookupError(f"no idle device for platform={platform}")

            def _rank(row: sqlite3.Row) -> tuple:
                return (0 if row["device_id"] in prefer else 1,
                        row["last_assigned_at"] or "")

            chosen = sorted(rows, key=_rank)[0]
            connection.execute(
                """
                UPDATE devices SET status='busy', current_run=?, busy_since=?,
                    last_assigned_at=?, last_heartbeat=?
                WHERE device_id=? AND status='online_idle'
                """,
                (run_id, now, now, now, chosen["device_id"]),
            )
        return self.get_device(chosen["device_id"])

    def release_device(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("run_id") or "")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT current_run FROM devices WHERE device_id=?", (device_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            if row["current_run"] and run_id and row["current_run"] != run_id:
                raise ValueError(f"device busy with another run: {row['current_run']}")
            connection.execute(
                """
                UPDATE devices SET status='online_idle', current_run='', busy_since=NULL,
                    last_heartbeat=?
                WHERE device_id=?
                """,
                (utc_now(), device_id),
            )
        return self.get_device(device_id)

    def set_device_status(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip()
        if status not in {"online_idle", "offline", "disabled"}:
            raise ValueError("status must be online_idle, offline or disabled")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM devices WHERE device_id=?", (device_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            if row["status"] == "busy" and status not in {"disabled", "offline"}:
                raise ValueError("device is busy, release it first")
            if row["status"] == "busy" and status == "offline":
                # 执行中失联:置 offline 移出可领队列,同时解除 run 占用(重插后由 register 复活)
                connection.execute(
                    """UPDATE devices SET status=?, current_run='', busy_since=NULL,
                       last_heartbeat=? WHERE device_id=?""",
                    (status, utc_now(), device_id),
                )
            else:
                connection.execute(
                    "UPDATE devices SET status=?, last_heartbeat=? WHERE device_id=?",
                    (status, utc_now(), device_id),
                )
        return self.get_device(device_id)

    # ---------- 流水线任务(P4):触发/认领/收尾 ----------

    def create_pipeline_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        trigger_source = str(payload.get("trigger_source") or "manual").strip()
        platform = str(payload.get("platform") or "flutter").strip().lower()
        if platform == "flutter":
            # 旧称归一化(2026-10-15):对账补行等旧调用点的 flutter 统一落 flutter-android
            platform = "flutter-android"
        if platform not in VALID_PLATFORMS:
            raise ValueError(f"platform must be one of {sorted(VALID_PLATFORMS)}")
        # 端透传(2026-10-15):''=不限端(执行端不过滤,旧行为);给了值必须是合法枚举
        device_os = str(payload.get("device_os") or "").strip().lower()
        if device_os and device_os not in SUITE_OS_TYPES:
            raise ValueError(f"device_os 必须是 {'/'.join(SUITE_OS_TYPES)} 之一或留空")
        job_id = str(payload.get("job_id") or "").strip() or (
            f"job-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-"
            f"{uuid.uuid4().hex[:6]}"
        )
        # created_at 可选覆盖(2026-09-24):对账补行用 Jenkins 构建真实开始时间,
        # 常规触发不传,仍取平台落行时刻
        created_at = str(payload.get("created_at") or "").strip() or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO pipeline_jobs (
                    job_id, trigger_source, branch, platform, case_filter, apk_id,
                    device_os, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    job_id, trigger_source,
                    str(payload.get("branch") or "").strip(),
                    platform,
                    str(payload.get("case_filter") or "").strip(),
                    str(payload.get("apk_id") or "").strip() or None,
                    device_os,
                    created_at,
                ),
            )
        return self.get_pipeline_job(job_id)

    def get_pipeline_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"pipeline job not found: {job_id}")
        return self._serialize_pipeline_job(row)

    def list_pipeline_jobs(
        self, status: str = "", limit: int = 30
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if status:
                rows = connection.execute(
                    """SELECT * FROM pipeline_jobs WHERE status=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM pipeline_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._serialize_pipeline_job(row) for row in rows]

    @staticmethod
    def _serialize_pipeline_job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item

    def bound_jenkins_builds(self, jenkins_job: str) -> set[int]:
        """某 Jenkins 任务已占用构建号的全量集合(对账占用判定专用)。

        不能用 list_pipeline_jobs 的"最近 N 行"窗口判占用:窗口按 created_at
        排序,而对账补录行的 created_at 落的是构建开始时间,永远沉在窗口外,
        占用判定会系统性漏判、同一构建被反复补行(2026-09-28 线上 #1 积 9 行)。
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT jenkins_build FROM pipeline_jobs WHERE jenkins_job=?",
                (jenkins_job,),
            ).fetchall()
        numbers: set[int] = set()
        for row in rows:
            try:
                numbers.add(int(row["jenkins_build"]))
            except (TypeError, ValueError):
                continue
        return numbers

    def dedupe_pipeline_build_rows(self, jenkins_job: str) -> int:
        """同一 Jenkins 任务同一构建号只保留一行,返回删除行数(对账自愈)。

        治旧版对账窗口漏判留下的重复行:同组保留 created_at 最早的一行——
        原生触发行先建行后派发,创建必然不晚于构建开始,天然优先于补录行
        (补录行 created_at=构建开始时刻);同秒并列再比 job_id,先到先得。
        只动 jenkins_job 命中的行,测试执行行(swan-autotest 等撞号任务)不受影响。
        """
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT job_id, jenkins_build FROM pipeline_jobs
                   WHERE jenkins_job=? AND jenkins_build IS NOT NULL
                   ORDER BY created_at, job_id""",
                (jenkins_job,),
            ).fetchall()
            seen: dict[int, str] = {}
            doomed: list[str] = []
            for row in rows:
                try:
                    number = int(row["jenkins_build"])
                except (TypeError, ValueError):
                    continue
                if seen.setdefault(number, row["job_id"]) != row["job_id"]:
                    doomed.append(row["job_id"])
            for job_id in doomed:
                connection.execute("DELETE FROM pipeline_jobs WHERE job_id=?", (job_id,))
        return len(doomed)

    def claim_pipeline_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行 agent 认领任务(原子操作,防多机抢跑)。

        两种通道:
          - 定向(Jenkins 构建):payload 带 job_id,精确认领该任务;任务不存在/
            已被认领/非 pending → LookupError(handler 转 404,agent 优雅退出)。
          - 轮询(本地常驻 agent):领最早 pending,跳过已派发 Jenkins 的任务
            (jenkins_job 非空)——那些由 Jenkins 构建里的 agent --job-id 领,
            否则本地 agent 抢在 Jenkins 前执行会让构建空跑。
        """
        claimed_by = str(payload.get("agent") or "").strip() or "unknown-agent"
        wanted_job_id = str(payload.get("job_id") or "").strip()
        now = utc_now()
        # 惰性回收放独立事务:无 pending 时 claim 抛 LookupError 会回滚当前事务,抢回不能跟着陪葬
        with self.transaction() as connection:
            _reap_timeout_jobs(connection)
            _force_cancel_stale(connection)
        with self.transaction() as connection:
            if wanted_job_id:
                row = connection.execute(
                    """SELECT job_id FROM pipeline_jobs
                       WHERE job_id=? AND status='pending'""",
                    (wanted_job_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"pipeline job not claimable: {wanted_job_id}")
            else:
                row = connection.execute(
                    """SELECT job_id FROM pipeline_jobs
                       WHERE status='pending' AND (jenkins_job='' OR jenkins_job IS NULL)
                       AND trigger_source NOT IN ('build', 'webhook')
                       ORDER BY created_at LIMIT 1"""
                ).fetchone()
                if row is None:
                    raise LookupError("no pending pipeline job")
            job_id = row["job_id"]
            connection.execute(
                """UPDATE pipeline_jobs SET status='running', claimed_by=?,
                   claimed_at=?, attempts=attempts+1
                   WHERE job_id=? AND status='pending'""",
                (claimed_by, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def finish_pipeline_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip()
        valid = set(PIPELINE_JOB_FINISHED_STATUSES)
        if status not in valid:
            raise ValueError(f"status must be one of {sorted(valid)}")
        # Jenkins 构建内运行的 agent 会随 finish 回报 JOB_NAME/BUILD_NUMBER:派发时
        # 短轮询没拿到构建号的任务,finish 后据此回填,流水线列表才有精确日志入口;
        # 只补空值,不覆盖派发时已记录的字段
        backfill_job = str(payload.get("jenkins_job") or "").strip()
        try:
            backfill_build = int(str(payload.get("jenkins_build") or "").strip())
        except ValueError:
            backfill_build = None
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM pipeline_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"pipeline job not found: {job_id}")
            # 状态条件更新(先到先得):仅 running/cancelling 可落终态——
            # 取消 vs 正常完成竞态(如平台已强制取消,agent 迟到的 finish)后到方无副作用
            updated = connection.execute(
                """UPDATE pipeline_jobs SET status=?, finished_at=?, result_json=?,
                   jenkins_job=CASE WHEN IFNULL(jenkins_job,'')='' THEN ? ELSE jenkins_job END,
                   jenkins_build=CASE WHEN jenkins_build IS NULL THEN ? ELSE jenkins_build END
                   WHERE job_id=? AND status IN ('running', 'cancelling')""",
                (status, utc_now(),
                 json.dumps(payload.get("result") or {}, ensure_ascii=False),
                 backfill_job, backfill_build, job_id),
            ).rowcount
            # cancelled 收尾:pytest 被看门狗杀掉时 conftest 的 sessionfinish 不会执行,
            # 设备占用残留——由平台按 run 归属统一释放(SIGTERM/SIGKILL 下均如此)
            if updated and status == "cancelled":
                released = connection.execute(
                    """UPDATE devices SET status='online_idle', current_run='', busy_since=NULL,
                       last_heartbeat=? WHERE status='busy' AND current_run=?""",
                    (utc_now(), f"pipe-{job_id}"),
                ).rowcount
                if released:
                    _reap_log(f"job={job_id} 已取消,释放设备 {released} 台")
        return self.get_pipeline_job(job_id)

    def finish_build_pipeline_job(self, job_id: str, status: str,
                                  result: dict[str, Any],
                                  finished_at: str = "") -> dict[str, Any]:
        """打包型流水线任务终态回写:pending 直接落终态。

        finished_at 可选(2026-09-24):对账/回查闭环传 Jenkins 构建真实结束
        时刻,缺省仍取平台落库时刻。

        finish_pipeline_job 只收 running/cancelling(agent 状态机),build/webhook
        任务没有认领环节,状态机独立:pending/running 且 trigger_source IN
        ('build','webhook') 可落,先到先得(running 2026-09-24 起,对账补的
        "运行中"态同样要能落终态)。
        """
        if status not in set(PIPELINE_JOB_FINISHED_STATUSES):
            raise ValueError(f"status must be one of {sorted(PIPELINE_JOB_FINISHED_STATUSES)}")
        with self.transaction() as connection:
            updated = connection.execute(
                """UPDATE pipeline_jobs SET status=?, finished_at=?, result_json=?
                   WHERE job_id=? AND status IN ('pending','running')
                     AND trigger_source IN ('build','webhook')""",
                (status, finished_at or utc_now(), json.dumps(result, ensure_ascii=False), job_id),
            ).rowcount
            if not updated:
                raise KeyError(f"build pipeline job not pending/running: {job_id}")
        return self.get_pipeline_job(job_id)

    def fix_reconciled_job_times(self, job_id: str, created_at: str,
                                 finished_at: str = "") -> dict[str, Any]:
        """对账补行存量修复(2026-09-24):首版补行把 created_at/finished_at 都
        落成同一同步时刻,按 Jenkins 构建真实起止回填。条件更新只命中
        created_at=finished_at 的坏行,幂等;finished_at 空串保持原值——
        同秒闭环的正常行不会被抹成 NULL。"""
        with self.transaction() as connection:
            if finished_at:
                connection.execute(
                    """UPDATE pipeline_jobs SET created_at=?, finished_at=?
                       WHERE job_id=? AND created_at=finished_at""",
                    (created_at, finished_at, job_id),
                )
            else:
                connection.execute(
                    "UPDATE pipeline_jobs SET created_at=? WHERE job_id=? AND created_at=finished_at",
                    (created_at, job_id),
                )
        return self.get_pipeline_job(job_id)

    def cancel_pipeline_job(self, job_id: str) -> dict[str, Any]:
        """用户取消流水线任务(状态机见 docs/流水线任务取消功能计划书.md 第四节)。

        pending → 直接 cancelled(claim 只领 pending,天然不再下发);
        running → 置 cancel_requested=1 + status='cancelling'(agent watchdog 轮询
          旗标杀 pytest 进程组后回报 cancelled;agent 离线由 _force_cancel_stale 兜底);
        cancelling → 幂等返回现状;其余终态 → ValueError(handler 转 409)。
        全部 UPDATE 带状态条件:取消 vs finish/强制兜底竞态先到先得。
        """
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status, trigger_source FROM pipeline_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"pipeline job not found: {job_id}")
            current = row["status"]
            if (current == "running"
                    and str(row["trigger_source"] or "") in ("build", "webhook")):
                # 打包行运行中 = Jenkins 构建进行中,平台无中止通道,置 cancelling
                # 会永远卡住(没有 agent 回报 cancelled)→ 拒绝(2026-09-24)
                raise ValueError("打包任务运行中不支持取消,请等 Jenkins 出终态")
            if current == "pending":
                connection.execute(
                    """UPDATE pipeline_jobs SET status='cancelled', finished_at=?, result_json=?
                       WHERE job_id=? AND status='pending'""",
                    (now, json.dumps({"cancel_requested": True,
                                      "reason": "pending 状态取消,未下发执行"},
                                     ensure_ascii=False), job_id),
                )
            elif current == "running":
                connection.execute(
                    """UPDATE pipeline_jobs SET status='cancelling', cancel_requested=1,
                       cancel_requested_at=? WHERE job_id=? AND status='running'""",
                    (now, job_id),
                )
            elif current != "cancelling":
                raise ValueError(f"任务状态为 {current},仅 pending/running 可取消")
        return self.get_pipeline_job(job_id)

    def mark_jenkins_dispatch(self, job_id: str, jenkins_job: str = "",
                              jenkins_build: int | None = None,
                              queue_id: int | None = None,
                              trigger_error: str = "",
                              result_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """回填 Jenkins 派发结果(M3)。

        触发成功:jenkins_job 置名 + jenkins_build(拿到时) + result_json.jenkins_queue_id;
        触发失败:jenkins_job 保持空串(轮询认领不跳过它,本地兜底可领),result_json
        记 jenkins_trigger_failed 原因。result_json 走读改写合并,不覆盖已有键。
        """
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT result_json FROM pipeline_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"pipeline job not found: {job_id}")
            result = json.loads(row["result_json"] or "{}")
            if trigger_error:
                result["jenkins_trigger_failed"] = trigger_error
            if result_extra:
                result.update(result_extra)  # 打包型任务:分支/环境/来源配置等元数据
            if queue_id is not None:
                result["jenkins_queue_id"] = queue_id
            connection.execute(
                """UPDATE pipeline_jobs SET jenkins_job=?, jenkins_build=?, result_json=?
                   WHERE job_id=?""",
                (jenkins_job, jenkins_build,
                 json.dumps(result, ensure_ascii=False), job_id),
            )
        return self.get_pipeline_job(job_id)

    def mark_build_pipeline_running(self, job_id: str, jenkins_job: str,
                                    jenkins_build: int) -> dict[str, Any]:
        """打包任务进入"运行中"(2026-09-24):Jenkins 对账发现构建已在跑。

        仅 build/webhook 来源且 pending/running 可置(幂等,重复调用无副作用);
        其它状态(终态/cancelling)不动。
        """
        with self.transaction() as connection:
            connection.execute(
                """UPDATE pipeline_jobs SET status='running', jenkins_job=?, jenkins_build=?
                   WHERE job_id=? AND status IN ('pending','running')
                     AND trigger_source IN ('build','webhook')""",
                (jenkins_job, jenkins_build, job_id),
            )
        return self.get_pipeline_job(job_id)

    @staticmethod
    def duration_between(started_at: str, ended_at: str) -> float:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            return max(0.0, (end - start).total_seconds())
        except ValueError:
            return 0.0

    @staticmethod
    def serialize_run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        item["pass_rate"] = round(
            item["passed_cases"] / item["total_cases"] * 100, 1
        ) if item["total_cases"] else 0.0
        return item

    @staticmethod
    def serialize_case(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["artifacts"] = json.loads(item.pop("artifacts_json") or "{}")
        return item

    def get_case(self, run_id: str, case_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM cases WHERE run_id=? AND case_id=?", (run_id, case_id)
            ).fetchone()
        if row is None:
            raise KeyError(f"case not found: {run_id}/{case_id}")
        return self.serialize_case(row)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            # 查询路径惰性收尾:执行 agent 失联的会话在此代管收尾(幂等)
            self._sweep_orphan_runs(connection)
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            cases = connection.execute(
                "SELECT * FROM cases WHERE run_id=? ORDER BY started_at, case_id", (run_id,)
            ).fetchall()
        result = self.serialize_run(row)
        result["cases"] = [self.serialize_case(case) for case in cases]
        return result

    def list_runs(self, platform: str = "", limit: int = 50, apk_id: str = "") -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 200)
        query = "SELECT * FROM runs"
        conditions: list[str] = []
        parameters: list[Any] = []
        if platform in VALID_PLATFORMS:
            conditions.append("platform=?")
            parameters.append(platform)
        if apk_id:
            conditions.append("apk_id=?")
            parameters.append(apk_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at DESC LIMIT ?"
        parameters.append(limit)
        with self.transaction() as connection:
            # 查询路径惰性收尾:执行 agent 失联的会话在此代管收尾(幂等)
            self._sweep_orphan_runs(connection)
            rows = connection.execute(query, parameters).fetchall()
        return [self.serialize_run(row) for row in rows]

    # ==================== 安装包管理 ====================

    def register_apk(self, payload: dict[str, Any]) -> dict[str, Any]:
        apk_id = str(payload.get("apk_id") or "").strip()
        if not apk_id:
            raise ValueError("apk_id is required")
        server_type = str(payload.get("server_type") or "test").strip().lower()
        if server_type not in VALID_SERVER_TYPES:
            raise ValueError(f"server_type 必须是 {'/'.join(VALID_SERVER_TYPES)} 之一")
        # os_type 显式值优先;缺省按 apk_id 前缀推断(mac 构建机旧脚本不带该字段)
        os_type = str(payload.get("os_type") or "").strip().lower() or infer_apk_os_type(apk_id)
        if os_type not in VALID_OS_TYPES:
            raise ValueError(f"os_type 必须是 {'/'.join(VALID_OS_TYPES)} 之一")
        build_time = str(payload.get("build_time") or "").strip() or None
        registered_at = str(payload.get("registered_at") or utc_now())
        file_hash = str(payload.get("file_hash") or "")
        with self.transaction() as connection:
            # 覆盖守卫(任务 024,20261016 事故:同 id 重构建顶掉旧包 10 例):同 apk_id
            # 内容不同=顶替,拒绝;新 apk_id 已带构建号段,正常流不触发,此闸兜底旧脚本误用。
            row = connection.execute(
                "SELECT file_hash FROM apks WHERE apk_id=?", (apk_id,)).fetchone()
            if row and file_hash != (row["file_hash"] or ""):
                raise ValueError(
                    f"apk_id={apk_id} 已登记且内容不同,禁止覆盖:"
                    "重复构建请用带构建号的新 apk_id,或先在页面删除旧行")
            connection.execute(
                """
                INSERT INTO apks (
                    apk_id, app_version, branch, commit_hash, flavor, source,
                    file_path, file_hash, notes, server_type, os_type, build_time,
                    jenkins_job, jenkins_build, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(apk_id) DO UPDATE SET
                    app_version=excluded.app_version,
                    branch=excluded.branch,
                    commit_hash=excluded.commit_hash,
                    flavor=excluded.flavor,
                    source=excluded.source,
                    file_path=excluded.file_path,
                    file_hash=excluded.file_hash,
                    notes=excluded.notes,
                    server_type=excluded.server_type,
                    os_type=excluded.os_type,
                    build_time=excluded.build_time,
                    jenkins_job=excluded.jenkins_job,
                    jenkins_build=excluded.jenkins_build,
                    registered_at=excluded.registered_at
                """,
                (
                    apk_id,
                    str(payload.get("app_version") or ""),
                    str(payload.get("branch") or ""),
                    str(payload.get("commit_hash") or ""),
                    str(payload.get("flavor") or ""),
                    str(payload.get("source") or "manual"),
                    str(payload.get("file_path") or ""),
                    file_hash,
                    str(payload.get("notes") or ""),
                    server_type,
                    os_type,
                    build_time,
                    str(payload.get("jenkins_job") or ""),
                    str(payload.get("jenkins_build") or ""),
                    registered_at,
                ),
            )
        return self.get_apk(apk_id)

    def apk_bound_builds(self, jenkins_job: str) -> set[int]:
        """某 Jenkins 任务已被 apks 行覆盖的构建号全量集合(包管理对账专用)。

        与 bound_jenkins_builds 同理不走列表窗口:补录行 registered_at=构建
        开始时间,行多了以后会沉出"最近 N 行"窗口,窗口判覆盖会反复拉控制台
        重算(register_apk_if_absent 幂等不会重复插行,差的是无谓开销)。
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT jenkins_build FROM apks WHERE jenkins_job=?",
                (jenkins_job,),
            ).fetchall()
        numbers: set[int] = set()
        for row in rows:
            try:
                numbers.add(int(row["jenkins_build"]))
            except (TypeError, ValueError):
                continue
        return numbers

    def register_apk_if_absent(self, payload: dict[str, Any]) -> bool:
        """对账补录用登记(2026-09-28):apk_id 不存在才整行插入,返回是否新插入。

        与 register_apk 的差异:绝不覆盖已登记行的业务字段(对账数据来自
        控制台解析,精度低于登记时的内存数据);已存在时仅回填空缺的
        jenkins_job/jenkins_build(修复手跑补录包行无构建号关联,使包管理
        行可跳转 Jenkins 日志)。
        """
        apk_id = str(payload.get("apk_id") or "").strip()
        if not apk_id:
            raise ValueError("apk_id is required")
        server_type = str(payload.get("server_type") or "test").strip().lower()
        if server_type not in VALID_SERVER_TYPES:
            raise ValueError(f"server_type 必须是 {'/'.join(VALID_SERVER_TYPES)} 之一")
        os_type = str(payload.get("os_type") or "").strip().lower() or infer_apk_os_type(apk_id)
        if os_type not in VALID_OS_TYPES:
            raise ValueError(f"os_type 必须是 {'/'.join(VALID_OS_TYPES)} 之一")
        registered_at = str(payload.get("registered_at") or utc_now())
        with self.transaction() as connection:
            existed = connection.execute(
                "SELECT 1 FROM apks WHERE apk_id=?", (apk_id,)).fetchone()
            connection.execute(
                """
                INSERT INTO apks (
                    apk_id, app_version, branch, commit_hash, flavor, source,
                    file_path, file_hash, notes, server_type, os_type, build_time,
                    jenkins_job, jenkins_build, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(apk_id) DO UPDATE SET
                    jenkins_job=CASE WHEN apks.jenkins_job=''
                                     THEN excluded.jenkins_job ELSE apks.jenkins_job END,
                    jenkins_build=CASE WHEN apks.jenkins_build=''
                                       THEN excluded.jenkins_build ELSE apks.jenkins_build END
                """,
                (
                    apk_id,
                    str(payload.get("app_version") or ""),
                    str(payload.get("branch") or ""),
                    str(payload.get("commit_hash") or ""),
                    str(payload.get("flavor") or ""),
                    str(payload.get("source") or "manual"),
                    str(payload.get("file_path") or ""),
                    str(payload.get("file_hash") or ""),
                    str(payload.get("notes") or ""),
                    server_type,
                    os_type,
                    str(payload.get("build_time") or "").strip() or None,
                    str(payload.get("jenkins_job") or ""),
                    str(payload.get("jenkins_build") or ""),
                    registered_at,
                ),
            )
            return existed is None

    def get_apk(self, apk_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM apks WHERE apk_id=?", (apk_id,)).fetchone()
        if row is None:
            raise KeyError(f"apk not found: {apk_id}")
        return dict(row)

    def list_apks(self, limit: int = 50, server_type: str = "",
                  os_type: str = "") -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 200)
        query = "SELECT * FROM apks"
        parameters: list[Any] = []
        if server_type in VALID_SERVER_TYPES:
            query += " WHERE server_type=?"
            parameters.append(server_type)
        if os_type in VALID_OS_TYPES:
            query += " AND os_type=?" if "WHERE" in query else " WHERE os_type=?"
            parameters.append(os_type)
        query += " ORDER BY registered_at DESC LIMIT ?"
        parameters.append(limit)
        with self.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def find_apk(self, branch: str, commit_hash: str, server_type: str) -> dict[str, Any] | None:
        """按 分支+commit+server_type 查最近一次登记(GitLab webhook 同 commit 去重用);无则 None。"""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM apks WHERE branch=? AND commit_hash=? AND server_type=?"
                " ORDER BY registered_at DESC LIMIT 1",
                (branch, commit_hash, server_type),
            ).fetchone()
        return dict(row) if row else None

    def delete_apk(self, apk_id: str) -> dict[str, Any]:
        # 只删台账行;磁盘上的 apk 文件与 runs 执行历史不动(弱关联,历史按 apk_id 仍可读)
        with self.transaction() as connection:
            row = connection.execute("SELECT apk_id FROM apks WHERE apk_id=?", (apk_id,)).fetchone()
            if row is None:
                raise KeyError(f"apk not found: {apk_id}")
            connection.execute("DELETE FROM apks WHERE apk_id=?", (apk_id,))
        return {"deleted": apk_id}

    # ==================== 用例执行历史（三视角之"某用例"） ====================

    def get_case_history(self, case_id: str, limit: int = 20) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 100)
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT c.case_id, c.name, c.status, c.started_at, c.ended_at,
                       c.duration_seconds, c.error,
                       r.run_id, r.platform, r.status AS run_status,
                       r.apk_id, r.app_version, r.device, r.started_at AS run_started_at
                FROM cases c JOIN runs r ON c.run_id = r.run_id
                WHERE c.case_id = ?
                ORDER BY c.started_at DESC LIMIT ?
                """,
                (case_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_case_def_history(self, case_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """用例定义的最近执行记录(2026-10-16 抽屉需求):cases 表的 case_id 是 pytest
        nodeid(tests/xx.py::Cls::test_x)或平台编号(aw-xxx)两种口径,两路都匹配;
        来源任务取 pipeline_jobs(run_id = 'pipe-' + job_id)的触发方式与 Jenkins 构建号。"""
        limit = min(max(int(limit), 1), 50)
        item = self._get_case_def_row(case_id)
        fp = str(item.get("file_path") or "").strip().replace("\\", "/")
        # file_path 里的 _ 是 LIKE 通配符,转义后再做前缀匹配
        like = fp.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "::%"
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.case_id AS nodeid, c.status, c.started_at, c.duration_seconds,
                       r.run_id, r.jenkins_build AS run_build, r.apk_id,
                       j.job_id, j.trigger_source, j.jenkins_job, j.jenkins_build AS job_build
                FROM cases c JOIN runs r ON c.run_id = r.run_id
                LEFT JOIN pipeline_jobs j ON r.run_id = 'pipe-' || j.job_id
                WHERE c.case_id = ? OR (? != '' AND c.case_id LIKE ? ESCAPE '\\')
                ORDER BY c.started_at DESC LIMIT ?
                """,
                (case_id, fp, like, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_case_ids(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """按关键词模糊搜用例(取最近出现的 case_id),供历史页选择。

        keyword 为空时不加 WHERE,返回全部 case_id(页面默认展示全部,搜索后才过滤)。
        """
        limit = min(max(int(limit), 1), 100)
        pattern = f"%{keyword}%"
        with self.transaction() as connection:
            if keyword:
                rows = connection.execute(
                    """
                    SELECT c.case_id, MAX(c.name) AS name, COUNT(*) AS executions,
                           MAX(c.started_at) AS last_seen
                    FROM cases c WHERE c.case_id LIKE ?
                    GROUP BY c.case_id ORDER BY last_seen DESC LIMIT ?
                    """,
                    (pattern, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT c.case_id, MAX(c.name) AS name, COUNT(*) AS executions,
                           MAX(c.started_at) AS last_seen
                    FROM cases c
                    GROUP BY c.case_id ORDER BY last_seen DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    # ---- 用例定义(case_defs):文字用例 + 自动化脚本用例,双向关联 + bug 单来源 ----

    @staticmethod
    def _validate_case_def(payload: dict[str, Any], for_update: bool = False) -> dict[str, Any]:
        case_type = str(payload.get("case_type") or "").strip().lower()
        if case_type not in {"text", "auto"}:
            raise ValueError("case_type 必须是 text(文字用例) 或 auto(自动化脚本用例)")
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("case_id 必填")
        fields = {
            "case_id": case_id,
            "case_type": case_type,
            "title": str(payload.get("title") or "").strip(),
            "module": str(payload.get("module") or "").strip(),
            # 优先级按 UI 规范 2.3 归一为 P0-P3:旧档 L1/L2/L3 入库时映射,非标值归 P3
            "priority": _normalize_priority(payload.get("priority")),
            "source": str(payload.get("source") or "").strip(),
            "source_bug": str(payload.get("source_bug") or "").strip(),
            "file_path": str(payload.get("file_path") or "").strip(),
            "content": str(payload.get("content") or ""),
            "linked_case_id": str(payload.get("linked_case_id") or "").strip(),
            # 用例范围(2026-10-09):''=未分类 / full=全量用例 / regression=回归用例
            "case_scope": str(payload.get("case_scope") or "").strip().lower(),
        }
        # structured(2026-10-13,用例域整合 P1):工作站原始报文 JSON,仅迁移/同步
        # 管线显式携带;payload 不携带时保留 None 语义(upsert UPDATE 分支不冲掉原值,
        # 防页面保存/sync_cases 重跑冲掉机器可读报文),与 human_confirmed 同构
        raw_structured = payload.get("structured")
        fields["structured"] = None if raw_structured is None else str(raw_structured)
        # review_meta(2026-10-13,P2 共库):人审状态 JSON,仅工作站人审/迁移管线
        # 显式携带;None 语义同 structured,页面保存不冲
        raw_review = payload.get("review_meta")
        fields["review_meta"] = None if raw_review is None else str(raw_review)
        if not fields["title"]:
            raise ValueError("title 必填")
        if fields["case_scope"] not in {"", "full", "regression"}:
            raise ValueError("case_scope 只能是 full(全量用例) / regression(回归用例) / 空(未分类)")
        # auto 用例不用 content(2026-10-09):脚本内容在代码工程维护(file_path 锁定
        # 来源仓库),忽略 payload 里的 content 置空,防脏数据;文字用例不受影响。
        # structured 同口径:auto 用例无工作站原始报文,置空(None 也归 '')。
        # review_meta 不置空:auto 未来也可挂接人审状态,由携带方自决
        if fields["case_type"] == "auto":
            fields["content"] = ""
            fields["structured"] = ""
        if fields["case_type"] == "auto" and fields["linked_case_id"] \
                and fields["linked_case_id"] == case_id:
            raise ValueError("linked_case_id 不能指向自己")
        return fields

    def batch_update_case_defs(self, case_ids: Any, patch: dict[str, Any]) -> dict[str, Any]:
        """批量编辑用例管理属性(2026-09-24,规范 4.2/8.5/8.6):仅 priority/module/case_scope。

        只动管理属性,不碰标题/内容等仓库源头字段;module 非空时自动登记进
        modules(与 upsert_case_def 同口径);case_scope 空=未分类(显式传''即批量
        归为未分类);返回实际更新行数,不存在的 id 自然跳过。
        """
        if isinstance(case_ids, str) or not isinstance(case_ids, list):
            raise ValueError("case_ids 必须是非空数组")
        ids = [str(c or "").strip() for c in case_ids]
        ids = [c for c in ids if c]
        if not ids:
            raise ValueError("case_ids 必须是非空数组")
        if not isinstance(patch, dict):
            raise ValueError("patch 必须是对象")
        updates: dict[str, Any] = {}
        if "priority" in patch:
            updates["priority"] = _normalize_priority(patch.get("priority"))
        if "module" in patch:
            updates["module"] = str(patch.get("module") or "").strip()
        if "case_scope" in patch:
            scope = str(patch.get("case_scope") or "").strip().lower()
            if scope not in {"", "full", "regression"}:
                raise ValueError("case_scope 只能是 full(全量用例) / regression(回归用例) / 空(未分类)")
            updates["case_scope"] = scope
        if not updates:
            raise ValueError("patch 至少包含 priority / module / case_scope 之一")
        with self.transaction() as connection:
            if updates.get("module"):
                connection.execute(
                    "INSERT OR IGNORE INTO modules (slug, name_cn) VALUES (?, ?)",
                    (updates["module"], MODULE_CN_SEED.get(updates["module"], updates["module"])),
                )
            sets = ", ".join(f"{key}=?" for key in updates) + ", updated_at=?"
            updated = connection.execute(
                f"UPDATE case_defs SET {sets} WHERE case_id IN ({','.join('?' * len(ids))})",
                (*updates.values(), utc_now(), *ids),
            ).rowcount
        return {"updated": updated}

    def upsert_case_def(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = self._validate_case_def(payload)
        now = utc_now()
        # link_strength(规范 8.2):strong=显式锚点/平台直建,weak=路径推断的弱关联。
        # 只在 payload 显式携带时才生效,否则 INSERT 默认 strong、UPDATE 保留原值——
        # 防止页面保存(不传该字段)把 sync 标的 weak 冲掉
        new_link = str(payload.get("link_strength") or "").strip().lower()
        if new_link not in {"strong", "weak"}:
            new_link = ""
        # human_confirmed(2026-09-29):「经人审工作站两道门确认」标志,0/1。
        # 与 link_strength 同构:只在 payload 显式携带时才生效,否则 INSERT 默认 0、
        # UPDATE 保留原值——防止页面编辑/sync_cases 重跑(不传该字段)冲掉已确认标志;
        # API 拉取每次显式带值,工作站改判(stored→pending)重拉即如实回写
        raw_human = payload.get("human_confirmed")
        new_human: int | None
        if raw_human is None:
            new_human = None  # 未携带:INSERT 落 0,UPDATE 保留原值
        elif str(raw_human).strip().lower() in ("1", "true", "yes"):
            new_human = 1
        else:
            new_human = 0  # 显式携带但非真值 => 0(pending 重拉/显式撤销确认)
        # biz_no(2026-09-30):平台有义展示编号(wifi-001 式),仅 API 拉取管线分配。
        # 与 human_confirmed 同构:payload 不携带时 INSERT 落 ''、UPDATE 保留原值——
        # 页面编辑/sync_cases 重跑不会冲掉;编号一经分配终身不变,工作站改号不影响
        new_biz = str(payload.get("biz_no") or "").strip() or None
        # case_scope(2026-10-09):用例范围分类(''=未分类/full=全量/regression=回归)。
        # 与 human_confirmed 同构:payload 不携带时 INSERT 落默认、UPDATE 保留原值——
        # 防止 sync_cases 重跑/旧调用方(不传该字段)冲掉人工归类;REG- 前缀用例
        # 新入库未携带时默认 regression(该前缀经用户确认全是回归用例)
        if payload.get("case_scope") is None:
            new_scope: str | None = None  # 未携带:INSERT 落默认,UPDATE 保留原值
        else:
            new_scope = fields["case_scope"]  # 显式携带(含清空为未分类)以校验后值为准
        with self.transaction() as connection:
            # 新模块 slug 自动登记进 modules(中文名优先取已知映射,否则先同 slug,
            # 后续可在模块管理里改),保证 sync_cases.py 再同步时筛选项与中文名映射不回退
            if fields["module"]:
                connection.execute(
                    "INSERT OR IGNORE INTO modules (slug, name_cn) VALUES (?, ?)",
                    (fields["module"], MODULE_CN_SEED.get(fields["module"], fields["module"])),
                )
            row = connection.execute(
                "SELECT case_id, link_strength, human_confirmed, biz_no, case_scope, structured, review_meta FROM case_defs WHERE case_id=?",
                (fields["case_id"],),
            ).fetchone()
            if row is None:
                # INSERT 的 scope 默认值:未携带时 REG- 前缀归 regression,其余未分类
                insert_scope = new_scope if new_scope is not None else (
                    "regression" if fields["case_id"].startswith("REG-") else "")
                connection.execute(
                    """INSERT INTO case_defs (
                        case_id, case_type, title, module, priority, source,
                        source_bug, file_path, content, linked_case_id,
                        link_strength, human_confirmed, biz_no, case_scope,
                        structured, review_meta, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fields["case_id"], fields["case_type"], fields["title"],
                     fields["module"], fields["priority"], fields["source"],
                     fields["source_bug"], fields["file_path"], fields["content"],
                     fields["linked_case_id"], new_link or "strong",
                     new_human if new_human is not None else 0,
                     new_biz or "", insert_scope,
                     fields["structured"] or "",
                     fields["review_meta"] if fields["review_meta"] is not None else "{}",
                     now, now),
                )
            else:
                connection.execute(
                    """UPDATE case_defs SET case_type=?, title=?, module=?,
                       priority=?, source=?, source_bug=?, file_path=?, content=?,
                       linked_case_id=?, link_strength=?, human_confirmed=?, biz_no=?,
                       case_scope=?, structured=?, review_meta=?, updated_at=? WHERE case_id=?""",
                    (fields["case_type"], fields["title"], fields["module"],
                     fields["priority"], fields["source"], fields["source_bug"],
                     fields["file_path"], fields["content"], fields["linked_case_id"],
                     new_link or row["link_strength"] or "strong",
                     new_human if new_human is not None else (row["human_confirmed"] or 0),
                     new_biz or row["biz_no"] or "",
                     new_scope if new_scope is not None else (row["case_scope"] or ""),
                     # structured/review_meta 未携带(None)时保留原值,防页面保存/重跑冲掉
                     fields["structured"] if fields["structured"] is not None
                         else (row["structured"] or ""),
                     fields["review_meta"] if fields["review_meta"] is not None
                         else (row["review_meta"] or "{}"),
                     now, fields["case_id"]),
                )
        return self.get_case_def(fields["case_id"])

    def reanchor_case_def(self, old_id: str, new_id: str) -> str:
        """工作站改号重锚(2026-09-30):工作站侧把编号从通用流水号改为
        模块slug-NNN 后,过渡期报文携带 formerId=旧编号;平台把旧行 case_id
        原地改为新编号,套件成员(suite_cases)与双向关联(linked_case_id)引用
        同步改写,其余字段(biz_no/human_confirmed/content 等)原样平移——
        平台展示编号与人工确认态均不丢。返回该行 biz_no。
        旧行不存在或新编号已被占用(冲突)时抛 KeyError,拉取管线跳过该条。
        执行历史(runs 等)按快照留旧编号,不回改。"""
        if old_id == new_id:
            return self._get_case_def_row(old_id).get("biz_no") or ""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT biz_no FROM case_defs WHERE case_id=?", (old_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"重锚源用例不存在: {old_id}")
            if connection.execute(
                "SELECT 1 FROM case_defs WHERE case_id=?", (new_id,)
            ).fetchone():
                raise KeyError(f"重锚目标编号已存在: {new_id}")
            connection.execute(
                "UPDATE case_defs SET case_id=?, updated_at=? WHERE case_id=?",
                (new_id, utc_now(), old_id),
            )
            connection.execute(
                "UPDATE suite_cases SET case_id=? WHERE case_id=?", (new_id, old_id),
            )
            connection.execute(
                "UPDATE case_defs SET linked_case_id=? WHERE linked_case_id=?",
                (new_id, old_id),
            )
        return row["biz_no"] or ""

    def _test_method_for(self, file_path: str) -> str:
        """自动化用例测试方法名(2026-10-15 列需求):读脚本源码取首个 def test_*。

        脚本内容不在库中(auto 用例不用 content),只能落盘读 file_path;
        按 路径+mtime 缓存,脚本改动自动重读;读不到(路径漂移/文件缺失)回空串。
        """
        fp = str(file_path or "").strip()
        if not fp:
            return ""
        path = Path(fp) if Path(fp).is_absolute() else PROJECT_ROOT / fp
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return ""
        key = str(path)
        hit = self._test_method_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        method = ""
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                matched = re.match(r"\s*def\s+(test_\w+)", line)
                if matched:
                    method = matched.group(1)
                    break
        except OSError:
            return ""
        self._test_method_cache[key] = (mtime, method)
        return method

    def get_case_def(self, case_id: str) -> dict[str, Any]:
        item = self._get_case_def_row(case_id)
        return self._decorate_case_def(item)

    def _get_case_def_row(self, case_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM case_defs WHERE case_id=?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"用例定义不存在: {case_id}")
        return dict(row)

    def _decorate_case_def(self, item: dict[str, Any]) -> dict[str, Any]:
        """补双向关联视图:text 用例挂上指向它的 auto 用例;auto 用例挂上对应文字用例标题。"""
        with self.connect() as connection:
            # 模块中文名(规范:展示一律 module_cn,查不到回退 slug)
            if item.get("module"):
                mrow = connection.execute(
                    "SELECT name_cn FROM modules WHERE slug=?", (item["module"],)
                ).fetchone()
                item["module_cn"] = (mrow["name_cn"] if mrow and mrow["name_cn"] else item["module"])
            else:
                item["module_cn"] = ""
            if item["case_type"] == "text":
                # 多值挂接(2026-10-13 P4):linked_case_id 允许逗号分隔(一个脚本覆盖
                # 多条回归用例),LIKE 全串匹配命中所有覆盖方
                autos = connection.execute(
                    """SELECT case_id, title, module, source_bug, file_path FROM case_defs
                       WHERE case_type='auto' AND (',' || linked_case_id || ',') LIKE ?
                       ORDER BY case_id""",
                    (f"%,{item['case_id']},%",),
                ).fetchall()
                # 详情抽屉关联区展示脚本方法名(2026-10-16),口径同 list_case_defs
                item["auto_cases"] = [
                    {**dict(a), "test_method": self._test_method_for(a["file_path"])} for a in autos
                ]
            else:
                # auto 行:拆逗号取全部挂接的文字用例(text_cases;text_case 保留为首条,兼容旧页面)
                linked_items = []
                for token in str(item["linked_case_id"] or "").split(","):
                    token = token.strip()
                    if not token:
                        continue
                    linked = connection.execute(
                        "SELECT case_id, title FROM case_defs WHERE case_id=?", (token,),
                    ).fetchone()
                    if linked:
                        linked_items.append(dict(linked))
                item["text_cases"] = linked_items
                item["text_case"] = linked_items[0] if linked_items else None
                item["test_method"] = self._test_method_for(item.get("file_path") or "")
        return item

    def list_case_defs(
        self, case_type: str = "", module: str = "", keyword: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if case_type:
            clauses.append("case_type=?")
            params.append(case_type)
        if module:
            clauses.append("module=?")
            params.append(module)
        if keyword:
            pattern = f"%{keyword}%"
            clauses.append(
                "(case_id LIKE ? OR biz_no LIKE ? OR title LIKE ? OR module LIKE ?"
                 " OR content LIKE ?)"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # 钳制放宽到 5000(2026-10-15):用例总量 1219 已破千,旧钳 1000 令列表视图
        # 永远看不到排在 1000 之后的文字用例(排序 auto 在前,auto 全量但 text 缺尾)
        limit = min(max(int(limit), 1), 5000)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT case_id, case_type, title, module, priority, source,
                    source_bug, file_path, linked_case_id, link_strength,
                    human_confirmed, biz_no, case_scope, updated_at
                    FROM case_defs{where} ORDER BY case_type, module, case_id
                    LIMIT ?""",
                (*params, limit),
            ).fetchall()
        # 列表视图一次性挂双向关联(text 行 -> auto_cases;auto 行 linked_case_id 逗号多值拆分)
        autos: dict[str, list[dict[str, Any]]] = {}
        # 套件归属装饰:一条 SQL 批量查全部成员关系,勿 N+1
        suites_map: dict[str, list[str]] = {}
        # 模块中文名映射(展示层用,库里的 module 仍存 slug)
        modules_map: dict[str, str] = {}
        # 编号->标题映射(2026-10-15 列需求):auto 行展示"对应文字用例"标题用
        titles_map: dict[str, str] = {}
        with self.connect() as connection:
            for row in connection.execute(
                """SELECT linked_case_id, case_id, title, module, source_bug, file_path
                   FROM case_defs WHERE case_type='auto' AND linked_case_id != ''
                   ORDER BY case_id"""
            ).fetchall():
                # 文字用例行也展示挂接脚本的测试方法名(2026-10-16 反馈):auto 行补 test_method
                auto_item = dict(row)
                auto_item["test_method"] = self._test_method_for(auto_item["file_path"])
                # 逗号多值:一条 auto 覆盖 N 条文字用例,每条都要能看到这个脚本
                for token in str(row["linked_case_id"]).split(","):
                    token = token.strip()
                    if token:
                        autos.setdefault(token, []).append(auto_item)
            for row in connection.execute(
                "SELECT case_id, title FROM case_defs"
            ).fetchall():
                titles_map[row["case_id"]] = row["title"]
            for row in connection.execute(
                "SELECT case_id, suite_id FROM suite_cases ORDER BY suite_id, seq"
            ).fetchall():
                suites_map.setdefault(row["case_id"], []).append(row["suite_id"])
            for row in connection.execute("SELECT slug, name_cn FROM modules").fetchall():
                modules_map[row["slug"]] = row["name_cn"] or row["slug"]
        items = []
        for row in rows:
            item = dict(row)
            if item["case_type"] == "text":
                item["auto_cases"] = autos.get(item["case_id"], [])
            else:
                # auto 行装饰(2026-10-15 列需求):挂接的文字用例 标题+编号,
                # 以及脚本里的测试方法名(读 py 源码,见 _test_method_for)
                item["text_cases"] = [
                    {"case_id": t, "title": titles_map.get(t, "")}
                    for t in (tok.strip() for tok in str(item["linked_case_id"] or "").split(","))
                    if t
                ]
                item["test_method"] = self._test_method_for(item["file_path"])
            item["suites"] = suites_map.get(item["case_id"], [])
            item["module_cn"] = modules_map.get(item["module"], item["module"])
            items.append(item)
        return items

    def list_case_defs_structured(self) -> list[dict[str, Any]]:
        """工作站接入导出(2026-10-13,用例域整合 P1/P2):text 用例全量轻列+原始报文。

        正文唯一权威视角:工作站 serve.py 拉此端点取 structured 还原 cases/0.3
        格式展示。只出 text(auto 无工作站报文),不挂装饰(autos/suites/module_cn),
        按编号排序,工作站侧自行合并人审状态(decisions.json 权威)。
        """
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT case_id, title, module, priority, human_confirmed,
                   structured, review_meta, updated_at FROM case_defs
                   WHERE case_type='text' ORDER BY case_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_case_def(self, case_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT case_id FROM case_defs WHERE case_id=?", (case_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"用例定义不存在: {case_id}")
            # SQLite FK 未启用,手动清理套件成员,防留孤儿(list_suite_cases 会查出 title=NULL)
            removed = connection.execute(
                "DELETE FROM suite_cases WHERE case_id=?", (case_id,)
            ).rowcount
            connection.execute("DELETE FROM case_defs WHERE case_id=?", (case_id,))
        return {"deleted": case_id, "removed_suite_members": removed}

    # ==================== 流水线配置(定时调度) ====================

    @staticmethod
    def _decorate_pipeline_config(row: sqlite3.Row) -> dict[str, Any]:
        """补实时字段:schedule(解析后的参数对象) + next_run_at(实时算,不落库)。"""
        item = dict(row)
        try:
            schedule = json.loads(item.get("schedule_param") or "{}")
        except json.JSONDecodeError:
            schedule = {}
        item["schedule"] = schedule if isinstance(schedule, dict) else {}
        try:
            build = json.loads(item.get("build_param") or "{}")
        except json.JSONDecodeError:
            build = {}
        item["build"] = build if isinstance(build, dict) else {}
        item["next_run_at"] = compute_next_run_at(
            str(item.get("schedule_type") or ""), item.get("schedule_param") or "{}"
        )
        return item

    def create_pipeline_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = validate_pipeline_config(payload)
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT config_id FROM pipeline_configs WHERE config_id=?",
                (fields["config_id"],),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"config_id 已存在: {fields['config_id']}")
            connection.execute(
                """INSERT INTO pipeline_configs (
                    config_id, name, description, suite_id, include_build,
                    schedule_type, schedule_param, enabled, platform, kind, build_param,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fields["config_id"], fields["name"], fields["description"],
                 fields["suite_id"], fields["include_build"], fields["schedule_type"],
                 fields["schedule_param"], fields["enabled"], fields["platform"],
                 fields["kind"], fields["build_param"], now, now),
            )
        return self.get_pipeline_config(fields["config_id"])

    def get_pipeline_config(self, config_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_configs WHERE config_id=?", (config_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"流水线配置不存在: {config_id}")
        return self._decorate_pipeline_config(row)

    def list_pipeline_configs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_configs ORDER BY config_id"
            ).fetchall()
        return [self._decorate_pipeline_config(row) for row in rows]

    def update_pipeline_config(self, config_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """部分更新:payload 未带的字段沿用库里现值,整体重新校验。"""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_configs WHERE config_id=?", (config_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"流水线配置不存在: {config_id}")
            merged = {
                "config_id": config_id,
                "name": payload.get("name", row["name"]),
                "description": payload.get("description", row["description"]),
                "suite_id": payload.get("suite_id", row["suite_id"]),
                "include_build": payload.get("include_build", row["include_build"]),
                "schedule_type": payload.get("schedule_type", row["schedule_type"]),
                "schedule_param": payload.get("schedule_param", row["schedule_param"]),
                "enabled": payload.get("enabled", row["enabled"]),
                "platform": payload.get("platform", row["platform"]),
                "kind": payload.get("kind", row["kind"]),
                "build_param": payload.get("build_param", row["build_param"]),
            }
            fields = validate_pipeline_config(merged)
            connection.execute(
                """UPDATE pipeline_configs SET name=?, description=?, suite_id=?,
                   include_build=?, schedule_type=?, schedule_param=?, enabled=?,
                   platform=?, kind=?, build_param=?, updated_at=? WHERE config_id=?""",
                (fields["name"], fields["description"], fields["suite_id"],
                 fields["include_build"], fields["schedule_type"],
                 fields["schedule_param"], fields["enabled"], fields["platform"],
                 fields["kind"], fields["build_param"], utc_now(), config_id),
            )
        return self.get_pipeline_config(config_id)

    def delete_pipeline_config(self, config_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT config_id FROM pipeline_configs WHERE config_id=?", (config_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"流水线配置不存在: {config_id}")
            connection.execute("DELETE FROM pipeline_configs WHERE config_id=?", (config_id,))
        return {"deleted": config_id}

    def run_pipeline_config(self, config_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """立即执行:照 trigger 通道建一个 schedule 来源任务,并回写 last_run_at。

        pipeline_jobs 的状态机/claim 逻辑不动;payload 可附带 platform/branch/
        case_filter/apk_id 覆盖。case_filter 的取值优先级:
          1) payload 显式给了 case_filter → 以 payload 为准(保留覆盖语义);
          2) 否则配置关联了套件 → 聚合套件成员的 pytest 目标(list_suite_case_paths
             已把 YAML 驱动用例映射为 test_case_driver node id,按 seq 顺序空格连接);
          3) 套件无可用成员 / 未关联套件 → 留空,执行 agent 回落默认 target。
        """
        config = self.get_pipeline_config(config_id)  # 不存在抛 KeyError → 404
        if str(config.get("kind") or "suite") == "build":
            # 打包型流水线:不建用例执行任务,直接触发打包 job;任务行仅作执行记录。
            # body 带 {"source":"schedule"} 的调用来自调度器,NOTES 前缀记 daily:(定时)
            scheduled = str(payload.get("source") or "") == "schedule"
            job = self._run_build_pipeline(config, scheduled=scheduled)
            with self.transaction() as connection:
                connection.execute(
                    "UPDATE pipeline_configs SET last_run_at=? WHERE config_id=?",
                    (utc_now(), config_id),
                )
            return {"config": self.get_pipeline_config(config_id), "job": job,
                    "case_filter_source": "build"}
        payload_case_filter = str(payload.get("case_filter") or "").strip()
        if payload_case_filter:
            # 覆盖语义:运行时显式指定的 case_filter 优先于套件聚合
            case_filter, filter_source = payload_case_filter, "payload"
        elif str(config.get("suite_id") or "").strip():
            # 套件聚合:list_suite_case_paths 已含 YAML→node id 映射,不再按 .py 过滤
            targets = self.list_suite_case_paths(config["suite_id"])
            case_filter = " ".join(targets)
            filter_source = "suite" if targets else ""
        else:
            case_filter, filter_source = "", ""
        # 端解析(2026-10-15):payload 覆盖 > 关联套件的 os_type > ''(不限端)。
        # 套件已删/值非法时回落 ''(不阻塞执行,兼容历史配置)
        device_os = str(payload.get("device_os") or "").strip().lower()
        if not device_os and str(config.get("suite_id") or "").strip():
            try:
                suite_os = str(self.get_test_suite(config["suite_id"]).get("os_type") or "")
            except KeyError:  # 套件已删:照旧行为聚合空目标回落默认 target,不阻塞
                suite_os = ""
            if suite_os in SUITE_OS_TYPES:
                device_os = suite_os
        job = try_jenkins_dispatch(self, self.create_pipeline_job({
            "trigger_source": "schedule",
            # 运行时 payload 优先,其次配置里的 platform 列,最后兜底 flutter
            "platform": str(payload.get("platform") or config.get("platform") or "flutter"),
            "branch": str(payload.get("branch") or ""),
            "case_filter": case_filter,
            "apk_id": str(payload.get("apk_id") or ""),
            "device_os": device_os,
        }))
        with self.transaction() as connection:
            connection.execute(
                "UPDATE pipeline_configs SET last_run_at=? WHERE config_id=?",
                (utc_now(), config_id),
            )
        # filter_source 摘要:payload=运行时覆盖 / suite=套件聚合 / 空串=回落执行 agent 默认 target
        return {
            "config": self.get_pipeline_config(config_id),
            "job": job,
            "case_filter_source": filter_source or "default",
        }

    def _run_build_pipeline(self, config: dict[str, Any],
                            scheduled: bool = False) -> dict[str, Any]:
        """打包型流水线(kind=build)执行:按配置 platform 选打包 job 出包,不跑用例。

        job 选择(2026-10-15):platform=flutter-ios → swan-build-IOS(mac 构建机,
        JENKINS_IOS_BUILD_JOB 可覆盖);flutter/flutter-android 及 legacy flutter →
        swan-build-android(JENKINS_BUILD_JOB 可覆盖)。

        任务行 trigger_source='build' 仅作执行记录:轮询认领在 SQL 里排除 build 来源,
        终态由读取路径的 resolve_build_pipeline_jobs 向 Jenkins 惰性回查(排队中无构建号,
        构建号由 resolver 从 queue_id 补拿)。触发即失败(Jenkins 未配置/触发报错)直接落
        failed,不留 pending。NOTES 前缀区分来源(2026-09-29):定时任务 daily:<配置ID>
        (包管理页显示"定时自动构建"),页面"立即执行" pipeline:<配置ID>("流水线主动
        打包");对账认领按两前缀等价匹配。
        """
        build = config.get("build") or {}
        branch = str(build.get("branch") or "dev")
        server_type = str(build.get("server_type") or "test")
        config_id = str(config.get("config_id") or "")
        platform = str(config.get("platform") or "flutter").strip().lower()
        result = {"build": {"branch": branch, "server_type": server_type,
                            "pipeline": config_id}}
        job = self.create_pipeline_job({
            "trigger_source": "build",
            "platform": platform,
            "branch": branch,
            "case_filter": "",
            "apk_id": "",
        })
        if not jenkins_client.config():
            return self.finish_build_pipeline_job(
                job["job_id"], "failed",
                {**result, "error": "Jenkins 未配置(JENKINS_BASE_URL/JENKINS_USER/JENKINS_API_TOKEN)"})
        if platform == "flutter-ios":
            build_job = (os.environ.get("JENKINS_IOS_BUILD_JOB", "").strip()
                         or "swan-build-IOS")
        else:
            build_job = os.environ.get("JENKINS_BUILD_JOB", "").strip() or "swan-build-android"
        # NOTES 与 /api/builds 同规:URL 编码后传,build_swan_apk.py 侧 unquote 解码;
        # 前缀标来源(daily:=定时任务 / pipeline:=立即执行),见方法 docstring
        outcome = jenkins_client.trigger(build_job, {
            "BRANCH": branch, "SERVER_TYPE": server_type,
            "NOTES": quote(f"{'daily' if scheduled else 'pipeline'}:{config_id}")})
        if not outcome.get("ok"):
            return self.finish_build_pipeline_job(
                job["job_id"], "failed",
                {**result, "error": f"Jenkins 触发失败: {outcome.get('error')}"})
        return self.mark_jenkins_dispatch(
            job["job_id"], jenkins_job=build_job,
            queue_id=outcome.get("queue_id"), result_extra=result)

    # ==================== 测试套件(M4 P1) ====================

    def create_test_suite(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = validate_test_suite(payload)
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT suite_id FROM test_suites WHERE suite_id=?",
                (fields["suite_id"],),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"suite_id 已存在: {fields['suite_id']}")
            connection.execute(
                """INSERT INTO test_suites (
                    suite_id, name, description, suite_type, os_type, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (fields["suite_id"], fields["name"], fields["description"],
                 fields["suite_type"], fields["os_type"], fields["enabled"], now, now),
            )
        return self.get_test_suite(fields["suite_id"])

    def get_test_suite(self, suite_id: str) -> dict[str, Any]:
        """套件详情:带 case_count 与成员用例列表(join case_defs 取标题/模块/类型/优先级)。"""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM test_suites WHERE suite_id=?", (suite_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"测试套件不存在: {suite_id}")
            item = dict(row)
            item["case_count"] = connection.execute(
                "SELECT COUNT(*) FROM suite_cases WHERE suite_id=?", (suite_id,)
            ).fetchone()[0]
        item["cases"] = self.list_suite_cases(suite_id)
        return item

    def list_test_suites(self) -> list[dict[str, Any]]:
        """套件列表:每行带 case_count(子查询一次算齐)。"""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT ts.*,
                          (SELECT COUNT(*) FROM suite_cases sc WHERE sc.suite_id = ts.suite_id)
                          AS case_count
                   FROM test_suites ts ORDER BY ts.created_at DESC, ts.suite_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def update_test_suite(self, suite_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """部分更新:payload 未带的字段沿用库里现值,整体重新校验。"""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM test_suites WHERE suite_id=?", (suite_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"测试套件不存在: {suite_id}")
            merged = {
                "suite_id": suite_id,
                "name": payload.get("name", row["name"]),
                "description": payload.get("description", row["description"]),
                "suite_type": payload.get("suite_type", row["suite_type"]),
                "os_type": payload.get("os_type", row["os_type"]),
                "enabled": payload.get("enabled", row["enabled"]),
            }
            fields = validate_test_suite(merged)
            connection.execute(
                """UPDATE test_suites SET name=?, description=?, suite_type=?, os_type=?,
                   enabled=?, updated_at=? WHERE suite_id=?""",
                (fields["name"], fields["description"], fields["suite_type"],
                 fields["os_type"], fields["enabled"], utc_now(), suite_id),
            )
        return self.get_test_suite(suite_id)

    def delete_test_suite(self, suite_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT suite_id FROM test_suites WHERE suite_id=?", (suite_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"测试套件不存在: {suite_id}")
            connection.execute("DELETE FROM suite_cases WHERE suite_id=?", (suite_id,))
            connection.execute("DELETE FROM test_suites WHERE suite_id=?", (suite_id,))
        return {"deleted": suite_id}

    def list_suite_case_paths(self, suite_id: str) -> list[str]:
        """套件成员的 pytest 目标列表(按 seq 排序,跳过空串),供流水线/冒烟联动聚合。

        一次 JOIN 查完避免 N+1;只取 enabled=1 的成员。file_path 规范化为
        相对仓库根的 posix 路径(绝对路径会尝试转相对,仓库外的丢弃)。
        YAML 驱动用例(knowledge/swan/cases/driver/*.yaml)映射为
        tests/flutter/test_case_driver.py::TestCaseDriver::test_yaml_<stem>
        (驱动脚本按 stem 注入 test 方法,平台机实测收集合法;M5 二期已完成)。
        """
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT cd.file_path
                   FROM suite_cases sc JOIN case_defs cd ON cd.case_id = sc.case_id
                   WHERE sc.suite_id=? AND sc.enabled=1 AND cd.file_path != ''
                   ORDER BY sc.seq, sc.case_id""",
                (suite_id,),
            ).fetchall()
        targets: list[str] = []
        for row in rows:
            raw = str(row["file_path"]).replace("\\", "/").strip()
            if raw.startswith("./"):
                raw = raw[2:]
            if not raw:
                continue
            path = Path(raw)
            if path.is_absolute():
                try:
                    raw = path.resolve().relative_to(PROJECT_ROOT).as_posix()
                except ValueError:  # 仓库外的绝对路径,无法作为 pytest 目标,跳过
                    continue
            target = self._yaml_path_to_node_id(raw) if path.suffix.lower() == ".yaml" else raw
            if target and target not in targets:
                targets.append(target)
        return targets

    @staticmethod
    def _yaml_path_to_node_id(raw_path: str) -> str | None:
        """YAML 驱动用例路径 → pytest node id;不在驱动用例目录内的 YAML 返回 None(跳过)。

        子目录内(如 driver/login/POS-002.yaml)同样按 stem 映射——驱动脚本
        注入方法只看 stem,与目录层级无关。
        """
        prefix = "knowledge/swan/cases/driver/"
        if not raw_path.startswith(prefix):
            return None
        stem = Path(raw_path[len(prefix):]).stem
        if not stem:
            return None
        return f"tests/flutter/test_case_driver.py::TestCaseDriver::test_yaml_{stem}"

    def suite_case_paths(self, suite_id: str) -> dict[str, Any]:
        """套件成员脚本路径(只读,GET /api/suites/<id>/case-paths,冒烟联动 M5)。

        套件不存在抛 KeyError → 404(与"存在但无成员"区分,联动脚本依赖此语义报错)。
        device_os 一并返回(2026-10-15):trigger_smoke 据此把端透传进触发 payload。
        """
        suite = self.get_test_suite(suite_id)  # 不存在 → KeyError
        return {"suite_id": suite_id, "paths": self.list_suite_case_paths(suite_id),
                "device_os": str(suite.get("os_type") or "")}

    def list_suite_cases(self, suite_id: str) -> list[dict[str, Any]]:
        """套件成员有序列表,LEFT JOIN case_defs 带出标题/模块/类型/优先级。"""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT sc.case_id, sc.seq, sc.enabled, cd.title, cd.module,
                          cd.case_type, cd.priority
                   FROM suite_cases sc LEFT JOIN case_defs cd ON cd.case_id = sc.case_id
                   WHERE sc.suite_id=? ORDER BY sc.seq, sc.case_id""",
                (suite_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_suite_cases(self, suite_id: str, case_ids: Any) -> dict[str, Any]:
        """批量添加成员:case_id 必须存在于 case_defs(缺失整体 400);重复添加幂等。

        传全量有序列表时即为"按传入顺序重排 seq";未列入的既有成员 seq 保持不变,
        列入的成员(新增或既有)按传入顺序排在它们之后。
        """
        self.get_test_suite(suite_id)  # 不存在抛 KeyError → 404
        if isinstance(case_ids, str):
            case_ids = [case_ids]
        if not isinstance(case_ids, list):
            raise ValueError("case_ids 必须是数组")
        wanted: list[str] = []
        for value in case_ids:
            case_id = str(value or "").strip()
            if case_id and case_id not in wanted:
                wanted.append(case_id)
        if not wanted:
            raise ValueError("case_ids 不能为空")
        with self.transaction() as connection:
            found = {
                row["case_id"]
                for row in connection.execute(
                    f"SELECT case_id FROM case_defs WHERE case_id IN ({','.join('?' * len(wanted))})",
                    wanted,
                )
            }
            missing = sorted(set(wanted) - found)
            if missing:
                raise ValueError("以下 case_id 不存在于 case_defs: " + ", ".join(missing))
            existing = {
                row["case_id"]: row["seq"]
                for row in connection.execute(
                    "SELECT case_id, seq FROM suite_cases WHERE suite_id=?", (suite_id,)
                )
            }
            # 未列入本次调用的既有成员 seq 保持,新成员/重排成员接在其后
            keep_max = max(
                (seq for cid, seq in existing.items() if cid not in set(wanted)),
                default=0,
            )
            added: list[str] = []
            for offset, case_id in enumerate(wanted, start=1):
                seq = keep_max + offset
                if case_id in existing:
                    if existing[case_id] != seq:
                        connection.execute(
                            "UPDATE suite_cases SET seq=? WHERE suite_id=? AND case_id=?",
                            (seq, suite_id, case_id),
                        )
                else:
                    connection.execute(
                        "INSERT INTO suite_cases (suite_id, case_id, seq, enabled) VALUES (?, ?, ?, 1)",
                        (suite_id, case_id, seq),
                    )
                    added.append(case_id)
        return {"added": len(added), "case_count": len(existing) + len(added)}

    def remove_suite_cases(self, suite_id: str, case_ids: Any) -> dict[str, Any]:
        """从套件移除成员(支持批量);未加入的 case_id 忽略,不报错。"""
        self.get_test_suite(suite_id)  # 不存在抛 KeyError → 404
        if isinstance(case_ids, str):
            case_ids = [case_ids]
        if not isinstance(case_ids, list):
            raise ValueError("case_ids 必须是数组")
        wanted = sorted({str(value or "").strip() for value in case_ids} - {""})
        if not wanted:
            raise ValueError("case_ids 不能为空")
        with self.transaction() as connection:
            cursor = connection.execute(
                f"DELETE FROM suite_cases WHERE suite_id=? AND case_id IN ({','.join('?' * len(wanted))})",
                (suite_id, *wanted),
            )
            removed = cursor.rowcount
        return {"removed": removed}

    # ==================== 功能模块(modules,模块管理) ====================

    @staticmethod
    def _validate_module(payload: dict[str, Any]) -> dict[str, Any]:
        slug = str(payload.get("slug") or "").strip()
        if not slug:
            raise ValueError("slug 必填")
        if len(slug) > 64:
            raise ValueError("slug 长度不能超过 64")
        try:
            sort_order = int(payload.get("sort_order") or 0)
        except (TypeError, ValueError):
            raise ValueError("sort_order 必须是整数") from None
        return {
            "slug": slug,
            "name_cn": str(payload.get("name_cn") or "").strip() or slug,
            "project": str(payload.get("project") or "swan").strip() or "swan",
            "sort_order": sort_order,
        }

    def list_modules(self) -> list[dict[str, Any]]:
        """模块列表:带 case_count(该 slug 被 case_defs 引用的条数),按排序字段+slug 排。"""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT m.*,
                          (SELECT COUNT(*) FROM case_defs cd WHERE cd.module = m.slug) AS case_count
                   FROM modules m ORDER BY m.sort_order, m.slug"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_module(self, slug: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM modules WHERE slug=?", (slug,)).fetchone()
        if row is None:
            raise KeyError(f"模块不存在: {slug}")
        return dict(row)

    def create_module(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = self._validate_module(payload)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT slug FROM modules WHERE slug=?", (fields["slug"],)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"模块已存在: {fields['slug']}")
            connection.execute(
                "INSERT INTO modules (slug, name_cn, project, sort_order) VALUES (?, ?, ?, ?)",
                (fields["slug"], fields["name_cn"], fields["project"], fields["sort_order"]),
            )
        return self.get_module(fields["slug"])

    def update_module(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        """部分更新:用于改中文名/排序;slug 不可改(它是 case_defs.module 的外键语义)。"""
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM modules WHERE slug=?", (slug,)).fetchone()
            if row is None:
                raise KeyError(f"模块不存在: {slug}")
            merged = {
                "slug": slug,
                "name_cn": payload.get("name_cn", row["name_cn"]),
                "project": payload.get("project", row["project"]),
                "sort_order": payload.get("sort_order", row["sort_order"]),
            }
            fields = self._validate_module(merged)
            connection.execute(
                "UPDATE modules SET name_cn=?, project=?, sort_order=? WHERE slug=?",
                (fields["name_cn"], fields["project"], fields["sort_order"], slug),
            )
        return self.get_module(slug)

    def delete_module(self, slug: str) -> dict[str, Any]:
        """删除模块:删除前校验 case_defs 引用数,有引用则 400 拒绝。"""
        with self.transaction() as connection:
            row = connection.execute("SELECT slug FROM modules WHERE slug=?", (slug,)).fetchone()
            used = connection.execute(
                "SELECT COUNT(*) FROM case_defs WHERE module=?", (slug,)
            ).fetchone()[0]
            if row is None:
                if used:  # 历史孤儿:用例还挂着但模块表没登记,清引用属库操作,端点不代做
                    raise ValueError(f"模块 {slug} 未在模块表登记(历史孤儿),仍有 {used} 条用例引用,需先改这些用例的模块")
                raise KeyError(f"模块不存在: {slug}")
            if used:
                raise ValueError(f"模块下有 {used} 条用例,禁止删除")
            connection.execute("DELETE FROM modules WHERE slug=?", (slug,))
        return {"deleted": slug}


class DashboardHandler(BaseHTTPRequestHandler):
    store: DashboardStore

    def log_message(self, format_string: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = HTTPStatus.OK,
                  set_cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int) -> None:
        self.send_json({"error": message}, status)

    def _session_user(self) -> str | None:
        """cookie 用户名 → 对照 users 表确认在册且启用(逐请求真闸门,规范 5.6)。"""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == AUTH_COOKIE and value:
                try:
                    return self.store.auth_check(unquote(value))
                except Exception:
                    return None  # 名单读取失败按未登录处理,不放行
        return None

    def _gate_authorized(self, parsed) -> bool:
        """浏览器面登录闸:公开 auth 端点与机器白名单之外,一律要求有效会话。"""
        if parsed.path in AUTH_PUBLIC_PATHS:
            return True
        if (self.command, parsed.path) in MACHINE_EXEMPT_EXACT:
            return True
        for method, prefix in MACHINE_EXEMPT_PREFIX:
            if self.command == method and parsed.path.startswith(prefix):
                return True
        return self._session_user() is not None

    def base_url(self) -> str:
        """按请求 Host 还原绝对地址基座(下载链接/二维码用),无 Host 头时退回本机端口。"""
        host = self.headers.get("Host", "").strip()
        if host:
            return f"http://{host}"
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Dashboard-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")  # 页面即改即生效,防浏览器放旧页
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/vendor/"):
            # 页面本地依赖(如 SheetJS)随仓库分发,内网无外网也能加载;仅允许 vendor 目录下的直接文件
            name = parsed.path.removeprefix("/vendor/").lstrip("/")
            target = (VENDOR_DIR / name).resolve()
            if target.parent != VENDOR_DIR or not target.is_file():
                self.send_error_json("vendor 文件不存在", HTTPStatus.NOT_FOUND)
                return
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "application/javascript; charset=utf-8" if target.suffix == ".js" else "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # ---- 浏览器面登录闸(规范 5.6):页面/静态资源公开,其余除公开 auth 端点
        # 与机器白名单外一律 401;前端遇 401 统一转登录视图 ----
        if not self._gate_authorized(parsed):
            self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
            return
        if parsed.path == "/api/health":
            self.send_json({"status": "ok"})
            return
        # ---- 平台登录(auth)----
        if parsed.path == "/api/auth/presets":
            self.send_json({"items": self.store.list_presets()})
            return
        if parsed.path == "/api/auth/me":
            user = self._session_user()
            if user is None:
                self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
            else:
                # 姓氏全拼随身份一并下发,边栏头像渲染用(v1.2 §14)
                try:
                    surname = self.store.get_user(user)["surname"]
                except KeyError:
                    surname = ""
                self.send_json({"username": user, "surname": surname})
            return
        if parsed.path == "/api/auth/users":
            self.send_json({"items": self.store.list_users()})
            return
        # ---- AI 任务看板(看板/列表/统计/用量/详情;/api/tasks/usage 须先于 /api/tasks/ 前缀)----
        if parsed.path == "/api/tasks/board":
            # 竖版看板单请求(v1.2 §A9);须先于 /api/tasks/ 前缀路由
            query = parse_qs(parsed.query)
            self.send_json(self.store.task_board(
                q=(query.get("q") or [""])[0].strip(),
                viewer=self._session_user() or "",
            ))
            return
        if parsed.path == "/api/tasks":
            query = parse_qs(parsed.query)
            viewer = self._session_user() or ""
            self.send_json(self.store.list_tasks(
                bucket=(query.get("bucket") or [""])[0],
                status=(query.get("status") or [""])[0],
                q=(query.get("q") or [""])[0].strip(),
                page=int((query.get("page") or ["1"])[0] or 1),
                pagesize=int((query.get("pagesize") or ["10"])[0] or 10),
                viewer=viewer,
            ))
            return
        if parsed.path == "/api/tasks/stats":
            self.send_json(self.store.task_stats(self._session_user() or ""))
            return
        if parsed.path == "/api/tasks/usage":
            self.send_json({"items": self.store.task_usage()})
            return
        if parsed.path == "/api/tasks/queue":
            # dispatch_worker 领活通道(机器豁免):活跃任务全量+问答历史
            self.send_json(self.store.dispatch_queue())
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.removeprefix("/api/tasks/").strip("/")
            try:
                self.send_json(self.store.get_task(task_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/runs":
            query = parse_qs(parsed.query)
            platform = query.get("platform", [""])[0].lower()
            apk_id = query.get("apk_id", [""])[0]
            limit = query.get("limit", ["50"])[0]
            try:
                runs = self.store.list_runs(platform=platform, limit=int(limit), apk_id=apk_id)
            except (TypeError, ValueError):
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"items": runs})
            return
        if parsed.path == "/api/apks":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            server_type = query.get("server_type", [""])[0].strip().lower()
            os_type = query.get("os_type", [""])[0].strip().lower()
            try:
                reconcile_apks_from_jenkins(self.store)  # 缺行补录(2026-09-28)
            except Exception as error:  # noqa: BLE001 — 对账失败不影响列表
                _reap_log(f"reconcile apks 失败: {error}",
                          filename="jenkins_dispatch.log")
            items = self.store.list_apks(limit=limit, server_type=server_type,
                                         os_type=os_type)
            base_url = self.base_url()
            # 日志链接给浏览器跳转用:优先 JENKINS_PUBLIC_URL(对外可达地址);未配置回退 JENKINS_BASE_URL
            # (BASE_URL 供服务端回调 Jenkins,可为 localhost/127.0.0.1;PUBLIC_URL 必须是浏览器可达地址)
            jenkins_base = (os.environ.get("JENKINS_PUBLIC_URL", "").strip() or os.environ.get("JENKINS_BASE_URL", "").strip()).rstrip("/")
            for item in items:
                # download_url 为绝对地址(二维码直接指向它);file_exists 供前端禁用下载按钮防 404
                item["download_url"] = f"{base_url}/api/apks/{quote(str(item['apk_id']), safe='')}/download"
                item["file_exists"] = apk_file_on_disk(item)
                # jenkins_log_url: Jenkins 构建日志页;基址未配置或该包无构建号则为空串,前端不渲染按钮
                jjob = str(item.get("jenkins_job") or "").strip()
                jbuild = str(item.get("jenkins_build") or "").strip()
                item["jenkins_log_url"] = (
                    f"{jenkins_base}/job/{quote(jjob, safe='')}/{quote(jbuild, safe='')}/console"
                    if jenkins_base and jjob and jbuild
                    else ""
                )
            self.send_json({"items": items})
            return
        if parsed.path.startswith("/api/apks/") and parsed.path.endswith("/download"):
            # 按包登记的 file_path 回源 APK 文件流,手机扫码/浏览器直接下载
            apk_id = unquote(parsed.path.removeprefix("/api/apks/").removesuffix("/download").strip("/"))
            if not apk_id:
                self.send_error_json("apk_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                apk = self.store.get_apk(apk_id)
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                return
            file_path = str(apk.get("file_path") or "").strip()
            target = Path(file_path) if file_path else None
            if target is None or not target.is_file():
                # 登记机本机路径不可达(跨机登记,构建机≠登记机)→ 回源平台归档副本
                archive = APK_ARCHIVE_DIR / f"{apk_id}.apk"
                target = archive if archive.is_file() else None
            if target is None:
                self.send_error_json(
                    f"apk 文件不在磁盘上,无法下载(file_path={file_path or '未登记'});"
                    "请重新打包登记(自动归档)或核对产物路径",
                    HTTPStatus.NOT_FOUND,
                )
                return
            # iOS 包(os_type=ios 或产物 .ipa)下载名给 .ipa、MIME 用通用二进制流;
            # 归档回源路径仍按 <apk_id>.apk 契约(与上传/巡检对账一致,不随 os 变)
            is_ios = (str(apk.get("os_type") or "") == "ios") or file_path.lower().endswith(".ipa")
            ext = "ipa" if is_ios else "apk"
            filename = quote(f"{apk_id}.{ext}", safe="()")
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "application/octet-stream" if is_ios
                else "application/vnd.android.package-archive",
            )
            self.send_header("Content-Length", str(target.stat().st_size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename}",
            )
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with target.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    self.wfile.write(chunk)
            return
        if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/history"):
            case_id = parsed.path.removeprefix("/api/cases/").removesuffix("/history").strip("/")
            if not case_id:
                self.send_error_json("case_id is required", HTTPStatus.BAD_REQUEST)
                return
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["20"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"items": self.store.get_case_history(case_id, limit=limit)})
            return
        if parsed.path == "/api/cases/search":
            query = parse_qs(parsed.query)
            keyword = query.get("q", [""])[0].strip()
            try:
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            # 未搜索状态展示全部用例,搜索后才返回匹配结果
            self.send_json({"items": self.store.search_case_ids(keyword, limit=limit)})
            return
        if parsed.path == "/api/gateway/health":
            # 验证码工具页的健康卡片:平台侧代理 otp-gateway /healthz,避免浏览器跨域
            import urllib.request as _urlreq
            gateway = os.environ.get("OTP_GATEWAY_URL", "http://127.0.0.1:18080").rstrip("/")
            try:
                with _urlreq.urlopen(f"{gateway}/healthz", timeout=5) as response:
                    body = response.read().decode("utf-8", "replace")
                self.send_json({"ok": True, "gateway": gateway, "detail": body[:200]})
            except Exception as error:  # noqa: BLE001 — 健康代理失败本身就是信息
                self.send_json({"ok": False, "gateway": gateway, "detail": str(error)})
            return
        if parsed.path == "/api/case-defs":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"items": self.store.list_case_defs(
                case_type=query.get("type", [""])[0].strip(),
                module=query.get("module", [""])[0].strip(),
                keyword=query.get("q", [""])[0].strip(),
                limit=limit,
            )})
            return
        if parsed.path.startswith("/api/case-defs/") and parsed.path.endswith("/history"):
            # 用例详情抽屉的执行记录(2026-10-16);须先于 /api/case-defs/<id> 通配匹配
            case_id = unquote(parsed.path.removeprefix("/api/case-defs/").removesuffix("/history").strip("/"))
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["10"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json({"items": self.store.get_case_def_history(case_id, limit=limit)})
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/case-defs/export":
            # 用例域整合 P1/P2(2026-10-13):工作站接入导出,text 全量轻列+structured。
            # 精确路径须先于 /api/case-defs/<id> 通配匹配(同 case-paths 教训)
            self.send_json({"cases": self.store.list_case_defs_structured()})
            return
        if parsed.path.startswith("/api/case-defs/"):
            case_id = unquote(parsed.path.removeprefix("/api/case-defs/").strip("/"))  # unquote:中文编号(aw-xxx)路径解码;ASCII 编号不受影响
            try:
                self.send_json(self.store.get_case_def(case_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/modules":
            self.send_json({"items": self.store.list_modules()})
            return
        if parsed.path == "/api/pipeline-configs":
            self.send_json({"items": self.store.list_pipeline_configs()})
            return
        if parsed.path.startswith("/api/pipeline-configs/"):
            config_id = parsed.path.removeprefix("/api/pipeline-configs/").strip("/")
            try:
                self.send_json(self.store.get_pipeline_config(config_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/suites":
            self.send_json({"items": self.store.list_test_suites()})
            return
        if parsed.path.startswith("/api/suites/") and parsed.path.endswith("/case-paths"):
            # 只读端点(流水线/冒烟联动 M5):套件成员脚本路径,供外部聚合 pytest 目标;
            # 须先于 /api/suites/<id> 通配匹配
            suite_id = parsed.path.removeprefix("/api/suites/").removesuffix("/case-paths").strip("/")
            try:
                self.send_json(self.store.suite_case_paths(suite_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/suites/"):
            suite_id = parsed.path.removeprefix("/api/suites/").strip("/")
            try:
                self.send_json(self.store.get_test_suite(suite_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/devices":
            self.send_json({"items": self.store.list_devices()})
            return
        if parsed.path == "/api/pipeline/jobs":
            query = parse_qs(parsed.query)
            status = query.get("status", [""])[0].strip()
            try:
                limit = int(query.get("limit", ["30"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            resolve_build_pipeline_jobs(self.store)  # 打包型任务惰性回查 Jenkins 终态
            items = self.store.list_pipeline_jobs(status=status, limit=limit)
            # jenkins_log_url 给浏览器跳转用(流水线列表"Jenkins 日志"按钮),与 APK 列表
            # 同规则:优先 JENKINS_PUBLIC_URL(对外可达),回退 JENKINS_BASE_URL(服务端
            # 回调用,可为 127.0.0.1);有构建号→该次构建的控制台日志,只有任务名(排队中)
            # →任务页;未派发 Jenkins 的本地兜底任务为空串,前端不渲染按钮
            jenkins_base = (os.environ.get("JENKINS_PUBLIC_URL", "").strip() or os.environ.get("JENKINS_BASE_URL", "").strip()).rstrip("/")
            for item in items:
                jjob = str(item.get("jenkins_job") or "").strip()
                jbuild = str(item.get("jenkins_build") or "").strip()
                item["jenkins_log_url"] = (
                    f"{jenkins_base}/job/{quote(jjob, safe='')}/{quote(jbuild, safe='')}/console"
                    if jenkins_base and jjob and jbuild
                    else f"{jenkins_base}/job/{quote(jjob, safe='')}/" if jenkins_base and jjob else ""
                )
            self.send_json({"items": items})
            return
        if parsed.path == "/api/zentao/config":
            from dashboard.zentao_client import is_configured
            self.send_json({
                "configured": is_configured(),
                "url": os.environ.get("ZENTAO_URL", ""),
            })
            return
        if parsed.path.startswith("/api/devices/"):
            device_id = parsed.path.removeprefix("/api/devices/").strip("/")
            try:
                self.send_json(self.store.get_device(device_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/pipeline/jobs/"):
            job_id = parsed.path.removeprefix("/api/pipeline/jobs/").strip("/")
            try:
                self.send_json(self.store.get_pipeline_job(job_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.removeprefix("/api/runs/").strip("/")
            if not run_id:
                self.send_error_json("run_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.get_run(run_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
                # ==================== 机器监控(部署计划 v3 §7.5):Prometheus 只读代理,无任意 query 透传 ====================
        if parsed.path == "/api/metrics/hosts":
            self.send_json({"items": metrics_machine_list()})
            return
        if parsed.path == "/api/metrics/summary":
            try:
                self.send_json(metrics_summary())
            except MetricsUnavailable as error:
                self.send_json(
                    {"error": "监控组件未就绪", "detail": str(error)},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return
        if parsed.path == "/api/metrics/history":
            try:
                self.send_json(metrics_history(parse_qs(parsed.query)))
            except MetricsBadRequest as error:
                self.send_error_json(str(error), HTTPStatus.BAD_REQUEST)
            except MetricsUnavailable as error:
                self.send_json(
                    {"error": "监控组件未就绪", "detail": str(error)},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return
        # ==================== 机器监控·Claude 任务视窗(2026-10-15):只读枚举,服务端 10s 缓存 ====================
        # force=1 供页面手动刷新绕缓存;编排永不抛错(单机失败在 payload 里标 online=false)。
        if parsed.path == "/api/machines/claude-tasks":
            query = parse_qs(parsed.query)
            force = (query.get("force") or ["0"])[0] == "1"
            self.send_json(claude_tasks.claude_tasks_summary(force=force))
            return
        # ==================== 巡检流程总览(规范 6.9):快照只读,缺失→404,超期判定归前端 ====================
        if parsed.path == "/api/patrol/status":
            try:
                self.send_json(patrol_status())
            except PatrolStatusMissing as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        # 巡检快照历史(2026-10-12 入库配套):最近 N 轮,新→旧;登录闸内,limit 上限 200
        if parsed.path == "/api/patrol/history":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                self.send_error_json("limit must be an integer", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json({"items": patrol_history(self.store.database_path, max(1, min(limit, 200)))})
            except sqlite3.Error as error:
                self.send_error_json(f"巡检历史查询失败: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_error_json("not found", HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 5 * 1024 * 1024:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def zentao_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        """从失败用例生成禅道 bug 草稿(纯组装,不提交;提交走 /api/zentao/create)。"""
        run_id = str(payload.get("run_id") or "").strip()
        case_name = str(payload.get("case_name") or "").strip()
        if not run_id or not case_name:
            raise ValueError("run_id 和 case_name 必填")
        try:
            run = self.store.get_run(run_id)
        except KeyError as error:
            raise ValueError(str(error)) from error
        case = next((c for c in run.get("cases") or [] if c.get("name") == case_name), None)
        if case is None:
            raise ValueError(f"会话 {run_id} 中找不到用例 {case_name}")
        module = case_name.split(".py::")[0].rsplit("/", 1)[-1] if ".py::" in case_name else ""
        title = f"[Swan][{module or run['platform']}] {case_name.split('::')[-1]} 自动化失败({run_id})"
        steps_lines = [
            "【自动化复现步骤】",
            f"1. 执行会话 {run_id}(平台页可查,设备 {run.get('device') or '-'},包体 {run.get('apk_id') or '-'})",
            f"2. 用例: {case_name}",
            f"3. 实际结果: {case.get('status')} - {case.get('error') or '见执行日志'}",
            "",
            f"【执行日志摘要】",
            (case.get("log") or "无")[-1500:],
        ]
        artifacts = case.get("artifacts") or {}
        evidence_dir = artifacts.get("evidence_dir") or ""
        if evidence_dir:
            steps_lines.append(f"\n【逐步证据截图目录】{evidence_dir}")
        return {
            "title": title,
            "steps": "\n".join(steps_lines),
            "severity": 3,
            "pri": 3,
            "bug_type": "业务缺陷",
            "run_id": run_id,
            "case_name": case_name,
        }

    def read_optional_json(self) -> dict[str, Any]:
        """同 read_json,但允许空请求体(流水线"立即执行"类接口)。"""
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return self.read_json()

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            # 浏览器面登录闸(同 do_GET;公开 auth 端点与机器白名单豁免)
            if not self._gate_authorized(parsed):
                self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
                return
            # 立即执行允许空请求体,须先于 read_json 分流(否则空 body 直接 400)
            if parsed.path.startswith("/api/pipeline-configs/") and parsed.path.endswith("/run"):
                config_id = parsed.path.removeprefix("/api/pipeline-configs/").removesuffix("/run").strip("/")
                self.send_json(
                    self.store.run_pipeline_config(config_id, self.read_optional_json()),
                    HTTPStatus.CREATED,
                )
                return
            # 任务取消(M-B):允许空请求体,同样先于 read_json 分流。
            # 不存在 404 / 非 pending/running 409(单独映射,不走外层 ValueError→400)
            if parsed.path.startswith("/api/pipeline/jobs/") and parsed.path.endswith("/cancel"):
                job_id = parsed.path.removeprefix("/api/pipeline/jobs/").removesuffix("/cancel").strip("/")
                try:
                    self.send_json(self.store.cancel_pipeline_job(job_id))
                except KeyError as error:
                    self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                except ValueError as error:
                    self.send_error_json(str(error), HTTPStatus.CONFLICT)
                return
            # APK 归档上传:构建机登记成功后把产物字节推给平台(原始 octet-stream,
            # 须先于 read_json 分流)。扫码下载跨机回源走 output/apks/<apk_id>.apk。
            if parsed.path.startswith("/api/apks/") and parsed.path.endswith("/file"):
                apk_id = unquote(parsed.path.removeprefix("/api/apks/").removesuffix("/file").strip("/"))
                if not apk_id:
                    self.send_error_json("apk_id is required", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self.store.get_apk(apk_id)
                except KeyError as error:
                    self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > (1 << 29):
                    self.send_error_json("body 须为 APK 原始字节(1B-512MB)", HTTPStatus.BAD_REQUEST)
                    return
                APK_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                dest = APK_ARCHIVE_DIR / f"{apk_id}.apk"
                tmp = dest.with_name(dest.name + ".part")
                received = 0
                try:
                    with open(tmp, "wb") as handle:
                        while received < length:
                            chunk = self.rfile.read(min(1 << 20, length - received))
                            if not chunk:
                                raise OSError("客户端提前断开")
                            handle.write(chunk)
                            received += len(chunk)
                    os.replace(tmp, dest)  # 落地后原子改名,避免半截文件被回源
                except OSError as error:
                    tmp.unlink(missing_ok=True)
                    self.send_error_json(f"APK 归档保存失败: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self.send_json({"ok": True, "apk_id": apk_id, "bytes": received, "stored": str(dest)})
                return
            # 退出登录:页面发的是无请求体 POST,须先于 read_json 分流(否则空 body 400,
            # 退出从未真正清过 cookie——v1.2 自查发现的历史 bug)
            if parsed.path == "/api/auth/logout":
                cookie = f"{AUTH_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                self.send_json({"ok": True}, set_cookie=cookie)
                return
            payload = self.read_json()
            # ---- 平台登录(auth;公开端点 presets/me 在 GET,login/logout 公开可匿名)----
            if parsed.path == "/api/auth/login":
                username = self.store.auth_check(str(payload.get("username") or "").strip())
                if username is None:
                    # 名单本就公开,不存在/停用统一"用户名不存在",不做模糊提示(设计 §2.1)
                    self.send_error_json("用户名不存在", HTTPStatus.NOT_FOUND)
                    return
                cookie = (f"{AUTH_COOKIE}={quote(username)}; Path=/; HttpOnly;"
                          f" SameSite=Lax; Max-Age={AUTH_COOKIE_MAX_AGE}")
                # 姓氏全拼登录即下发(进应用后边栏头像免二次查询,v1.2 §14)
                try:
                    surname = self.store.get_user(username)["surname"]
                except KeyError:
                    surname = ""
                self.send_json({"username": username, "surname": surname}, set_cookie=cookie)
                return
            # ---- 设置弹窗账号台账(登录后全员可用,无角色)----
            if parsed.path == "/api/auth/users":
                try:
                    self.send_json(self.store.create_user(payload), HTTPStatus.CREATED)
                except DuplicateUserError as error:
                    self.send_error_json(str(error), HTTPStatus.CONFLICT)
                return
            if parsed.path.startswith("/api/auth/users/"):
                username = unquote(parsed.path.removeprefix("/api/auth/users/").strip("/"))
                try:
                    self.send_json(self.store.update_user(username, payload))
                except KeyError as error:
                    self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                return
            # ---- AI 任务看板 ----
            if parsed.path == "/api/tasks":
                payload["submitter"] = self._session_user()  # 提交人=当前登录人,不收前端值
                self.send_json(self.store.create_task(payload), HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/answer"):
                task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/answer").strip("/")
                user = self._session_user()
                try:
                    task = self.store.get_task(task_id)
                except KeyError as error:
                    self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                    return
                # 答复框只对提交人渲染是工作流语义;服务端同样按提交人校验(设计 §2.5)
                if not user or task["submitter"] != user:
                    self.send_error_json("仅任务提交人可答复", HTTPStatus.FORBIDDEN)
                    return
                try:
                    self.send_json(self.store.answer_task(task_id, payload, user))
                except TaskStateError as error:
                    self.send_error_json(str(error), HTTPStatus.CONFLICT)
                return
            # ---- dispatch worker 内部通道(机器豁免,见模块头 MACHINE_EXEMPT_*)----
            if parsed.path == "/api/dispatch/update":
                try:
                    self.send_json(self.store.dispatch_update(payload))
                except TaskStateError as error:
                    self.send_error_json(str(error), HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/runs":
                self.send_json(self.store.create_run(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/apks":
                self.send_json(self.store.register_apk(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/webhooks/gitlab":
                # GitLab push webhook → 自动打包(2026-09-22 webhook 方案):dev 分支
                # 有推送即走 /api/builds 同链路触发 Jenkins。鉴权:X-GitLab-Token 与
                # .env.local GITLAB_WEBHOOK_SECRET 恒时比对(未配置视同拒绝);触发参数
                # 全部服务端生成——branch 来自固定映射表、COMMIT 取 payload 40 位十六
                # 进制校验、notes 固定前缀+白名单用户名,不给坑 15 留注入面。GitLab
                # 侧分支过滤仅为省流,本映射表才是权威;所有分支恒回 2xx(GitLab 对
                # 非 2xx 指数退避重试,鉴权失败除外——401 让其丢弃即可)。
                secret = os.environ.get("GITLAB_WEBHOOK_SECRET", "").strip()
                header_token = self.headers.get("X-GitLab-Token", "")
                if not secret or not hmac.compare_digest(
                        header_token.encode("utf-8"), secret.encode("utf-8")):
                    self.send_error_json("invalid webhook token", HTTPStatus.UNAUTHORIZED)
                    return

                def _skip(reason: str) -> None:
                    print(f"[gitlab-webhook] skip: {reason}", flush=True)
                    self.send_json({"ok": True, "action": "skipped", "reason": reason})

                if str(payload.get("object_kind") or "") != "push":
                    _skip(f"object_kind={payload.get('object_kind') or ''} 非 push 事件")
                    return
                ref = str(payload.get("ref") or "")
                branch = ref.removeprefix("refs/heads/")
                after = str(payload.get("after") or "")
                # 全 0 是分支删除/空树哨兵,且 0 是合法十六进制字符,须显式排除
                if not re.fullmatch(r"[0-9a-f]{40}", after) or after == "0" * 40:
                    _skip(f"ref={ref} after={after[:12]!r} 非常规分支推送(删除分支/空树等)")
                    return
                repo = str((payload.get("project") or {}).get("path_with_namespace") or "")
                server_type = WEBHOOK_BUILD_MAP.get(repo, {}).get(branch, "")
                if not server_type:
                    _skip(f"{repo or '(unknown repo)'} {branch} 不在自动打包映射")
                    return
                short_sha = after[:7]
                is_app_repo = repo in WEBHOOK_APP_REPOS
                if is_app_repo:
                    seen = self.store.find_apk(branch, short_sha, server_type)
                    if seen:
                        _skip(f"{seen['apk_id']} 已登记(同 commit 去重)")
                        return
                notes = "webhook"
                pusher = str(payload.get("user_name") or "").strip()
                if re.fullmatch(r"[A-Za-z0-9_.\-]+", pusher):
                    notes += ":" + pusher
                build_job = os.environ.get("JENKINS_BUILD_JOB", "").strip() or "swan-build-android"
                # 流水线可见性(2026-09-24):webhook 触发同样落 pipeline_jobs 行,
                # 流水线页"最近任务"可见;终态与 build 来源同走 resolve_build_pipeline_jobs
                result = {"build": {"branch": branch, "server_type": server_type,
                                    "repo": repo, "notes": notes}}
                if is_app_repo:
                    result["build"]["commit"] = short_sha
                job = self.store.create_pipeline_job({
                    "trigger_source": "webhook", "platform": "flutter",
                    "branch": branch, "case_filter": "", "apk_id": ""})
                if not jenkins_client.config():
                    self.store.finish_build_pipeline_job(
                        job["job_id"], "failed",
                        {**result, "error": "Jenkins 未配置(JENKINS_BASE_URL/JENKINS_USER/JENKINS_API_TOKEN)"})
                    self.send_error_json(
                        "Jenkins 未配置(JENKINS_BASE_URL/JENKINS_USER/JENKINS_API_TOKEN)",
                        HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                trigger_params = {"BRANCH": branch, "SERVER_TYPE": server_type,
                                  "NOTES": quote(notes)}
                if is_app_repo:
                    # COMMIT=app 仓要签出的 commit;core 等依赖仓的 sha 不进此参
                    trigger_params["COMMIT"] = after
                outcome = jenkins_client.trigger(build_job, trigger_params)
                if not outcome.get("ok"):
                    print(f"[gitlab-webhook] trigger failed: {outcome.get('error')}", flush=True)
                    self.store.finish_build_pipeline_job(
                        job["job_id"], "failed",
                        {**result, "error": f"Jenkins 触发失败: {outcome.get('error')}"})
                    self.send_error_json(f"Jenkins 触发失败: {outcome.get('error')}",
                                         HTTPStatus.BAD_GATEWAY)
                    return
                self.store.mark_jenkins_dispatch(
                    job["job_id"], jenkins_job=build_job,
                    queue_id=outcome.get("queue_id"), result_extra=result)
                print(f"[gitlab-webhook] triggered: {repo} {branch}@{short_sha} "
                      f"server_type={server_type} queue_id={outcome.get('queue_id')} "
                      f"pipeline_job={job['job_id']}", flush=True)
                body = {"ok": True, "action": "triggered", "job": build_job,
                        "branch": branch, "queue_id": outcome.get("queue_id"),
                        "pipeline_job": job["job_id"]}
                if is_app_repo:
                    body["commit"] = short_sha
                self.send_json(body)
                return
            if parsed.path == "/api/builds":
                # 主动触发打包(M3):Jenkins 参数化 job(默认 swan-build-android,可用
                # JENKINS_BUILD_JOB 覆盖)。NOTES 必须 URL 编码后再传——规避 Windows
                # 批处理对中文/特殊字符的编码坑;build_swan_apk.py 侧 unquote 解码
                branch = str(payload.get("branch") or "").strip()
                if not branch:
                    self.send_error_json("branch is required", HTTPStatus.BAD_REQUEST)
                    return
                if not re.fullmatch(r"[A-Za-z0-9._/\-]+", branch):
                    # Jenkins job 把 BRANCH 原样拼进批处理命令行,白名单外字符(空格
                    # & | 等)可注入命令以 SYSTEM 执行——分支名只放行合法字符(坑 15)
                    self.send_error_json("branch 只允许字母/数字/._/与-字符",
                                         HTTPStatus.BAD_REQUEST)
                    return
                server_type = str(payload.get("server_type") or "").strip() or "test"
                if server_type not in VALID_SERVER_TYPES:
                    self.send_error_json("server_type must be formal or test",
                                         HTTPStatus.BAD_REQUEST)
                    return
                # os_type 选打包 job:ios → swan-build-IOS(mac 构建机),android → 安卓 job;
                # 仅主动打包区分平台,webhook/流水线定时打包仍是安卓专用(见不做清单)
                os_type = str(payload.get("os_type") or "").strip().lower() or "android"
                if os_type not in VALID_OS_TYPES:
                    self.send_error_json("os_type must be android or ios",
                                         HTTPStatus.BAD_REQUEST)
                    return
                notes = str(payload.get("notes") or "").strip()
                if len(notes) > 200:
                    self.send_error_json("notes too long (max 200 chars)",
                                         HTTPStatus.BAD_REQUEST)
                    return
                if re.search(r'[&|<>^"%]', notes):
                    # quote 只是 URL 编码不是命令转义,& | < > ^ " % 经 %NOTES% 批处理
                    # 展开仍可穿透命令行——黑名单硬拒(坑 15)
                    self.send_error_json('notes 含命令特殊字符(& | < > ^ " %)',
                                         HTTPStatus.BAD_REQUEST)
                    return
                if not jenkins_client.config():
                    self.send_error_json(
                        "Jenkins 未配置(JENKINS_BASE_URL/JENKINS_USER/JENKINS_API_TOKEN)",
                        HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                if os_type == "ios":
                    build_job = (os.environ.get("JENKINS_IOS_BUILD_JOB", "").strip()
                                 or "swan-build-IOS")
                else:
                    build_job = os.environ.get("JENKINS_BUILD_JOB", "").strip() or "swan-build-android"
                params = {"BRANCH": branch, "SERVER_TYPE": server_type}
                commit = str(payload.get("commit") or "").strip()
                if commit:
                    # 短哈希只可能是十六进制,白名单外字符同理可注入命令行(坑 15)
                    if not re.fullmatch(r"[0-9a-fA-F]{1,40}", commit):
                        self.send_error_json("commit 只允许 1-40 位十六进制",
                                             HTTPStatus.BAD_REQUEST)
                        return
                    params["COMMIT"] = commit
                # 来源前缀:手动打包(包管理页"主动打包")记 manual:*,无备注也记 manual,
                # 与 pipeline:/daily:/webhook: 前缀并列供前端分类展示(2026-09-29)
                params["NOTES"] = quote(f"manual:{notes}" if notes else "manual")
                outcome = jenkins_client.trigger(build_job, params)
                if not outcome.get("ok"):
                    self.send_error_json(f"Jenkins 触发失败: {outcome.get('error')}",
                                         HTTPStatus.BAD_GATEWAY)
                    return
                self.send_json({"ok": True, "job": build_job,
                                "queue_id": outcome.get("queue_id"),
                                "location": outcome.get("location")})
                return
            if parsed.path == "/api/case-defs":
                self.send_json(self.store.upsert_case_def(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/case-defs/batch-update":
                # 批量编辑(2026-09-24,规范 4.2/8.6):管理属性 priority/module/case_scope
                self.send_json(self.store.batch_update_case_defs(
                    payload.get("case_ids"), payload.get("patch") or {}))
                return
            if parsed.path == "/api/case-defs/pull-api":
                # 退役(2026-10-13,用例域整合 P2):共库后工作站与测试平台直连同一
                # case_defs 表,不再有"从 API 拉取"概念(工作站具备正文 CRUD,人审
                # 状态经 review_meta 列共享)。返回 410 提示调用方改走共库路径。
                self.send_error_json(
                    "该端点已退役:工作站与测试平台已共库(case_defs),"
                    "文字用例直接在工作站维护或经批量导入,无需拉取",
                    HTTPStatus.GONE)
                return
            if parsed.path == "/api/modules":
                self.send_json(self.store.create_module(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/pipeline-configs":
                self.send_json(self.store.create_pipeline_config(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/suites":
                self.send_json(self.store.create_test_suite(payload), HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/suites/") and parsed.path.endswith("/cases"):
                suite_id = parsed.path.removeprefix("/api/suites/").removesuffix("/cases").strip("/")
                # 批量 {case_ids:[...]} 或单个 {case_id:"..."}
                case_ids = payload.get("case_ids")
                if case_ids is None:
                    case_ids = payload.get("case_id")
                self.send_json(self.store.add_suite_cases(suite_id, case_ids), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/devices/register":
                self.send_json(self.store.register_device(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/devices/acquire":
                try:
                    self.send_json(self.store.acquire_device(payload), HTTPStatus.CREATED)
                except LookupError as error:
                    self.send_error_json(str(error), HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if parsed.path == "/api/zentao/draft":
                self.send_json(self.zentao_draft(payload))
                return
            if parsed.path == "/api/zentao/create":
                from dashboard.zentao_client import ZentaoError, create_bug
                if not str(payload.get("confirm") or ""):
                    self.send_error_json("提单需人工确认(confirm 字段)", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    result = create_bug(
                        product_id=int(payload["product_id"]),
                        title=str(payload["title"]),
                        steps=str(payload["steps"]),
                        severity=int(payload.get("severity") or 3),
                        pri=int(payload.get("pri") or 3),
                    )
                except (ZentaoError, KeyError, ValueError) as error:
                    self.send_error_json(str(error), HTTPStatus.BAD_GATEWAY)
                    return
                self.send_json(result, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/pipeline/trigger":
                # 平台策略(2026-09-29):UI 自动化仅用测试服包。formal 包是 Release
                # 构建,kReleaseMode 锁死正式服,验证码网关(testnetwork)取不到它的
                # 验证码,${otp} 用例必失败(jenkins-swan-autotest #37/#38/#39 实证)。
                # 触发入口一律拦截:build-downstream 由 trigger_smoke 按预期跳过
                # (409 优雅退出),手动/其他来源直接 409 提示。apk 查不到(异常路径)
                # 不拦截,维持原行为由装包环节兜底报错。
                trigger_apk_id = str(payload.get("apk_id") or "").strip()
                if trigger_apk_id:
                    try:
                        trigger_server_type = str(
                            self.store.get_apk(trigger_apk_id).get("server_type") or "")
                    except KeyError:
                        trigger_server_type = ""
                    if trigger_server_type == "formal":
                        self.send_error_json(
                            f"UI 自动化仅允许测试服包: {trigger_apk_id} 是 formal 包"
                            "(Release 构建锁正式服,验证码网关取不到码),任务未创建",
                            HTTPStatus.CONFLICT,
                        )
                        return
                job = self.store.create_pipeline_job(payload)
                # M3:建完尝试派 Jenkins;失败留 pending 本地兜底(try_jenkins_dispatch 内消化)
                self.send_json(try_jenkins_dispatch(self.store, job), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/pipeline/claim":
                try:
                    self.send_json(self.store.claim_pipeline_job(payload))
                except LookupError as error:
                    self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
                return
            if parsed.path.startswith("/api/pipeline/jobs/") and parsed.path.endswith("/finish"):
                job_id = parsed.path.removeprefix("/api/pipeline/jobs/").removesuffix("/finish").strip("/")
                self.send_json(self.store.finish_pipeline_job(job_id, payload))
                return
            if parsed.path.startswith("/api/devices/") and parsed.path.endswith("/release"):
                device_id = parsed.path.removeprefix("/api/devices/").removesuffix("/release").strip("/")
                self.send_json(self.store.release_device(device_id, payload))
                return
            if parsed.path.startswith("/api/devices/") and parsed.path.endswith("/status"):
                device_id = parsed.path.removeprefix("/api/devices/").removesuffix("/status").strip("/")
                self.send_json(self.store.set_device_status(device_id, payload))
                return
            if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/cases"):
                run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/cases").strip("/")
                self.send_json(self.store.upsert_case(run_id, payload), HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/complete"):
                run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/complete").strip("/")
                self.send_json(self.store.complete_run(run_id, payload))
                return
            if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/zentao-sync"):
                # M6 禅道测试单回写:仅此手动端点(页面按钮触发),无任何自动回写路径
                run_id = parsed.path.removeprefix("/api/runs/").removesuffix("/zentao-sync").strip("/")
                from dashboard.zentao_client import ZentaoError
                try:
                    self.send_json(self.store.sync_run_zentao(run_id))
                except RunNotFinalizedError as error:
                    self.send_error_json(str(error), HTTPStatus.CONFLICT)
                except ZentaoError as error:
                    self.send_error_json(str(error), HTTPStatus.BAD_GATEWAY)
                return
            self.send_error_json("not found", HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_error_json(str(error), HTTPStatus.BAD_REQUEST)
        except KeyError as error:
            self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_error_json(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)


    def do_PUT(self) -> None:
        try:
            parsed = urlparse(self.path)
            if not self._gate_authorized(parsed):
                self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
                return
            payload = self.read_json()
            if parsed.path.startswith("/api/case-defs/"):
                case_id = unquote(parsed.path.removeprefix("/api/case-defs/").strip("/"))  # unquote:中文编号(aw-xxx)路径解码;ASCII 编号不受影响
                if not case_id:
                    self.send_error_json("case_id is required", HTTPStatus.BAD_REQUEST)
                    return
                payload["case_id"] = case_id
                self.send_json(self.store.upsert_case_def(payload))
                return
            if parsed.path.startswith("/api/modules/"):
                # unquote(2026-10-16):中文/空格 slug(如「Wi-Fi 设备模块」)URL 编码后原样查库必 404
                slug = unquote(parsed.path.removeprefix("/api/modules/").strip("/"))
                if not slug:
                    self.send_error_json("slug is required", HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(self.store.update_module(slug, payload))
                return
            if parsed.path.startswith("/api/pipeline-configs/"):
                config_id = parsed.path.removeprefix("/api/pipeline-configs/").strip("/")
                if not config_id:
                    self.send_error_json("config_id is required", HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(self.store.update_pipeline_config(config_id, payload))
                return
            if parsed.path.startswith("/api/suites/"):
                suite_id = parsed.path.removeprefix("/api/suites/").strip("/")
                if not suite_id:
                    self.send_error_json("suite_id is required", HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(self.store.update_test_suite(suite_id, payload))
                return
            self.send_error_json("not found", HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_error_json(str(error), HTTPStatus.BAD_REQUEST)
        except KeyError as error:
            self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_error_json(str(error), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self._gate_authorized(parsed):
            self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
            return
        # 删除安装包台账行(磁盘文件与执行历史不动)
        if parsed.path.startswith("/api/apks/"):
            apk_id = parsed.path.removeprefix("/api/apks/").strip("/")
            if not apk_id:
                self.send_error_json("apk_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.delete_apk(apk_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        # 套件成员移除:支持 ?case_id= 单删,或 body {case_id}/{case_ids} 批量删
        if parsed.path.startswith("/api/suites/") and parsed.path.endswith("/cases"):
            suite_id = parsed.path.removeprefix("/api/suites/").removesuffix("/cases").strip("/")
            query = parse_qs(parsed.query)
            case_ids = query.get("case_id", [])
            try:
                if not case_ids and int(self.headers.get("Content-Length", "0")) > 0:
                    body = self.read_json()
                    case_ids = body.get("case_ids")
                    if case_ids is None:
                        case_ids = body.get("case_id")
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error_json(str(error), HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.remove_suite_cases(suite_id, case_ids))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/suites/"):
            suite_id = parsed.path.removeprefix("/api/suites/").strip("/")
            if not suite_id:
                self.send_error_json("suite_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.delete_test_suite(suite_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/case-defs/"):
            case_id = unquote(parsed.path.removeprefix("/api/case-defs/").strip("/"))  # unquote:中文编号(aw-xxx)路径解码;ASCII 编号不受影响
            if not case_id:
                self.send_error_json("case_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.delete_case_def(case_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/modules/"):
            # unquote(2026-10-16):同 PUT,中文/空格 slug 删除报「模块不存在」的根因
            slug = unquote(parsed.path.removeprefix("/api/modules/").strip("/"))
            if not slug:
                self.send_error_json("slug is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.delete_module(slug))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            except ValueError as error:  # 有用例引用,拒绝删除
                self.send_error_json(str(error), HTTPStatus.BAD_REQUEST)
            return
        if parsed.path.startswith("/api/pipeline-configs/"):
            config_id = parsed.path.removeprefix("/api/pipeline-configs/").strip("/")
            if not config_id:
                self.send_error_json("config_id is required", HTTPStatus.BAD_REQUEST)
                return
            try:
                self.send_json(self.store.delete_pipeline_config(config_id))
            except KeyError as error:
                self.send_error_json(str(error), HTTPStatus.NOT_FOUND)
            return
        self.send_error_json("not found", HTTPStatus.NOT_FOUND)


def create_server(host: str = "127.0.0.1", port: int = 8080, database_path: str | Path = DEFAULT_DATABASE) -> ThreadingHTTPServer:
    store = DashboardStore(database_path)

    class BoundDashboardHandler(DashboardHandler):
        pass

    BoundDashboardHandler.store = store
    return ThreadingHTTPServer((host, port), BoundDashboardHandler)


# ==================== 机器监控:Prometheus 只读代理(部署计划 v3 §7.5,设计稿 docs/机器监控页设计.md §3.3,档案 docs/agent模块档案/监控.md) ====================
# 独立 section:仅本段常量/函数 + do_GET 内 /api/metrics/* 路由,不依赖、不改其他模块代码。
# 部署前提:dashboard 与 Prometheus 同机(167;9090 仅本机监听)。instance 取值来自 scrape 配置
# (tools/provision/monitoring-infra.md):167=127.0.0.1:9100,176=192.168.12.176:9100。
import urllib.error
import urllib.parse
import urllib.request

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
_PROM_TIMEOUT_SECONDS = 5
# 机器白名单:machine_id -> {instance: node_exporter 标签精确值, role: 页面角色};
# METRICS_MACHINES 环境变量可覆盖(JSON,同形),机器清单不做页面管理(设计稿 §3.3)
_METRICS_MACHINES_DEFAULT = {
    "167": {"instance": "127.0.0.1:9100", "role": "platform"},
    "176": {"instance": "192.168.12.176:9100", "role": "build"},
}
try:
    METRICS_MACHINES: dict[str, dict[str, str]] = (
        json.loads(os.environ["METRICS_MACHINES"]) if os.environ.get("METRICS_MACHINES")
        else json.loads(json.dumps(_METRICS_MACHINES_DEFAULT))
    )
except ValueError:
    METRICS_MACHINES = json.loads(json.dumps(_METRICS_MACHINES_DEFAULT))
# 走势:近 15 分钟、15s 步长=60 点(设计稿 §3.1);数据滞后阈值 30s(设计稿 §四)由前端按
# summary.timestamp 与本地时间差判定(主会话裁决 2026-09-21),本常量仅作两端共享口径参考
METRICS_WINDOW_SECONDS = 900
METRICS_STEP_SECONDS = 15
METRICS_STALE_SECONDS = 30
# history 可选指标:只做有走势意义的(磁盘容量型不画走势、负载三值在卡头,设计稿 §2.2)
METRICS_HISTORY_KINDS = ("cpu", "mem", "net_up", "net_down")


class MetricsBadRequest(ValueError):
    """请求参数不合法(machine 不在白名单等)→ 400。"""


class MetricsUnavailable(RuntimeError):
    """Prometheus 服务本身不可达/响应异常 → 503,前端整页显示"监控组件未就绪"。"""


def _metrics_promql(kind: str, instance: str) -> str:
    """白名单 PromQL 模板(instance 精确匹配,值只来自白名单,无注入面)。

    返回口径:cpu=占用率 %;mem_used/mem_total=字节;disks=按 device+挂载点字节(同设备去重);
    net_up/net_down=字节/秒;load1/5/15=原值;uptime_s=秒;cpu_cores=核数;cpu_model=型号字符串(textfile 静态)。"""
    inst = f'instance="{instance}"'
    if kind == "cpu":
        return f'100 - avg(rate(node_cpu_seconds_total{{{inst},mode="idle"}}[5m])) * 100'
    if kind == "cpu_cores":
        return f'count(count(node_cpu_seconds_total{{{inst},mode="idle"}}) by (cpu))'
    if kind == "cpu_model":  # 型号来自两机 textfile collector 静态指标(monitoring-infra.md 勘误 v1.3),未布的机器为 None
        return f'node_cpu_model_info{{{inst}}}'
    if kind == "mem_used":
        return f'node_memory_MemTotal_bytes{{{inst}}} - node_memory_MemAvailable_bytes{{{inst}}}'
    if kind == "mem_total":
        return f'node_memory_MemTotal_bytes{{{inst}}}'
    if kind in ("mem", "mem_percent"):  # "mem"=走势口径,占用率 %
        return (
            f'(1 - node_memory_MemAvailable_bytes{{{inst}}} '
            f'/ node_memory_MemTotal_bytes{{{inst}}}) * 100'
        )
    if kind == "disk_avail":
        return f'node_filesystem_avail_bytes{{{inst},mountpoint=~"/|/opt",fstype!~"tmpfs|overlay"}}'
    if kind == "disk_size":
        return f'node_filesystem_size_bytes{{{inst},mountpoint=~"/|/opt",fstype!~"tmpfs|overlay"}}'
    if kind == "net_up":
        return f'sum(rate(node_network_transmit_bytes_total{{{inst},device!="lo"}}[5m]))'
    if kind == "net_down":
        return f'sum(rate(node_network_receive_bytes_total{{{inst},device!="lo"}}[5m]))'
    if kind == "load1":
        return f'node_load1{{{inst}}}'
    if kind == "load5":
        return f'node_load5{{{inst}}}'
    if kind == "load15":
        return f'node_load15{{{inst}}}'
    if kind == "uptime_s":
        return f'time() - node_boot_time_seconds{{{inst}}}'
    raise MetricsBadRequest(f"未知指标类型: {kind}")


def _prom_api(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """调 Prometheus /api/v1/*;网络/HTTP/状态异常一律归一为 MetricsUnavailable(503 降级)。"""
    url = f"{PROMETHEUS_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_PROM_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise MetricsUnavailable(f"Prometheus({PROMETHEUS_URL})不可达: {error}") from error
    if body.get("status") != "success":
        raise MetricsUnavailable(f"Prometheus 返回异常状态: {body.get('status')}")
    return body.get("data") or {}


def _prom_instant(promql: str) -> float | None:
    """即时查询取唯一序列当前值;无数据(exporter 缺该指标)返回 None,前端显示 '-'。"""
    result = (_prom_api("/api/v1/query", {"query": promql}).get("result") or [])
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _prom_instant_map(promql: str, labels: str | tuple[str, ...]) -> dict[Any, float]:
    """即时查询按 label 拆多序列 → {label值: 值};labels 传元组时键为元组(磁盘按 device+挂载点拆用)。"""
    result = (_prom_api("/api/v1/query", {"query": promql}).get("result") or [])
    out: dict[Any, float] = {}
    for item in result:
        metric = item.get("metric") or {}
        if isinstance(labels, tuple):
            key = tuple(metric.get(name) for name in labels)
            if any(part is None for part in key):
                continue
        else:
            key = metric.get(labels)
            if key is None:
                continue
        try:
            value = float(item["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        out[key] = value
    return out


def _prom_matrix(promql: str, start: float, end: float, step_seconds: int) -> list[list[float]]:
    """区间查询取第一条序列 [[unix_ts, value], ...];无数据返回空表。"""
    params = {"query": promql, "start": start, "end": end, "step": step_seconds}
    result = (_prom_api("/api/v1/query_range", params).get("result") or [])
    if not result:
        return []
    series: list[list[float]] = []
    for point in result[0].get("values") or []:
        try:
            series.append([float(point[0]), float(point[1])])
        except (IndexError, TypeError, ValueError):
            continue
    return series


def metrics_machine_list() -> list[dict[str, str]]:
    """机器清单(来自后端白名单配置,不做页面增删管理——设计稿 §3.3)。"""
    return [
        {"machine_id": machine_id, "instance": cfg["instance"], "role": cfg["role"]}
        for machine_id, cfg in METRICS_MACHINES.items()
    ]


def _metrics_resolve_machine(query: dict[str, list[str]]) -> tuple[str, dict[str, str]]:
    values = query.get("machine") or []
    key = (values[0] if values else "").strip()
    if key not in METRICS_MACHINES:
        raise MetricsBadRequest(f"machine 参数必填且必须是 {'/'.join(METRICS_MACHINES)} 之一")
    return key, METRICS_MACHINES[key]


def _collect_machine(instance: str) -> dict[str, Any]:
    """单机一轮即时查询(设计稿 §3.3 字段口径,2026-09-21 契约扩展:mem/disks 带 _bytes
    后缀原值+percent,disks 按 device 去重);Prometheus 不可达抛 MetricsUnavailable。"""
    def val(kind: str) -> float | None:
        return _prom_instant(_metrics_promql(kind, instance))

    cpu = val("cpu")
    mem_used = val("mem_used")
    mem_total = val("mem_total")
    net_up = val("net_up")
    net_down = val("net_down")
    up_raw = _prom_instant(f'up{{instance="{instance}"}}')
    # 判定"该机可达":target up=1,或任一核心指标有值(Prometheus 刚装好时 up 也查不到,靠指标兜底)
    reached = bool(up_raw == 1) or any(
        value is not None for value in (cpu, mem_used, mem_total, net_up, net_down)
    )
    # 磁盘按 (device, mountpoint) 取值:同设备多挂载点(/ 与 /opt 同盘,两台机现状)只保留
    # 一条,优先根分区;不同设备才多行——渲染跟着数据走(设计稿 §2.2 主会话裁决),不硬凑双行
    avail_map = _prom_instant_map(_metrics_promql("disk_avail", instance), ("device", "mountpoint"))
    size_map = _prom_instant_map(_metrics_promql("disk_size", instance), ("device", "mountpoint"))
    mounts_by_device: dict[str, set[str]] = {}
    for device, mountpoint in set(avail_map) | set(size_map):
        mounts_by_device.setdefault(device, set()).add(mountpoint)
    disks: list[dict[str, Any]] = []
    for device in sorted(mounts_by_device):
        mount = "/" if "/" in mounts_by_device[device] else sorted(mounts_by_device[device])[0]
        total = size_map.get((device, mount))
        avail = avail_map.get((device, mount))
        used = (total - avail) if (total is not None and avail is not None) else None
        percent = round(used / total * 100, 1) if (used is not None and total) else None
        disks.append({"mount": mount, "used_bytes": used, "total_bytes": total, "percent": percent})
    disks.sort(key=lambda row: row["mount"])
    # CPU 型号:textfile 静态指标按 model label 取值(未布 textfile 的机器为 None,前端不显示型号段)
    model_map = _prom_instant_map(_metrics_promql("cpu_model", instance), "model")
    cpu_model = next(iter(model_map), None)
    return {
        "up_reached": reached,
        "cpu_percent": cpu,
        "cpu_cores": val("cpu_cores"),
        "cpu_model": cpu_model,
        "mem_percent": val("mem_percent"),
        "mem_used_bytes": mem_used,
        "mem_total_bytes": mem_total,
        "disks": disks,
        "net_up": net_up,
        "net_down": net_down,
        "load": [val("load1"), val("load5"), val("load15")],
        "uptime_s": val("uptime_s"),
    }


def metrics_summary(query: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """两机首屏指标一次全量返回(设计稿 §3.2:一次 fetch 渲染两卡)。

    无状态:每轮即时查询,不做进程内缓存/滞后状态机——"数据滞后"黄态由前端按
    summary.timestamp 与本地时间差 >30s 判定(主会话裁决 2026-09-21)。
    Prometheus 服务不可达 → MetricsUnavailable(路由层 503 整页提示);单机 exporter
    失联 → 该机 up=false、指标为 null,另一机照常——单机故障不拖垮整页(设计稿 §四硬要求)。"""
    machines: list[dict[str, Any]] = []
    for machine_id, cfg in METRICS_MACHINES.items():
        data = _collect_machine(cfg["instance"])  # MetricsUnavailable 直接上抛 → 503
        reached = bool(data.pop("up_reached"))
        entry: dict[str, Any] = {
            "machine_id": machine_id,
            "role": cfg["role"],
            "instance": cfg["instance"],
            "timestamp": time.time(),  # 本条数据产出时刻,前端据此判滞后
            "up": reached,             # target up=1 或任一核心指标有值(兜底 up 序列未生成)
        }
        entry.update(data)  # 失联机各指标为 null、disks=[],前端按 up 分支渲染
        machines.append(entry)
    return {"machines": machines}


def metrics_history(query: dict[str, list[str]]) -> dict[str, Any]:
    """单机走势:range 目前仅 15m(P1 再扩 1h/6h),step 15s=60 点;metric 缺省返回全部走势类。"""
    machine_id, cfg = _metrics_resolve_machine(query)
    metric = ((query.get("metric") or [""])[0]).strip()
    kinds = [metric] if metric else list(METRICS_HISTORY_KINDS)
    for kind in kinds:
        if kind not in METRICS_HISTORY_KINDS:
            raise MetricsBadRequest(f"metric 必须是 {'/'.join(METRICS_HISTORY_KINDS)} 之一")
    range_text = ((query.get("range") or ["15m"])[0]).strip() or "15m"
    if range_text != "15m":
        raise MetricsBadRequest("range 目前仅支持 15m(1h/6h 在设计稿 P1 清单)")
    end = time.time()
    start = end - METRICS_WINDOW_SECONDS
    return {
        "machine_id": machine_id,
        "instance": cfg["instance"],
        "range_seconds": METRICS_WINDOW_SECONDS,
        "step_seconds": METRICS_STEP_SECONDS,
        "metrics": {
            kind: _prom_matrix(_metrics_promql(kind, cfg["instance"]), start, end, METRICS_STEP_SECONDS)
            for kind in kinds
        },
    }

# ==================== 机器监控 section 结束 ====================

# ==================== 机器监控·Claude 任务视窗(2026-10-15):每机 Claude 活动只读枚举 ====================
# 独立 section:采集/通道/缓存全在 dashboard/claude_tasks.py,本段只做装配;
# 任一机失败 → 该机 online=false 由前端降级,绝不拖垮整页(与机器监控 §四同口径);
# 纯只读:远端只跑 stdin 下发的枚举脚本,不写文件、不动进程、不装东西。
import claude_tasks  # noqa: E402  (与 jenkins_client 同款:直跑形态由头部 sys.path 补齐)


# ==================== 巡检流程总览:巡检状态快照只读代理(设计稿 docs/设计稿/巡检流程监控-设计稿.html v0.6,口径权威 docs/agent模块档案/巡检机制.md) ====================
# 独立 section:仅本段常量/函数 + do_GET 内 /api/patrol/* 路由 + main() 一行线程启动,不依赖、不改其他模块代码。
# 数据源:巡检 agent 每轮覆盖写 last_status.json(.167 cron 每日 08:41/19:43,巡检机制.md §6);
# 本端点只读原样返回,好坏判定/快照超期判定归前端(与机器监控"卡态前端判定"同口径);
# 浏览器通道走登录闸,无需 MACHINE_EXEMPT。快照缺失/不可读 → 404,前端显"巡检数据缺失"+重试(规范 4.7/6.9)。
PATROL_STATUS_FILE = os.environ.get("PATROL_STATUS_FILE", "/opt/patrol/last_status.json")


class PatrolStatusMissing(RuntimeError):
    """巡检状态快照不存在/不可读 → 404(前端只区分"有快照/没快照"两态)。"""


def patrol_status() -> dict:
    """读巡检状态快照原样返回;缺失与损坏同归 PatrolStatusMissing(404)。"""
    path = Path(PATROL_STATUS_FILE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PatrolStatusMissing(f"巡检状态文件不可读: {error}") from error


# ==================== 巡检快照入库(2026-10-12 增):每轮快照留档,支撑回溯/趋势 ====================
# 平台侧采集,巡检脚本零改动:后台线程每 60s 读快照文件,ts 唯一键 INSERT OR IGNORE 幂等;
# 快照文件虽被巡检每轮覆盖,历史行已留档。查询走 GET /api/patrol/history(登录闸内)。
PATROL_INGEST_INTERVAL_S = 60.0


def _patrol_db_connect(db_path: str | Path) -> sqlite3.Connection:
    """section 自持连接(不走 DashboardStore,保隔离);建表语句幂等。"""
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS patrol_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts TEXT NOT NULL UNIQUE,"
        "host TEXT NOT NULL DEFAULT '',"
        "payload TEXT NOT NULL,"  # 快照 JSON 全文(原样)
        "ingested_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    return connection


def ingest_patrol_snapshot(db_path: str | Path) -> bool:
    """当前快照入库(ts 幂等);新入一条返回 True。失败吞掉记 stderr,不影响服务主链路。"""
    try:
        snapshot = patrol_status()
    except PatrolStatusMissing:
        return False
    connection = _patrol_db_connect(db_path)
    try:
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO patrol_snapshots(ts, host, payload) VALUES(?,?,?)",
                (str(snapshot.get("ts", "")), str(snapshot.get("host", "")),
                 json.dumps(snapshot, ensure_ascii=False)),
            )
            return connection.total_changes > 0
    except sqlite3.Error as error:
        print(f"[patrol-ingest] 快照入库失败: {error}", file=sys.stderr, flush=True)
        return False
    finally:
        connection.close()


def patrol_history(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """最近 N 轮快照(新→旧);payload 解为对象随行返回。"""
    connection = _patrol_db_connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        with connection:
            rows = connection.execute(
                "SELECT id, ts, host, payload, ingested_at FROM patrol_snapshots"
                " ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        connection.close()
    return [
        {"id": row["id"], "ts": row["ts"], "host": row["host"],
         "ingested_at": row["ingested_at"], "snapshot": json.loads(row["payload"])}
        for row in rows
    ]


def _patrol_ingest_loop(db_path: str | Path) -> None:
    while True:
        ingest_patrol_snapshot(db_path)
        time.sleep(PATROL_INGEST_INTERVAL_S)


def patrol_ingest_start(db_path: str | Path) -> None:
    """main() 唯一 hook:先同步入一次(重启后立即有行),再起守护线程持续采集。"""
    import threading  # 局部导入,保 section 自包含(同文件 mid-file 导入先例)

    ingest_patrol_snapshot(db_path)
    threading.Thread(target=_patrol_ingest_loop, args=(db_path,), daemon=True,
                     name="patrol-ingest").start()

# ==================== 巡检流程总览 section 结束 ====================

# 页面独立维护在 dashboard/page.html(四页签:会话/包管理/用例历史/验证码工具)
DASHBOARD_HTML = (Path(__file__).resolve().parent / "page.html").read_text(encoding="utf-8")
# 页面本地 JS 依赖目录(SheetJS 等),经 /vendor/<文件名> 提供,仅限本目录下文件
VENDOR_DIR = (Path(__file__).resolve().parent / "vendor").resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="UIAutotest test history dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.database)
    patrol_ingest_start(args.database)  # 巡检快照入库线程(见巡检 section:60s 一采,ts 幂等)
    print(f"Dashboard listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
