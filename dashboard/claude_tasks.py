"""机器监控·Claude 任务视窗采集模块(2026-10-15,机器监控页新视窗)。

只读枚举每台机器上的 Claude 活动,产出 JSON:
- processes: Claude 进程(=任务窗口):pid/启动时间/工作目录/内存
- sessions:  ~/.claude/projects/**/*.jsonl 最近 10 分钟有写入的活跃会话;
             只读文件头/尾各 4KB 解析,绝不整读大文件
- tmux:      tmux 会话名(Linux 两台;Windows 无)

通道:dashboard 服务端(.167)对本机直接执行,远端机 SSH 执行 `<python> -`
(脚本走 stdin,免引号地狱;脚本纯 ASCII,防 Windows 远端源码码页坑)。
纯只读:不写任何远端文件、不动任何进程、不装任何东西。

失败面:任一机器失败由编排层归一为该机 online=false,不拖垮整页;
机内单段(processes/sessions/tmux)失败由脚本兜成空表+notes,不拖垮该机数据。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# ==================== 机器清单与通道配置 ====================
# 写法风格对齐 server.py 的 _METRICS_MACHINES_DEFAULT:白名单写死 + 环境变量整体覆盖,不做页面管理。
# transport=local 表示 dashboard 本机直接执行(167);ssh 走 OpenSSH BatchMode 免密。
# 229 工作站按用户拍板(2026-10-15)不进默认清单(本机即 229,无需面板);需要时用
# CLAUDE_TASKS_MACHINES 环境变量按同形 JSON 加回。
_CLAUDE_TASKS_MACHINES_DEFAULT: dict[str, dict[str, str]] = {
    # 167 走 ssh(jks@127.0.0.1):服务身份 autotest 读不了 jks 家目录,local 通道采不到
    # 其 .claude 会话(2026-10-15);ssh 到 jks 本机后脚本以 jks 身份读自家 home,与 176 同模式。
    "167": {"transport": "ssh", "host": "jks@127.0.0.1", "python_cmd": "python3 -", "role": "platform"},
    "176": {"transport": "ssh", "host": "jks@192.168.12.176", "python_cmd": "python3 -", "role": "build"},
}
try:
    CLAUDE_TASKS_MACHINES: dict[str, dict[str, str]] = (
        json.loads(os.environ["CLAUDE_TASKS_MACHINES"]) if os.environ.get("CLAUDE_TASKS_MACHINES")
        else json.loads(json.dumps(_CLAUDE_TASKS_MACHINES_DEFAULT))
    )
except ValueError:
    CLAUDE_TASKS_MACHINES = json.loads(json.dumps(_CLAUDE_TASKS_MACHINES_DEFAULT))

CLAUDE_TASKS_TIMEOUT_SECONDS = 6.0   # 单机采集超时(任务口径 ≤6s;SSH ConnectTimeout 另计在远端连接阶段)
CLAUDE_TASKS_CACHE_SECONDS = 10.0    # 结果缓存,防轮询打爆远端
_SSH_FLAGS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
              "-o", "StrictHostKeyChecking=accept-new")


class ClaudeTasksMachineError(RuntimeError):
    """单机采集失败(超时/SSH 失败/输出非约定 JSON)→ 该机 online=false。"""


# ==================== 采集脚本(随 stdin 下发,纯 ASCII) ====================
# 事实依据(2026-10-15 本机实测):主会话=projects/<proj>/<uuid>.jsonl(isSidechain=false);
# 子代理=projects/<proj>/<uuid>/subagents/agent-*.jsonl(isSidechain=true,带 agentId);
# 会话标题取 agentName > aiTitle > 首条真实 user 文本(系统包裹行不算)。
CLAUDE_TASKS_SCRIPT = r'''
import base64, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

ACTIVE_WINDOW_SECONDS = 600   # mtime 10 分钟内 = 活跃会话
IDLE_SHOW_SECONDS = 86400      # 24h 内的空闲会话仍列出(标"空闲"),更久不显示——空闲但活着要与无任务可区分(2026-10-15 用户反馈)
MAX_SESSIONS = 50
HEAD_BYTES = 4096
TAIL_BYTES = 4096
TITLE_MAX = 80

def _read_head(path, nbytes):
    try:
        with open(path, "rb") as fh:
            return fh.read(nbytes)
    except OSError:
        return b""

def _read_tail(path, nbytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > nbytes:
                fh.seek(size - nbytes)
            return fh.read(nbytes), size > nbytes
    except OSError:
        return b"", False

def _json_lines(raw, drop_first_partial):
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    if drop_first_partial and lines:
        lines = lines[1:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out

def _block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    return ""

_ZH_TITLE_RULES = (
    # (首问前缀, 中文描述):已知英文系统引导映射(2026-10-15 用户反馈:主会话描述须中文)。
    # 中文以 UTF-8 真字符存放:采集脚本走 stdin 传给 .167/.176 本地 python(两端 UTF-8),
    # 229 Windows 通道已移除,不再需要纯 ASCII 约束。
    ("Read ~/leader/LEADER.md", "值守小领导"),
    ("This session is being continued", "续接会话"),
    ("你是 Swan 测试平台巡检 agent", "定时巡检"),
)

def _zh_rule_title(t):
    # 仅在命中已知引导词时返回中文名,否则 None(与 _zh_title 的"不重写"语义区分)
    if not isinstance(t, str):
        return None
    for prefix, zh in _ZH_TITLE_RULES:
        if t.startswith(prefix):
            return zh
    return None

def _zh_title(t):
    if not isinstance(t, str):
        return t
    for prefix, zh in _ZH_TITLE_RULES:
        if t.startswith(prefix):
            return zh
    return t

def _friendly_agent(agent_id):
    # agentId 形如 aclaude-mon-dev-<hex16>(前缀 a+类型/名,尾随机段);去尾随机段与
    # 首部 agent-/a 前缀得可读名。类型与名无法可靠切分,整段展示(如实口径)。
    if not isinstance(agent_id, str) or not agent_id:
        return None
    s = re.sub(r"-[0-9a-f]{12,}$", "", agent_id)
    if s.startswith("agent-"):
        s = s[len("agent-"):]
    if len(s) > 1 and s[0] == "a" and s[1].isalpha():
        s = s[1:]
    return s or None

def _parse_session(mtime, path, kind):
    info = {
        "session_id": path.stem, "kind": kind, "agent": None, "title": None,
        "cwd": None, "model": None, "first_event_at": None, "last_event_at": None,
        "mtime": round(mtime, 3),
    }
    head = _json_lines(_read_head(path, HEAD_BYTES), False)
    tail_raw, partial = _read_tail(path, TAIL_BYTES)
    tail = _json_lines(tail_raw, partial)
    first_prompt = None; ai_title = None; agent_name = None
    for d in head:
        t = d.get("type")
        if info["first_event_at"] is None and isinstance(d.get("timestamp"), str):
            info["first_event_at"] = d["timestamp"]
        if t == "user" and first_prompt is None and isinstance(d.get("message"), dict):
            txt = _block_text(d["message"].get("content")).strip()
            if txt and not txt.startswith(("<", "[Request", "Caveat:")):
                first_prompt = txt[:TITLE_MAX]
        if t == "ai-title" and ai_title is None and isinstance(d.get("aiTitle"), str):
            ai_title = d["aiTitle"]
        if t == "agent-name" and agent_name is None and isinstance(d.get("agentName"), str):
            agent_name = d["agentName"]
    for d in tail:
        if isinstance(d.get("cwd"), str):
            info["cwd"] = d["cwd"]
        if isinstance(d.get("timestamp"), str):
            info["last_event_at"] = d["timestamp"]
        if kind == "subagent" and info["agent"] is None:
            info["agent"] = _friendly_agent(d.get("agentId"))
        if d.get("type") == "assistant" and isinstance(d.get("message"), dict) \
                and isinstance(d["message"].get("model"), str):
            info["model"] = d["message"]["model"]
        if d.get("type") == "ai-title" and ai_title is None and isinstance(d.get("aiTitle"), str):
            ai_title = d["aiTitle"]
        if d.get("type") == "agent-name" and agent_name is None and isinstance(d.get("agentName"), str):
            agent_name = d["agentName"]
    if kind == "subagent":
        info["parent_id"] = path.parent.parent.name  # <父会话>/subagents/<file>.jsonl → 父会话 id(缩进展示依据)
        info["agent"] = info["agent"] or _friendly_agent(path.stem)
    info["title"] = _zh_rule_title(first_prompt) or agent_name or ai_title or _zh_title(first_prompt)  # 已知引导词的固定中文名优先,防英文 agentName 抢显示
    return info

def _iso_epoch(s):
    # ISO 时间戳 -> epoch 秒;空/解析失败返回 None
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

def _claude_procs():
    # 当前存活的 claude 进程 [(cwd, start_epoch)];/proc 不可用(如 Windows 测试环境)返回 None=调用方跳过存活过滤
    if not os.path.isdir("/proc"):
        return None
    try:
        btime = None
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime"):
                    btime = int(line.split()[1])
                    break
        if not btime:
            return None
        hz = os.sysconf("SC_CLK_TCK") or 100
        out = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/" + pid + "/comm") as f:
                    if f.read().strip() != "claude":
                        continue
                with open("/proc/" + pid + "/stat") as f:
                    st = f.read()
                starttick = int(st[st.rindex(")") + 2:].split()[19])
                out.append((os.readlink("/proc/" + pid + "/cwd"), btime + starttick / hz))
            except (OSError, ValueError, IndexError):
                continue
        return out
    except OSError:
        return None

def _sessions_active(base_dir=None, now=None, procs=None):
    # 存活过滤(任务 023,2026-10-16 用户反馈):只显示当下存活的会话,活跃/空闲都列;会话关闭即不再出现。
    # 判据:存在 cwd 同项目的 claude 进程,且会话首条消息时间落在其启动后一刻钟内
    # (同目录常驻进程不得给晚生会话背书,如定时巡检无头跑;子代理随父会话)。
    # procs=None 且 /proc 不可用(非 Linux)时退回 24h 兜底窗。
    base = Path(base_dir) if base_dir else Path.home() / ".claude" / "projects"
    ts = time.time() if now is None else now
    if procs is None:
        procs = _claude_procs()
    found = []
    if not base.is_dir():
        return []
    for proj in base.iterdir():
        try:
            if not proj.is_dir():
                continue
            for p in proj.glob("*.jsonl"):
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                found.append((mt, p, "main"))
            for p in proj.glob("*/subagents/*.jsonl"):
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                found.append((mt, p, "subagent"))
        except OSError:
            continue
    if procs:
        parsed = [(_parse_session(mt, p, kind), p, kind) for mt, p, kind in found]
        alive_main = set()
        for info, p, kind in parsed:
            if kind != "main":
                continue
            born = _iso_epoch(info.get("first_event_at"))
            if born is None:
                continue
            slug = p.parent.name
            if any(slug == cwd.replace("/", "-") and start - 5 <= born <= start + 900
                   for cwd, start in procs):
                alive_main.add(info["session_id"])
        def _alive(info, p, kind):
            if kind == "subagent":
                return p.parent.parent.name in alive_main  # 父会话活着,子代理记录就活着
            return info["session_id"] in alive_main
        sessions = [info for info, p, kind in parsed if _alive(info, p, kind)]
        sessions.sort(key=lambda i: i["mtime"], reverse=True)
        sessions = sessions[:MAX_SESSIONS]
        for info in sessions:
            info["active"] = (ts - info["mtime"]) <= ACTIVE_WINDOW_SECONDS
        return sessions
    if procs == []:
        return []  # 明确无任何 claude 进程:无存活会话
    found = [(mt, _p, _k) for (mt, _p, _k) in found if ts - mt <= IDLE_SHOW_SECONDS]  # 24h 兜底(非 Linux)
    found.sort(key=lambda row: row[0], reverse=True)
    sessions = [_parse_session(mt, p, kind) for mt, p, kind in found[:MAX_SESSIONS]]
    for info, (mt, _p, _kind) in zip(sessions, found):
        info["active"] = (ts - mt) <= ACTIVE_WINDOW_SECONDS
    return sessions

def _processes_linux():
    out = []
    try:
        hz = os.sysconf("SC_CLK_TCK") or 100
    except (ValueError, OSError, AttributeError):
        hz = 100
    try:
        pagesz = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        pagesz = 4096
    btime = 0
    try:
        with open("/proc/stat", "rb") as fh:
            for ln in fh.read().decode("utf-8", "replace").splitlines():
                if ln.startswith("btime "):
                    btime = int(ln.split()[1]); break
    except (OSError, ValueError, IndexError):
        pass
    try:
        pids = os.listdir("/proc")
    except OSError:
        return out
    for name in pids:
        if not name.isdigit():
            continue
        pd = "/proc/" + name
        try:
            with open(pd + "/comm", "rb") as fh:
                comm = fh.read().decode("utf-8", "replace").strip()
            with open(pd + "/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if not comm:
            continue
        args = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        lc = comm.lower()
        if not (lc in ("claude", "claude.exe")
                or (lc in ("node", "bun") and "claude-code" in args)):
            continue
        item = {"pid": int(name), "command": args[:200] or comm, "cwd": None,
                "started_at": None, "rss_bytes": None}
        try:
            with open(pd + "/stat", "rb") as fh:
                stat = fh.read().decode("utf-8", "replace")
            fields = stat[stat.rindex(")") + 2:].split()
            if btime:
                item["started_at"] = btime + int(fields[19]) / float(hz)
            item["rss_bytes"] = int(fields[21]) * pagesz
        except (OSError, ValueError, IndexError):
            pass
        try:
            item["cwd"] = os.readlink(pd + "/cwd")
        except OSError:
            pass
        out.append(item)
    out.sort(key=lambda it: it.get("started_at") or 0)
    return out

# PowerShell 探测用 -EncodedCommand(b64 UTF-16LE)下发:内层引号零转义,规避
# -Command 嵌套引号被 Windows 参数解析打碎的坑(实测 Name='claude.exe' 内层引号必炸)。
_PS_PROBE_PLAIN = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
    "$ErrorActionPreference = 'SilentlyContinue'\n"
    "Get-CimInstance Win32_Process -Filter \"Name='claude.exe' OR Name='claude'\" | "
    "ForEach-Object { \"`n{0}`t{1}`t{2}`t{3}\" -f $_.ProcessId,"
    " [int]((New-TimeSpan $_.CreationDate ([DateTime]::Now)).TotalSeconds),"
    " $_.WorkingSetSize, $_.CommandLine }\n"
)

def _processes_windows():
    out = []
    try:
        encoded = base64.b64encode(_PS_PROBE_PLAIN.encode("utf-16-le")).decode("ascii")
        cp = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return out
    now = time.time()
    for ln in cp.stdout.decode("utf-8", "replace").splitlines():
        parts = ln.split("\t", 3)
        if len(parts) != 4 or not parts[0].strip().isdigit():
            continue
        age_txt = parts[1].strip()
        rss_txt = parts[2].strip()
        age = int(age_txt) if age_txt.lstrip("-").isdigit() else None
        rss = int(rss_txt) if rss_txt.isdigit() else None
        out.append({
            "pid": int(parts[0]),
            "command": parts[3].strip()[:200],
            "cwd": None,
            "started_at": (now - age) if age is not None else None,
            "rss_bytes": rss,
        })
    out.sort(key=lambda it: it.get("started_at") or 0)
    return out

def _detect_processes():
    if os.name == "nt":
        return _processes_windows()
    return _processes_linux()

def _list_tmux():
    if os.name == "nt":
        return []
    try:
        cp = subprocess.run(["tmux", "list-sessions", "-F", "#S"],
                            capture_output=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode != 0:
        return []
    return [ln.strip() for ln in cp.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]

def _main():
    payload = {
        "ok": True, "collected_at": time.time(),
        "hostname": os.uname().nodename if os.name != "nt" else os.environ.get("COMPUTERNAME", ""),
        "processes": [], "sessions": [], "tmux": [], "notes": [],
    }
    for label, fn in (("processes", _detect_processes),
                      ("sessions", _sessions_active), ("tmux", _list_tmux)):
        try:
            payload[label] = fn()
        except Exception as exc:  # 单段失败不拖垮整机数据,notes 如实记录
            payload["notes"].append("%s: %s %s" % (label, type(exc).__name__, str(exc)[:120]))
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=True).encode("utf-8"))

if __name__ == "__main__":
    _main()
'''


# ==================== 单机执行 ====================
def _claude_tasks_run_machine(machine_id: str, cfg: dict[str, str]) -> dict[str, Any]:
    """单机采集:local=本机 python 执行脚本,ssh=脚本走 stdin 到远端 python。"""
    script = CLAUDE_TASKS_SCRIPT.encode("utf-8")
    if cfg.get("transport") == "local":
        args = [sys.executable, "-"]
    else:
        host = cfg.get("host", "").strip()
        if not host:
            raise ClaudeTasksMachineError(f"机器 {machine_id} 未配置 host")
        args = ["ssh", *_SSH_FLAGS, host, cfg.get("python_cmd", "python3 -")]
    try:
        completed = subprocess.run(
            args, input=script, capture_output=True, timeout=CLAUDE_TASKS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ClaudeTasksMachineError(f"采集超时(>{CLAUDE_TASKS_TIMEOUT_SECONDS:g}s)") from error
    except OSError as error:
        raise ClaudeTasksMachineError(f"进程/SSH 启动失败: {error}") from error
    if completed.returncode != 0:
        stderr_tail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ClaudeTasksMachineError(
            "exit " + str(completed.returncode) + (f": {stderr_tail[-1][:160]}" if stderr_tail else ""))
    try:
        data = json.loads(completed.stdout.decode("utf-8", "replace"))
    except ValueError as error:
        raise ClaudeTasksMachineError(f"输出不是约定 JSON: {error}") from error
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ClaudeTasksMachineError("输出缺少 ok=true,丢弃")
    return data


def claude_tasks_fetch(machine_id: str) -> dict[str, Any]:
    """按白名单配置采集一台机器;失败抛 ClaudeTasksMachineError(编排层归一为离线)。"""
    cfg = CLAUDE_TASKS_MACHINES.get(machine_id)
    if cfg is None:
        raise ClaudeTasksMachineError(f"机器 {machine_id} 不在白名单")
    return _claude_tasks_run_machine(machine_id, cfg)


# ==================== 编排:并发拉取 + 短缓存(任一机失败不拖垮整页) ====================
_claude_tasks_cache: dict[str, Any] = {"ts": 0.0, "payload": None}
_claude_tasks_cache_lock = threading.Lock()


def _fetch_machine_entry(machine_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "machine_id": machine_id,
        "role": CLAUDE_TASKS_MACHINES[machine_id].get("role", ""),
        "transport": CLAUDE_TASKS_MACHINES[machine_id].get("transport", ""),
        "online": True, "error": None, "data": None,
    }
    try:
        entry["data"] = claude_tasks_fetch(machine_id)
    except Exception as error:  # noqa: BLE001 — 单机失败归一为离线,绝不拖垮整页
        entry["online"] = False
        entry["error"] = f"{type(error).__name__}: {error}"[:200]
    return entry


def claude_tasks_summary(force: bool = False) -> dict[str, Any]:
    """三机 Claude 活动一次全量返回;10s 缓存,force=True 绕过(页面手动刷新用)。"""
    with _claude_tasks_cache_lock:
        now = time.time()
        if (not force and _claude_tasks_cache["payload"] is not None
                and now - _claude_tasks_cache["ts"] < CLAUDE_TASKS_CACHE_SECONDS):
            return _claude_tasks_cache["payload"]
        with ThreadPoolExecutor(max_workers=max(1, len(CLAUDE_TASKS_MACHINES))) as pool:
            machines = list(pool.map(_fetch_machine_entry, CLAUDE_TASKS_MACHINES))
        payload = {"machines": machines, "collected_at": now}
        _claude_tasks_cache["ts"] = now
        _claude_tasks_cache["payload"] = payload
        return payload
