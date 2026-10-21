"""AI 任务派发 worker(大领导):分析 → worktree → 派发小领导 → 收报告 → 回写状态机。

职责(设计 docs/AI任务与平台登录设计.md 7.2 + 两级 agent 编排先例):
  1. 轮询 GET /api/tasks/queue(机器通道,免登录)
  2. 待分析任务 → claude -p 无头分析(读 scripts/dispatch_charter.md)→ POST analyze;
     信息不足 → POST question 转疑问待答,提交人答复后任务自动回队列
  3. 代码修改类 → 本仓库建 worktree,分支 u/<提交人>/t<任务号>-<标题slug>
  4. 按目标机派发小领导:inbox 文件(自包含 outbox 协议)+ tmux send-keys;
     文本与 C-m 分开发送(同突发 C-m 被 TUI 当粘贴吞掉,E2E-2 教训),受理轮询
     最长 60s、每 5s 补发一次 C-m(单次 2s 重试不够;claude TUI 不认 Enter)。
     .176 常规经 SSH;.167 配置 DISPATCH_SSH_167 后同样走 SSH——生产 worker 以
     autotest 跑而小领导 tmux/inbox 属 jks,本地直连会打错人;未配则本地直连,.229 本机联测用
  5. 轮询 outbox(本机 + 远端经 SSH,与派发同构)收报告 → POST progress/merge/finish
     回写状态机;非法/未知/过期报告挪 outbox/bad/ 留查,内容绝不销毁
  6. 终态 → notify_wecom() 独立函数发测试部群:先做 wecom-cli 命令存在性检测
     (本机优先,退回经 SSH DISPATCH_SSH_WECOM,默认 .176 已装),失败只记
     task_events(note),绝不阻塞任务状态;
     @发布人依赖 users.wecom_userid,未配置降级不 @;纯文字无 emoji,中间态绝不发
  7. DISPATCH_STUB=1 全流程本地模拟(不调 claude/不 tmux/不 ssh/绝不外发企微),
     供本地验证与 dashboard/tests/test_dispatch_worker_stub.py 状态机联测

硬性边界见 scripts/dispatch_charter.md(.167 平台库只读/不碰服务进程;.176 不碰 Jenkins)。
凭据:群 chat_id 只从后端 .env.local 读 WECOM_CHAT_ID,不进 git(团队纪律)。

用法:
  python scripts/dispatch_worker.py           # 常驻(部署机 systemd 拉起)
  python scripts/dispatch_worker.py --once    # 扫一轮即退出(调试用)
  python scripts/dispatch_worker.py --loop 5  # 自定义轮询间隔(默认 10s)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")
LOG_PATH = PROJECT_ROOT / "logs" / "dispatch_worker.log"
CHARTER_PATH = PROJECT_ROOT / "scripts" / "dispatch_charter.md"
LOOP_SECONDS = 10
ANALYZE_TIMEOUT = 600  # 大领导分析是重调用,给足 10 分钟
CLAUDE_BIN = os.environ.get("DISPATCH_CLAUDE", "claude")
STUB = os.environ.get("DISPATCH_STUB", "") == "1"

# 派发通道:与两级编排先例对齐(~/leader/inbox + tmux session leader)。
# STUB 模式默认落 logs/dispatch-stub,避免污染本机 ~/leader。
if STUB:
    _default_leader_home = PROJECT_ROOT / "logs" / "dispatch-stub"
else:
    _default_leader_home = Path.home() / "leader"
INBOX_DIR = Path(os.environ.get("DISPATCH_INBOX", _default_leader_home / "inbox"))
OUTBOX_DIR = Path(os.environ.get("DISPATCH_OUTBOX", _default_leader_home / "outbox"))
TMUX_SESSION = os.environ.get("DISPATCH_TMUX_SESSION", "leader")
MACHINE_SSH = {"176": os.environ.get("DISPATCH_SSH_176", "192.168.12.176")}
# 167 派发通道:配置(如 jks@127.0.0.1)则 167 派发/outbox 回读走 SSH——生产 worker 以
# autotest 跑而小领导 tmux/inbox 属 jks,本地直连会打到 autotest 自己;留空=本地直连
SSH_167 = os.environ.get("DISPATCH_SSH_167", "").strip()
REPO_ROOT = Path(os.environ.get("DISPATCH_REPO", PROJECT_ROOT))
WORKTREE_PARENT = Path(os.environ.get("DISPATCH_WORKTREES", REPO_ROOT.parent / "uiautotest-worktrees"))

TASK_TYPES = ("code_change", "test_run", "troubleshoot", "env_maint", "other")
TASK_RISKS = ("high", "mid", "low")
TARGET_MACHINES = ("167", "176")


def log(message: str) -> None:
    """写日志文件(pythonw/systemd 下 stdout 可能为 None,print 前判空)。"""
    line = f"[dispatch-worker {datetime.now().isoformat(timespec='seconds')}] {message}"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except OSError:
        pass  # 日志目录不可写不能挡住派发本身
    if sys.stdout:
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


def _int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _run(args: list[str], input_bytes: bytes | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout)


def slugify(title: str) -> str:
    """标题转分支 slug;纯中文标题返回空串,分支名只剩任务号部分。"""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24]


def claude_analyze(task: dict) -> dict:
    """大领导分析:返回 {question} 或完整五元组;字段经 _validate_analysis 收敛。"""
    if STUB:
        return _validate_analysis(_stub_analyze(task))
    charter = CHARTER_PATH.read_text(encoding="utf-8") if CHARTER_PATH.is_file() else "(章程缺失)"
    qa = "\n".join(
        f"问:{q.get('question', '')} / 答:{q.get('answer') or '(未答)'}"
        for q in task.get("questions") or []) or "(无)"
    prompt = (
        "你是测试部 AI 任务大领导。按章程分析任务,只输出一个 JSON 对象,不要输出任何其他文字。\n"
        f"{charter}\n任务号:{task.get('task_id')}\n标题:{task.get('title')}\n"
        f"描述:\n{task.get('description') or ''}\n历史问答:{qa}\n"
        '输出形状(question 为空字符串表示无疑问):'
        '{"task_type": "code_change|test_run|troubleshoot|env_maint|other",'
        ' "risk": "high|mid|low", "target_machine": "167|176",'
        ' "analysis_note": "做什么/改哪里/怎么验收", "question": ""}')
    run = _run([CLAUDE_BIN, "-p", prompt], timeout=ANALYZE_TIMEOUT)
    if run.returncode != 0:
        raise RuntimeError(f"claude -p rc={run.returncode}: {run.stderr.decode('utf-8', 'replace')[:200]}")
    text = run.stdout.decode("utf-8", "replace")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"claude 输出无 JSON: {text[:200]}")
    return _validate_analysis(json.loads(match.group()))


def _stub_analyze(task: dict) -> dict:
    """STUB 分析:标题关键词启发式,固定 token 数,联测可断言。"""
    text = f"{task.get('title', '')}{task.get('description', '')}"
    if any(key in text for key in ("为什么", "排查", "挂了", "原因")):
        kind, risk, machine, note = "troubleshoot", "mid", "176", "stub 分析:先排查再定论"
    elif any(key in text for key in ("修", "改", "修复", "选择器", "代码")):
        kind, risk, machine, note = "code_change", "high", "176", "stub 分析:定位需修改代码,先跑回归再合并"
    else:
        kind, risk, machine, note = "test_run", "low", "167", "stub 分析:按现有套件执行一遍"
    return {"task_type": kind, "risk": risk, "target_machine": machine, "analysis_note": note,
            "tokens_in": 12000, "tokens_out": 800}


def _validate_analysis(data: dict) -> dict:
    """把大领导产出收敛到平台接受的枚举;非法即抛,由调用方退避重试。"""
    question = str(data.get("question") or "").strip()
    if question:
        return {"question": question[:2000]}
    kind = str(data.get("task_type") or "").strip()
    if kind not in TASK_TYPES:
        raise ValueError(f"task_type 非法: {kind!r}")
    risk = str(data.get("risk") or "").strip()
    if risk not in TASK_RISKS:
        raise ValueError(f"risk 非法: {risk!r}")
    machine = str(data.get("target_machine") or "").strip()
    if machine and machine not in TARGET_MACHINES:
        raise ValueError(f"target_machine 非法: {machine!r}")
    return {"task_type": kind, "risk": risk, "target_machine": machine,
            "analysis_note": str(data.get("analysis_note") or "")[:500],
            "tokens_in": _int(data.get("tokens_in")), "tokens_out": _int(data.get("tokens_out"))}


def ensure_worktree(task: dict) -> tuple[str | None, str | None]:
    """代码类任务建隔离 worktree(章程三-1);非代码类返回 (None, None)。

    STUB 模式不碰 git、不建目录,但分支名照常计算(stub 报告带名,联测协议)。
    """
    if task.get("task_type") != "code_change":
        return None, None
    task_id = task["task_id"]
    branch = (f"u/{task.get('submitter') or 'ai'}/t{task_id.replace('TASK-', '').lower()}"
              f"-{slugify(task.get('title') or '')}").rstrip("-")
    if STUB:
        return branch, None
    path = WORKTREE_PARENT / task_id
    if path.exists():
        return branch, str(path)
    WORKTREE_PARENT.mkdir(parents=True, exist_ok=True)
    add = _run(["git", "-C", str(REPO_ROOT), "worktree", "add", "-b", branch, str(path)])
    if add.returncode != 0:  # 分支已存在(上次派发建过)→ 直接挂分支
        add = _run(["git", "-C", str(REPO_ROOT), "worktree", "add", str(path), branch])
    if add.returncode != 0:
        raise RuntimeError(f"worktree 创建失败: {add.stderr.decode('utf-8', 'replace')[:200]}")
    log(f"worktree 就绪 {task_id} branch={branch} path={path}")
    return branch, str(path)


def _ssh_host_for(machine: str) -> str | None:
    """目标机的派发/outbox SSH 通道;None=本地直连(仅 167 未配 DISPATCH_SSH_167 时)。"""
    if machine == "176":
        return MACHINE_SSH["176"]
    if machine == "167" and SSH_167:
        return SSH_167
    return None


def _remote_hosts() -> list[tuple[str, str]]:
    """远端 outbox 清单:与派发通道同构,167 只在配置了 SSH 通道时才远端回读。"""
    remotes = [("176", MACHINE_SSH["176"])]
    if SSH_167:
        remotes.append(("167", SSH_167))
    return remotes


SUBMIT_MARK = "esc to interrupt"  # claude TUI 受理标志;Enter 键 TUI 不受理,派发一律 C-m

DISPATCH_PROMPT = ("读取执行 {inbox} 中的任务 {task_id},遵守其中的章程边界;"
                   "进度与结果按任务卡 outbox_protocol 字段的协议写 {outbox}")

# outbox 报告协议随任务卡自包含下发(小领导侧 LEADER.md 是 9-22 老章程,没有这套协议;
# 缺 phase 字段的报告平台无法受理)。文件名带序号防 progress 多条互相覆盖。
OUTBOX_PROTOCOL = (
    "outbox 报告协议(把 JSON 报告写到 $HOME/leader/outbox/<序号>-<任务号>.json,"
    "每个文件必须是单个 JSON 对象,严禁写成数组或一次塞多条记录;"
    "progress 可写多个文件序号递增,finish 写完即终;phase 必填,缺了平台无法受理):\n"
    '进度: {"task_id": "<任务号>", "phase": "progress", "detail": "一句话进展",'
    ' "tokens_in": 0, "tokens_out": 0}\n'
    '合并: {"task_id": "<任务号>", "phase": "merge", "event": "merge_conflict|merge_retry",'
    ' "detail": "冲突说明"}\n'
    '完成: {"task_id": "<任务号>", "phase": "finish", "ok": true,'
    ' "report_md": "## 结论…(Markdown 报告,结论先行附验证证据)",'
    ' "tokens_in": 0, "tokens_out": 0}\n'
    '失败: finish 同一条,ok=false 必带 "error_message"。token 为该轮增量,平台侧累计。\n'
    '代码修改类任务的 finish 额外带 "merge_status": "merged|pending|conflict|abandoned"'
    ' 与 "branch_name"/"worktree_path";其他类型任务一律不要写这三个字段'
    '(非代码任务无分支与合并语义)。')


def dispatch_task(task: dict, branch: str | None, worktree: str | None) -> bool:
    """任务卡投递到目标机小领导:inbox 文件 + tmux send-keys(.176 经 SSH)。

    STUB 模式同时模拟小领导回写(先 progress 后 finish),驱动 outbox 处理路径。
    """
    machine = str(task.get("target_machine") or "167")
    task_id = task["task_id"]
    payload = {key: task.get(key) for key in
               ("task_id", "title", "description", "task_type", "risk", "analysis_note", "submitter")}
    payload.update({"target_machine": machine, "branch_name": branch, "worktree_path": worktree,
                    "questions": task.get("questions") or [],
                    "outbox_protocol": OUTBOX_PROTOCOL})  # 任务卡自包含,不依赖 leader 侧章程版本
    body = json.dumps(payload, ensure_ascii=False)
    inbox_name = f"{datetime.now().strftime('%H%M%S')}-{task_id}.json"
    if STUB:
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        (INBOX_DIR / inbox_name).write_text(body, encoding="utf-8")
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        (OUTBOX_DIR / f"1-{task_id}.json").write_text(json.dumps(
            {"task_id": task_id, "phase": "progress", "detail": "stub 小领导已开工",
             "tokens_in": 500, "tokens_out": 200}, ensure_ascii=False), encoding="utf-8")
        report: dict = {"task_id": task_id, "phase": "finish", "ok": True,
                        "report_md": "## 结论\nstub 模式模拟执行完成\n\n- 状态机走通\n- 未外发任何命令",
                        "tokens_in": 1000, "tokens_out": 400}
        if task.get("task_type") == "code_change":
            report.update({"merge_status": "merged", "branch_name": branch, "worktree_path": worktree})
        (OUTBOX_DIR / f"2-{task_id}.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        log(f"stub dispatch {task_id} → {machine} inbox={inbox_name}")
        return True
    outbox_hint = f"$HOME/leader/outbox/{task_id}.json"
    host = _ssh_host_for(machine)
    if host:  # 远端(176 常规;167 配置 DISPATCH_SSH_167 后同构):文件/按键/回执一条 SSH 会话
        inbox_hint = f"$HOME/leader/inbox/{inbox_name}"
        prompt = DISPATCH_PROMPT.format(inbox=inbox_hint, task_id=task_id, outbox=outbox_hint)
        quoted_session = shlex.quote(TMUX_SESSION)
        # 文本先单独 send-keys,停 1s 再进入受理轮询:C-m 与文本同突发会被 TUI
        # 当成粘贴内容吞掉(只换行不提交);轮询 12×5s,期间每轮补发 C-m——
        # 已提交后再发 C-m 是空输入回车,无副作用。受理=footer 出现 esc to interrupt
        script = (
            f"mkdir -p ~/leader/inbox ~/leader/outbox && "
            f"cat > {inbox_hint} && "
            f"tmux send-keys -t {quoted_session} {shlex.quote(prompt)} && sleep 1 && "
            f"for i in $(seq 1 12); do "
            f"tmux send-keys -t {quoted_session} C-m; sleep 5; "
            f"if tmux capture-pane -t {quoted_session} -p | grep -qi '{SUBMIT_MARK}'; "
            f"then echo submitted; exit 0; fi; done; echo resent")
        run = _run(["ssh", host, script], body.encode("utf-8"))
    else:  # 本地直连:直接写文件 + 本地 tmux(.229 本机联测用)
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        inbox_path = INBOX_DIR / inbox_name
        inbox_path.write_text(body, encoding="utf-8")
        prompt = DISPATCH_PROMPT.format(inbox=inbox_path, task_id=task_id, outbox=outbox_hint)
        run = _run(["tmux", "send-keys", "-t", TMUX_SESSION, prompt])
        if run.returncode == 0:
            time.sleep(1)
            for _ in range(12):
                _run(["tmux", "send-keys", "-t", TMUX_SESSION, "C-m"])
                time.sleep(5)
                pane = _run(["tmux", "capture-pane", "-t", TMUX_SESSION, "-p"]).stdout
                if SUBMIT_MARK in pane.decode("utf-8", "replace").lower():
                    break
            else:
                run = subprocess.CompletedProcess(run.args, 1, b"resent", b"")
    if run.returncode != 0:
        raise RuntimeError(f"派发失败 {machine}: "
                           f"{(run.stderr or run.stdout).decode('utf-8', 'replace')[:200]}")
    if "resent" in run.stdout.decode("utf-8", "replace").lower():
        log(f"leader 未受理已补发 C-m {task_id}(若仍未执行需人工看一眼)")
    log(f"dispatched {task_id} → {machine} inbox={inbox_name}")
    return True


def wecom_cli_command() -> list[str] | None:
    """企微发送命令存在性检测:本机 which 优先,退回经 SSH DISPATCH_SSH_WECOM。

    默认回退 .176(已装 wecom-cli)——生产 worker 就在 .167 跑,SSH 回自己
    (autotest 没有到自己的钥匙)只会卡死,回退机必须独立配置。
    """
    if shutil.which("wecom-cli"):
        return ["wecom-cli"]
    host = os.environ.get("DISPATCH_SSH_WECOM", "").strip() or MACHINE_SSH["176"]
    try:
        probe = _run(["ssh", host, "command -v wecom-cli"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0 and probe.stdout.strip():
        return ["ssh", host, probe.stdout.decode("utf-8", "replace").strip()]
    return None


def notify_wecom(task: dict, queue_item: dict | None = None) -> None:
    """任务终态企微群通知(独立函数)。失败只记 task_events(note),绝不阻塞任务状态。

    纯文字无 emoji;失败附一行原因;@发布人靠 users.wecom_userid(队列带
    submitter_wecom_userid),未配置降级不 @。中间态绝不发。凭据只认
    WECOM_CHAT_ID(后端 .env.local);未配置或 STUB 一律不外发。
    """
    task_id = str(task.get("task_id") or "?")

    def note(detail: str) -> None:
        try:
            _post("/api/dispatch/update", {"action": "note", "task_id": task_id,
                                           "event_type": "wecom_failed", "detail": detail})
        except Exception as error:  # noqa: BLE001 — 旁路日志再失败只剩本地日志
            log(f"wecom note 落库失败 {task_id}: {error}")

    try:
        if STUB:
            note("stub 模式不发送企微")
            return
        chat_id = os.environ.get("WECOM_CHAT_ID", "")
        if not chat_id:
            note("未配置 WECOM_CHAT_ID,群消息未发")
            return
        status = task.get("status")
        title = (queue_item or {}).get("title") or task.get("title") or ""
        if status == "done":
            text = f"「Swan 测试平台」任务 {task_id}「{title}」已完成"
        elif status == "failed":
            text = f"「Swan 测试平台」任务 {task_id}「{title}」已失败"
            reason = (task.get("error_message") or "")[:100]
            if reason:
                text += f"\n原因:{reason}"
        else:
            return  # 非终态绝不发
        userid = (queue_item or {}).get("submitter_wecom_userid")
        if userid:
            text += f" <@{userid}>"
        cli = wecom_cli_command()
        if not cli:
            note("wecom-cli 不存在(本机与平台机均未安装),群消息未发")
            return
        run = _run(cli + ["message", "aibot", "send", "--chat-id", chat_id, "--msg-type", "markdown",
                          "--markdown", json.dumps({"content": text}, ensure_ascii=False)], timeout=30)
        if run.returncode != 0:
            tail = (run.stderr or run.stdout).decode("utf-8", "replace")[:120]
            note(f"企微发送失败 rc={run.returncode}: {tail}")
        else:
            log(f"wecom notified {task_id}")
    except Exception as error:  # noqa: BLE001 — 通知是旁路,任何异常都不外抛
        note(f"企微通知异常:{error}")


def _iter_outbox():
    """产出 (source, 文件名, 文本):本机 outbox + 各远端 outbox(经 SSH,与派发同构)。"""
    if OUTBOX_DIR.is_dir():
        for path in sorted(OUTBOX_DIR.glob("*.json")):
            try:
                yield "local", path.name, path.read_text(encoding="utf-8")
            except OSError as error:
                log(f"读 outbox 失败 {path.name}: {error}")
    for machine, host in _remote_hosts():
        try:
            listed = _run(["ssh", host, "ls -1 $HOME/leader/outbox/*.json 2>/dev/null"], timeout=20)
            for line in listed.stdout.decode("utf-8", "replace").splitlines():
                name = Path(line.strip()).name
                if not name:
                    continue
                cat = _run(["ssh", host, f"cat $HOME/leader/outbox/{shlex.quote(name)}"], timeout=20)
                if cat.returncode == 0:
                    yield f"{machine}:{host}", name, cat.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired) as error:
            log(f"{machine} outbox 读取失败: {error}")


def _discard_outbox(source: str, name: str) -> None:
    try:
        if source == "local":
            (OUTBOX_DIR / name).unlink(missing_ok=True)
        else:  # 远端来源形如 "167:jks@127.0.0.1"/"176:192.168.12.176"
            _run(["ssh", source.split(":", 1)[1],
                  f"rm -f $HOME/leader/outbox/{shlex.quote(name)}"], timeout=20)
    except OSError as error:
        log(f"清理 outbox 失败 {name}: {error}")


def _quarantine_outbox(source: str, name: str) -> None:
    """报告挪 outbox/bad/ 留查——内容绝不销毁,现场随时可回放(2026-09-30 丢报告教训)。"""
    try:
        if source == "local":
            bad = OUTBOX_DIR / "bad"
            bad.mkdir(parents=True, exist_ok=True)
            (OUTBOX_DIR / name).replace(bad / name)
        else:
            _run(["ssh", source.split(":", 1)[1],
                  f"mkdir -p $HOME/leader/outbox/bad && "
                  f"mv $HOME/leader/outbox/{shlex.quote(name)} $HOME/leader/outbox/bad/"], timeout=20)
    except OSError as error:
        log(f"挪 bad/ 失败 {name}: {error}")


def _apply_report(report: dict, queue_item: dict) -> bool:
    """把一条 outbox 报告回写到状态机;True=已受理,False=隔离留查。"""
    task_id = report["task_id"]
    phase = report.get("phase")
    tokens = {"tokens_in": _int(report.get("tokens_in")), "tokens_out": _int(report.get("tokens_out"))}
    # 小领导常把换行写成字面 \n(E2E-2 实测),入库前归一成真实换行,页面才可读
    readable = lambda text: str(text or "").replace("\\r\\n", "\n").replace("\\n", "\n")
    if phase == "progress":
        _post("/api/dispatch/update", {"action": "progress", "task_id": task_id,
                                       "detail": readable(report.get("detail")), **tokens})
        return True
    if phase == "merge":
        _post("/api/dispatch/update", {"action": "merge", "task_id": task_id,
                                       "event": report.get("event"),
                                       "detail": readable(report.get("detail"))})
        return True
    if phase == "finish":
        # 合并三字段仅代码任务有意义;leader 常对非代码任务误抄协议示例值
        #(E2E 实测非代码任务被塞 merge_status=abandoned),非代码一律清空
        code_task = (queue_item or {}).get("task_type") == "code_change"
        result = _post("/api/dispatch/update", {
            "action": "finish", "task_id": task_id, "ok": bool(report.get("ok")),
            "report_md": readable(report.get("report_md")),
            "error_message": readable(report.get("error_message")),
            "branch_name": str(report.get("branch_name") or "") if code_task else "",
            "worktree_path": str(report.get("worktree_path") or "") if code_task else "",
            "merge_status": str(report.get("merge_status") or "") if code_task else "", **tokens})
        notify_wecom(result, queue_item)
        return True
    log(f"未知 phase {phase!r} task={task_id},挪 bad/ 留查")
    return False  # 报告内容不能销毁,由调用方隔离留查


def process_outbox(active: dict[str, dict]) -> int:
    """扫本机与远端 outbox,回写报告。

    已受理的报告清走;非法 JSON/任务不在队列/未知 phase/结构错乱/被平台拒绝
    (4xx)的挪 outbox/bad/ 留查不销毁;5xx/网络错误原地保留待重试。bad/ 不在
    扫描范围(只扫根层 *.json),不会反复处理。
    leader 可能并写一个文件含多条 phase 记录(JSON 数组,E2E-2 实测):逐条
    展开按序受理;数组里任何一条非法则整份挪 bad/。
    """
    handled = 0
    for source, name, raw in _iter_outbox():
        try:
            report = json.loads(raw)
        except ValueError:
            log(f"outbox 非法 JSON {source}:{name},挪 bad/ 留查")
            _quarantine_outbox(source, name)
            continue
        records = report if isinstance(report, list) else [report]
        quarantine = False
        try:
            for record in records:
                task_id = str(record.get("task_id") or "")
                if task_id not in active:
                    raise ValueError(f"报告任务不在队列 task={task_id}")
                if not _apply_report(record, active[task_id]):
                    quarantine = True
                else:
                    handled += 1
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                log(f"报告被平台拒绝({error.code}) {source}:{name},挪 bad/ 留查")
                quarantine = True
            else:
                log(f"平台 {error.code} {source}:{name},下轮重试")
                continue
        except urllib.error.URLError as error:
            log(f"平台不可达 {source}:{name}: {error},下轮重试")
            continue
        except Exception as error:  # noqa: BLE001 — 结构错乱重试无益,挪 bad/
            log(f"报告结构异常 {source}:{name}: {error},挪 bad/ 留查")
            quarantine = True
        if quarantine:
            _quarantine_outbox(source, name)
        else:
            _discard_outbox(source, name)
    return handled


def tick(state: dict) -> int:
    """扫一轮:分析/派发待分析任务,收 outbox 报告;返回本轮处理条数。"""
    active = {t["task_id"]: t for t in _get("/api/tasks/queue").get("items") or []}
    handled = 0
    for task in list(active.values()):
        task_id = task["task_id"]
        if task["status"] != "pending_analysis":
            continue  # questioning 等提交人答复;running 等小领导 outbox
        if time.time() < state.get(f"cool:{task_id}", 0):
            continue  # 上轮启动失败,退避中
        if task.get("task_type"):  # 上轮已分析(启动失败重入)→ 直接补启动,不重复分析
            analysis: dict = {}
        else:
            try:
                analysis = claude_analyze(task)
            except Exception as error:  # noqa: BLE001 — 分析失败退避重试,不挡其他任务
                log(f"分析失败 {task_id}: {error},60s 后重试")
                state[f"cool:{task_id}"] = time.time() + 60
                continue
            if analysis.get("question"):
                _post("/api/dispatch/update", {"action": "question", "task_id": task_id,
                                               "question": analysis["question"]})
                log(f"提问 {task_id}: {analysis['question'][:60]}")
                handled += 1
                continue
            _post("/api/dispatch/update", {
                "action": "analyze", "task_id": task_id,
                "task_type": analysis["task_type"], "risk": analysis["risk"],
                "target_machine": analysis["target_machine"],
                "analysis_note": analysis["analysis_note"],
                "tokens_in": analysis["tokens_in"], "tokens_out": analysis["tokens_out"]})
            task = {**task, "task_type": analysis["task_type"],
                    "target_machine": analysis["target_machine"] or "167"}
        try:
            branch, worktree = ensure_worktree(task)
            _post("/api/dispatch/update", {"action": "start", "task_id": task_id})
            dispatch_task(task, branch, worktree)
        except Exception as error:  # noqa: BLE001 — 启动失败退避重试(任务仍在待分析/running)
            log(f"启动失败 {task_id}: {error},60s 后重试")
            state[f"cool:{task_id}"] = time.time() + 60
        handled += 1
    return handled + process_outbox(active)


def main() -> int:
    parser = argparse.ArgumentParser(description="Swan 平台 AI 任务派发 worker(大领导)")
    parser.add_argument("--once", action="store_true", help="扫一轮即退出(调试用)")
    parser.add_argument("--loop", type=int, default=LOOP_SECONDS, help="轮询间隔秒数(默认 10)")
    args = parser.parse_args()
    log(f"启动: platform={PLATFORM_URL} stub={STUB} mode={'once' if args.once else 'loop'} "
        f"inbox={INBOX_DIR} outbox={OUTBOX_DIR}")
    state: dict = {}
    try:
        while True:
            try:
                tick(state)
            except Exception as error:  # noqa: BLE001 — 单轮失败不退出,等下一轮
                log(f"tick error: {error}")
            if args.once:
                break
            time.sleep(args.loop)
    except KeyboardInterrupt:
        log("收到中断,退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
