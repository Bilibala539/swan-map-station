"""dispatch_worker 通道选择联测(2026-09-30):167 SSH 化 + wecom 回退链。

纪律:全程 fake _run,绝不真调 ssh/tmux;STUB=1 全流程不碰 ssh/tmux 由
test_dispatch_worker_stub.py 保证,这里单测路由选择逻辑本身。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dispatch_worker.py"


def _load_worker():
    """以 STUB=1 安全默认加载 worker 模块(纯定义,无副作用),测试内再改属性。"""
    spec = importlib.util.spec_from_file_location("dispatch_worker_routes", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    old = os.environ.get("DISPATCH_STUB")
    os.environ["DISPATCH_STUB"] = "1"
    try:
        spec.loader.exec_module(module)
    finally:
        if old is None:
            os.environ.pop("DISPATCH_STUB", None)
        else:
            os.environ["DISPATCH_STUB"] = old
    return module


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"", args=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.args = args or []  # worker 未受理兜底会读 run.args 重组 CompletedProcess


def _fake_run(calls: list):
    def run(args, input_bytes=None, timeout=30):
        calls.append((args, input_bytes))
        if "capture-pane" in args:  # 本地受理轮询:首轮即见 claude TUI 受理标志(esc to interrupt)
            return _FakeCompleted(args=args, stdout=b"esc to interrupt")
        return _FakeCompleted(args=args)
    return run


def _task(machine: str) -> dict:
    return {"task_id": "TASK-20260930-001", "title": "修选择器", "submitter": "yumiao",
            "task_type": "test_run", "target_machine": machine}


def test_route_167_via_ssh_when_configured():
    worker = _load_worker()
    worker.STUB = False
    worker.SSH_167 = "jks@127.0.0.1"
    calls: list = []
    worker._run = _fake_run(calls)

    assert worker._ssh_host_for("167") == "jks@127.0.0.1"
    assert ("167", "jks@127.0.0.1") in worker._remote_hosts()  # outbox 回读同构
    assert worker.dispatch_task(_task("167"), None, None) is True

    (args, body), = calls  # 一条 SSH 会话:远端 inbox 文件 + C-m 按键 + capture-pane 回执
    assert args[0] == "ssh" and args[1] == "jks@127.0.0.1"
    assert "tmux send-keys" in args[2] and "$HOME/leader/inbox/" in args[2]
    assert "C-m" in args[2] and "capture-pane" in args[2] and "esc to interrupt" in args[2]
    payload = json.loads(body.decode("utf-8"))
    assert payload["task_id"] == "TASK-20260930-001"
    # 任务卡自包含 outbox 协议(小领导侧 LEADER.md 是 9-22 老章程,不能依赖)
    assert all(word in payload["outbox_protocol"] for word in ("progress", "merge", "finish"))


def test_route_167_local_when_unset(monkeypatch):
    worker = _load_worker()
    worker.STUB = False
    worker.SSH_167 = ""
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # 跳过提交回执等待
    calls: list = []
    worker._run = _fake_run(calls)
    with tempfile.TemporaryDirectory() as temporary_directory:
        worker.INBOX_DIR = Path(temporary_directory) / "inbox"

        assert worker._ssh_host_for("167") is None
        assert not [machine for machine, _ in worker._remote_hosts() if machine == "167"]
        assert worker.dispatch_task(_task("167"), None, None) is True

        # 本地直连:文本单独 send-keys → 受理轮询(每轮 C-m+capture-pane,首轮即受理);全程不碰 ssh
        assert [call[0][0] for call in calls] == ["tmux"] * 3
        assert calls[0][0][1] == "send-keys" and calls[0][0][-1].startswith("读取执行")
        assert calls[1][0] == ["tmux", "send-keys", "-t", worker.TMUX_SESSION, "C-m"]
        assert calls[2][0][1] == "capture-pane"
        inbox_file, = worker.INBOX_DIR.glob("*.json")
        payload = json.loads(inbox_file.read_text(encoding="utf-8"))
        assert payload["task_id"] == "TASK-20260930-001"
        assert "outbox_protocol" in payload


def test_route_176_always_ssh():
    worker = _load_worker()
    worker.STUB = False
    worker.SSH_167 = ""  # 176 通道不受 167 配置影响
    calls: list = []
    worker._run = _fake_run(calls)

    assert worker._ssh_host_for("176") == worker.MACHINE_SSH["176"]
    assert worker.dispatch_task(_task("176"), None, None) is True
    (args, _), = calls
    assert args[0] == "ssh" and args[1] == worker.MACHINE_SSH["176"]
    assert "C-m" in args[2]  # 派发回车一律 C-m(claude TUI 不认 Enter)


def test_outbox_readback_mirrors_dispatch_channel():
    worker = _load_worker()
    worker.STUB = False
    worker.SSH_167 = "jks@127.0.0.1"
    report = json.dumps({"task_id": "TASK-20260930-001", "phase": "progress",
                         "detail": "x"}, ensure_ascii=False).encode("utf-8")
    calls: list = []

    def run(args, input_bytes=None, timeout=30):
        calls.append(args)
        if "ls -1" in args[2]:
            return _FakeCompleted(stdout=b"/home/jks/leader/outbox/1-TASK-20260930-001.json\n")
        return _FakeCompleted(stdout=report)  # cat

    worker._run = run
    with tempfile.TemporaryDirectory() as temporary_directory:
        worker.OUTBOX_DIR = Path(temporary_directory) / "outbox"  # 本地空,只剩远端
        items = list(worker._iter_outbox())
    assert len(items) == 2  # 176 + 167 各一次远端扫描
    sources = {source for source, _, _ in items}
    assert sources == {f"176:{worker.MACHINE_SSH['176']}", "167:jks@127.0.0.1"}
    assert items[0][2] == report.decode("utf-8")

    # 清理同样按来源走对应 SSH 主机
    worker._run = _fake_run(calls)
    worker._discard_outbox("167:jks@127.0.0.1", "1-TASK-20260930-001.json")
    last_args = calls[-1][0]
    assert last_args[0] == "ssh" and last_args[1] == "jks@127.0.0.1"
    assert "rm -f" in last_args[2]


def test_wecom_fallback_chain(monkeypatch):
    worker = _load_worker()
    monkeypatch.setattr("shutil.which", lambda name: None)  # 本机没有 wecom-cli
    calls: list = []

    # 默认回退 .176(生产 worker 在 .167 上,ssh 回自己只会卡死)
    def probe_ok(args, input_bytes=None, timeout=30):
        calls.append(args)
        return _FakeCompleted(stdout=b"/usr/local/bin/wecom-cli\n")

    worker._run = probe_ok
    assert worker.wecom_cli_command() == ["ssh", worker.MACHINE_SSH["176"],
                                          "/usr/local/bin/wecom-cli"]
    assert calls[-1][1] == worker.MACHINE_SSH["176"]

    # DISPATCH_SSH_WECOM 覆盖回退机
    monkeypatch.setenv("DISPATCH_SSH_WECOM", "me@somewhere")
    calls.clear()
    assert worker.wecom_cli_command() == ["ssh", "me@somewhere", "/usr/local/bin/wecom-cli"]
    assert calls[-1][1] == "me@somewhere"

    # 本机有 wecom-cli → 直接本机,不碰 ssh
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/wecom-cli"
                        if name == "wecom-cli" else None)
    calls.clear()
    assert worker.wecom_cli_command() == ["wecom-cli"]
    assert not calls

    # 探测失败(命令不存在/超时)→ None,调用方降级只记事件
    monkeypatch.setattr("shutil.which", lambda name: None)
    worker._run = lambda args, input_bytes=None, timeout=30: _FakeCompleted(returncode=1)
    assert worker.wecom_cli_command() is None

    def raise_timeout(args, input_bytes=None, timeout=30):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    worker._run = raise_timeout
    assert worker.wecom_cli_command() is None


def test_quarantine_preserves_content():
    """非法/未知 phase/任务不在队列的报告挪 outbox/bad/,内容不销毁(2026-09-30 丢报告教训)。"""
    worker = _load_worker()
    worker.STUB = False
    calls: list = []
    worker._run = _fake_run(calls)  # 防误触:本地隔离不应碰 ssh
    with tempfile.TemporaryDirectory() as temporary_directory:
        outbox = Path(temporary_directory) / "outbox"
        outbox.mkdir()
        cases = {
            "a-bad-phase.json": '{"task_id": "TASK-X", "detail": "没有 phase 字段的现场报告"}',
            "b-invalid.json": '{"task_id": 截断的非法 JSON',
            "c-stale.json": '{"task_id": "TASK-GONE", "phase": "finish", "ok": true}',
        }
        for name, text in cases.items():
            (outbox / name).write_text(text, encoding="utf-8")
        worker.OUTBOX_DIR = outbox
        handled = worker.process_outbox({"TASK-X": {"task_id": "TASK-X"}})
        assert handled == 0
        assert not list(outbox.glob("*.json"))  # 根层清空,下轮不会反复处理
        bad = {path.name: path.read_text(encoding="utf-8") for path in (outbox / "bad").glob("*")}
        assert bad == cases  # 三份原样保留,内容零销毁
        assert not [args for args, _ in calls if "bad" in " ".join(args)]  # 隔离走本地 mv,不经 ssh
