"""176 构建机巡检脚本(巡检机制 v1,2026-10-09 上线)。

四项只读检查:remoting agent 在线 / 根分区磁盘水位 / swan-build 产物增长 /
git 源仓库拉取探活(git ls-remote,凭据经 askpass 现读 .env.local,不进命令行)。
全绿不发企微(主会话拍板口径);有告警经 wecom-cli 发测试部群,
通道与打包失败通知一致(HOME=WECOM_CLI_HOME 隔离,凭据 autotest 属主,
jks 身份读不了 credentials.enc,故 cron 以 sudo -u autotest 运行本脚本)。
每次运行落 status.json 供 .167 巡检侧/人工查看(跨机自动合并留 v2)。
v1 只报不修:agent 拉起/磁盘清理等自愈动作不在本脚本职责内。

cron(jks 用户 crontab,时刻错开 .167 侧 08:41/19:43;输出重定向必须丢 /dev/null,
jks 的 cron shell 无权在 patrol 目录建日志文件,落盘由本脚本 log() 自行完成):
  47 8,19 * * * sudo -n -u autotest /usr/bin/python3 /opt/swan-build/patrol/patrol_176.py > /dev/null 2>&1

用法:
  python3 patrol_176.py               # 常规运行(有告警才发企微)
  python3 patrol_176.py --dry-run     # 只落状态不发企微(冒烟/自测)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

PATROL_DIR = Path("/opt/swan-build/patrol")
STATUS_FILE = PATROL_DIR / "status.json"
LOG_FILE = PATROL_DIR / "patrol.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 超过即保留后半截,防 cron 追加无限增长
APKS_DIR = Path("/opt/swan-build/apks")
DISK_WARN_PERCENT = 80
DISK_CRIT_PERCENT = 90
APK_STALE_HOURS = 48  # 对齐每日 04:17 打包节奏(时间触发,与有无 push 无关)
SRC_DIR = Path("/opt/swan-build/src")  # 打包工作区(构建成功后默认即删,存在时可读其 remote)
ENV_FILE = Path("/opt/uiautotest/.env.local")  # git 探活凭据同源(autotest 可读,部署实测)
BUILD_SCRIPT = Path("/opt/uiautotest/scripts/build_swan_apk.py")  # REPO_URL 定义行做 URL 兜底源
GIT_PROBE_TIMEOUT = 30  # git ls-remote 超时秒数(巡检机制.md §10 用户拍板口径)
WECOM_CLI_PATH = os.environ.get("WECOM_CLI_PATH", "/usr/local/bin/wecom-cli")
WECOM_CLI_HOME = os.environ.get("WECOM_CLI_HOME", "/opt/swan-build/wecom-home")
WECOM_CHAT_ID = os.environ.get("WECOM_CHAT_ID", "wr8gXkCwAAZGKjkN_P-0r134RU6QaoAg")

LEVEL_ORDER = {"ok": 0, "warn": 1, "crit": 2}


def check_agent() -> dict:
    """remoting agent 判活:裸进程跑的 java -jar remoting.jar(非 systemd 单元),
    pgrep -f remoting.jar 有输出即在线;多 pid 取首个做展示。"""
    result = subprocess.run(
        ["pgrep", "-f", "remoting.jar"], capture_output=True, text=True, timeout=10)
    pids = result.stdout.split()
    alive = result.returncode == 0 and bool(pids)
    return {
        "name": "remoting_agent",
        "level": "ok" if alive else "crit",
        "detail": f"pid={pids[0]}" if alive else "无 remoting.jar 进程",
    }


def check_disk() -> dict:
    """根分区水位:.176 上 /opt/swan-build 与系统同盘,看 / 即可。"""
    usage = shutil.disk_usage("/")
    percent = round(usage.used * 100 / usage.total, 1)  # usage 无 percent 字段,自行算
    if percent >= DISK_CRIT_PERCENT:
        level = "crit"
    elif percent >= DISK_WARN_PERCENT:
        level = "warn"
    else:
        level = "ok"
    return {
        "name": "disk_root",
        "level": level,
        "detail": f"根分区使用 {percent}%(阈值 告警{DISK_WARN_PERCENT}%/严重{DISK_CRIT_PERCENT}%)",
    }


def check_apks() -> dict:
    """产物增长:最新 apk 距今超 48h 报警。每日打包是定时触发,正常每天必出
    新 formal+test 两包;注意本检查罩不住"上传平台失败"类故障(文件照常落
    .176),该盲区由 .167 侧 apks 登记vs归档一致性检查互补。"""
    apks = sorted(APKS_DIR.glob("*.apk"), key=lambda p: p.stat().st_mtime)
    if not apks:
        return {"name": "apk_growth", "level": "warn", "detail": "apks 目录无产物"}
    newest = apks[-1]
    age_hours = (time.time() - newest.stat().st_mtime) / 3600
    level = "warn" if age_hours > APK_STALE_HOURS else "ok"
    return {
        "name": "apk_growth",
        "level": level,
        "detail": f"最新产物 {newest.name} 距今 {age_hours:.1f}h(阈值 {APK_STALE_HOURS}h)",
    }


def redact(text: str, secrets: list[str]) -> str:
    """日志脱敏:凭据字面量替换 ***(探活 stderr 可能回显敏感串)。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def load_git_secret() -> str:
    """读 GITLAB_BOT_PASSWORD 值仅用于日志脱敏;读不到返回空串,不阻断探活。"""
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("GITLAB_BOT_PASSWORD="):
            return line.split("=", 1)[1].strip()
    return ""


