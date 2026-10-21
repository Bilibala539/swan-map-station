"""dispatch_worker STUB 模式状态机联测(2026-09-29)。

DISPATCH_STUB=1 下不调 claude/不 tmux/不 ssh/绝不外发企微,全流程本地走通:
提交 → 大领导分析(analyze) → worktree 跳过 → start → 派发(stub 小领导回写
outbox progress+finish) → 状态机 done/merged → 企微通知降级只记 task_events(note)。
"""
from __future__ import annotations

import gc
import importlib.util
import json
import os
import tempfile
import threading
import urllib.request
from pathlib import Path

from dashboard.server import create_server

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dispatch_worker.py"


def _load_worker(base_url: str, io_root: Path):
    """以测试专属环境加载 dispatch_worker 模块(环境变量在 exec 前注入)。"""
    spec = importlib.util.spec_from_file_location("dispatch_worker_under_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    old_env = {key: os.environ.get(key) for key in
               ("DISPATCH_STUB", "DASHBOARD_URL", "DISPATCH_INBOX", "DISPATCH_OUTBOX",
                "WECOM_CHAT_ID", "DISPATCH_REPO", "DISPATCH_WORKTREES")}
    os.environ["DISPATCH_STUB"] = "1"
    os.environ["DASHBOARD_URL"] = base_url
    os.environ["DISPATCH_INBOX"] = str(io_root / "inbox")
    os.environ["DISPATCH_OUTBOX"] = str(io_root / "outbox")
    os.environ.pop("WECOM_CHAT_ID", None)  # STUB 下本就拦截,双保险:绝不外发
    os.environ["DISPATCH_REPO"] = str(io_root / "repo")
    os.environ["DISPATCH_WORKTREES"] = str(io_root / "wt")
    try:
        spec.loader.exec_module(module)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return module


class Client:
    """带 cookie 罐的最小 API 客户端(与 test_task_board 同款)。"""

    def __init__(self, base_url: str):
        self.base = base_url
        self.cookie = ""

    def req(self, path: str, method: str = "GET", payload: dict | None = None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request_object = urllib.request.Request(
            self.base + path, data=body, method=method, headers=headers)
        with urllib.request.urlopen(request_object, timeout=5) as response:
            set_cookie = response.headers.get("Set-Cookie")
            if set_cookie:
                self.cookie = set_cookie.split(";")[0]
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)


def test_stub_worker_full_loop():
    with tempfile.TemporaryDirectory() as temporary_directory:
        io_root = Path(temporary_directory)
        server = create_server("127.0.0.1", 0, io_root / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            client = Client(base)
            client.req("/api/auth/login", "POST", {"username": "yumiao"})
            _, body = client.req("/api/tasks", "POST", {
                "title": "修一下登录用例的选择器", "description": "LOGIN-L2-POS-002 最近三天必失败"})
            task_id = body["task_id"]

            worker = _load_worker(base, io_root)
            assert worker.STUB is True
            handled = worker.tick({})
            assert handled == 3  # 一轮全通:分析/启动/派发 1 + outbox 回写(progress+finish) 2

            status, detail = client.req(f"/api/tasks/{task_id}")
            assert status == 200 and detail["status"] == "done"
            assert detail["task_type"] == "code_change" and detail["risk"] == "high"
            assert detail["merge_status"] == "merged"  # stub 报告按代码类合并成功收尾
            assert detail["branch_name"] == f"u/yumiao/t{task_id.replace('TASK-', '').lower()}"
            assert detail["worktree_path"] is None  # STUB 不建 worktree
            assert "stub 模式模拟执行完成" in detail["report_md"]
            # token:分析 12800 + 进展 700 + 收尾 1400
            assert detail["tokens_total"] == 12800 + 700 + 1400

            event_types = [e["event_type"] for e in detail["events"]]
            assert event_types.count("analyzed") == 1 and event_types.count("started") == 1
            assert event_types.count("progress") == 1 and event_types.count("finished") == 1
            # 企微降级:STUB 绝不外发,只记 note 事件,任务状态不被阻塞
            assert event_types.count("wecom_failed") == 1
            assert "stub 模式不发送企微" in detail["events"][-1]["detail"]

            # 队列已空,收件箱留痕,outbox 清空
            queue = json.loads(_raw_queue(base))
            assert not queue["items"]
            assert (io_root / "outbox").is_dir() and not list((io_root / "outbox").glob("*.json"))
            assert list((io_root / "inbox").glob("*.json"))  # 任务卡留痕

            # 非代码类(troubleshoot):终态无 merge_status
            _, body2 = client.req("/api/tasks", "POST",
                                  {"title": "每日回归为什么全挂了", "description": "176 全红"})
            worker.tick({})
            _, detail2 = client.req(f"/api/tasks/{body2['task_id']}")
            assert detail2["status"] == "done" and detail2["task_type"] == "troubleshoot"
            assert detail2["merge_status"] is None
            assert detail2["tokens_total"] == 12800 + 700 + 1400
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            gc.collect()


def _raw_queue(base: str) -> str:
    request_object = urllib.request.Request(f"{base}/api/tasks/queue")
    with urllib.request.urlopen(request_object, timeout=5) as response:
        return response.read().decode("utf-8")