def resolve_repo_url() -> str:
    """源仓库 URL 实测解析,不硬编码:优先读打包工作区实际 remote(与
    build_swan_apk.py clone 的仓严格一致);工作区构建成功后默认即删,退而读
    部署版打包脚本的 REPO_URL 定义行;两处皆不可得抛 RuntimeError(其本身
    即构成巡检发现,交上游排查)。"""
    result = subprocess.run(
        ["git", "-C", str(SRC_DIR), "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=10)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    if BUILD_SCRIPT.is_file():
        match = re.search(r'^REPO_URL\s*=\s*"([^"]+)"',
                          BUILD_SCRIPT.read_text(encoding="utf-8", errors="replace"), re.M)
        if match:
            return match.group(1)
    raise RuntimeError("无法确定源仓库 URL(工作区不存在且部署版打包脚本无 REPO_URL)")


def ensure_askpass() -> Path:
    """生成 git 探活 askpass 助手(每次运行重写):本文件不含任何机密,凭据在
    git 调用时刻由助手从 .env.local 现读——密码不进命令行(ps 不可见)、不进
    环境变量、不进任何日志。"""
    helper = PATROL_DIR / "git_askpass.sh"
    helper.write_text("\n".join([
        "#!/bin/sh",
        "# git 探活 askpass:凭据现读 /opt/uiautotest/.env.local,本文件不含机密",
        "case \"$1\" in",
        "  *[Uu]sername*) grep -E '^GITLAB_BOT_USER=' /opt/uiautotest/.env.local | head -1 | cut -d= -f2- ;;",
        "  *) grep -E '^GITLAB_BOT_PASSWORD=' /opt/uiautotest/.env.local | head -1 | cut -d= -f2- ;;",
        "esac",
    ]) + "\n", encoding="utf-8")
    helper.chmod(0o700)
    return helper


def check_git_probe() -> dict:
    """git 源仓库拉取探活(第 4 查,L3 只报不修):git ls-remote <url> HEAD,
    超时 30s。验证"拉得到源码"本身——GitLab 服务端 5xx 曾致拉码失败只能靠
    Jenkins 连败间接暴露,本查把"拉不到代码"与"编译问题"区分开。失败三分类:
    网络或服务端(超时/域名解析/HTTP 5xx)/凭据(authentication/permission
    denied/40x)/其他(stderr 摘要);stderr 脱敏截断后进 detail。"""
    name = "git_src_probe"
    try:
        repo_url = resolve_repo_url()
    except RuntimeError as error:
        return {"name": name, "level": "warn", "detail": str(error)}
    env = dict(os.environ)
    env["GIT_ASKPASS"] = str(ensure_askpass())
    env["GIT_TERMINAL_PROMPT"] = "0"  # 禁交互提示,失败直接走非零退出
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_PROBE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return {"name": name, "level": "warn",
                "detail": f"拉取失败-网络或服务端:ls-remote 超时 {GIT_PROBE_TIMEOUT}s({repo_url})"}
    elapsed = time.monotonic() - started
    if result.returncode == 0 and result.stdout.strip():
        head_sha = result.stdout.split()[0]
        return {"name": name, "level": "ok",
                "detail": f"ls-remote HEAD={head_sha[:12]} 耗时 {elapsed:.1f}s"}
    stderr = (result.stderr or "").strip()
    lowered = stderr.lower()
    if any(marker in lowered for marker in (
            "timed out", "could not resolve host", "failed to connect", "connection refused",
            "connection reset", "network is unreachable", "http 5", "returned error: 5")):
        failure_class = "拉取失败-网络或服务端"
    elif any(marker in lowered for marker in (
            "authentication", "permission denied", "could not read username",
            "401", "403", "invalid credentials")):
        failure_class = "拉取失败-凭据"
    else:
        failure_class = "拉取失败-其他"
    detail = f"{failure_class}:{redact(stderr, [load_git_secret()])[:160]}({repo_url})"
    return {"name": name, "level": "warn", "detail": detail}


def send_wecom(alarms: list[dict]) -> None:
    """告警发测试部群;调用方式与打包失败通知一致(HOME 隔离)。
    发送失败只打日志,不影响巡检主流程与 status.json。"""
    lines = "\n".join(f"> {a['level'].upper()} {a['name']}: {a['detail']}" for a in alarms)
    content = (f'<font color="warning">**【.176 巡检告警】**</font>\n'
               f'{lines}\n> 时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    try:
        result = subprocess.run(
            [WECOM_CLI_PATH, "message", "aibot", "send", "--chat-id", WECOM_CHAT_ID,
             "--msg-type", "markdown", "--markdown",
             json.dumps({"content": content}, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, env={**os.environ, "HOME": WECOM_CLI_HOME})
        print(f"企微告警:exit={result.returncode} "
              f"{(result.stdout or result.stderr or '').strip()[:200]}")
    except Exception as error:
        print(f"企微告警异常:{error}")


def log(msg: str) -> None:
    """巡检日志由脚本自己追加:patrol 目录属 autotest,jks 的 cron shell 无权在
    该目录建文件(cron 行的 >> 重定向会 Permission denied),故 cron 行只做
    > /dev/null,落盘全走本函数;print 仅供手动直跑看输出。"""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line)


def rotate_log() -> None:
    """patrol.log 超限保留后半截(cron 每次追加,需防无限增长)。"""
    if LOG_FILE.is_file() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        LOG_FILE.write_text(text[len(text) // 2:], encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry_run = "--dry-run" in sys.argv
    PATROL_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log()
    try:
        checks = [check_agent(), check_disk(), check_apks(), check_git_probe()]
        alarms = [c for c in checks if c["level"] != "ok"]
        overall = "ok"
        for check in checks:
            if LEVEL_ORDER[check["level"]] > LEVEL_ORDER[overall]:
                overall = check["level"]
        status = {
            "host": "176",
            "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "overall": overall,
            "checks": checks,
            "alarms": [check["name"] for check in alarms],
        }
        STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        for check in checks:
            log(f"[{check['level'].upper():4}] {check['name']}: {check['detail']}")
        log(f"overall={overall} alarms={status['alarms']} status={STATUS_FILE}")
        if alarms and dry_run:
            log("dry-run:跳过企微发送")
        elif alarms:
            send_wecom(alarms)
    except Exception:
        # 任何未预期异常都落日志防静默,退出码 1 便于 cron 层面观察
        log(f"巡检异常:{traceback.format_exc().strip()}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
