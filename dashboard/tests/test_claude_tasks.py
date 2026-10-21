"""Claude 任务视窗(dashboard/claude_tasks.py,2026-10-15)联测。

纪律(同 test_dispatch_routes):全程 fake,绝不真调 ssh/tmux;
解析器用本机真实 jsonl 摘样做 fixture(2026-10-15 实测结构:
主会话=projects/<proj>/<uuid>.jsonl 且 isSidechain=false;
子代理=<uuid>/subagents/agent-*.jsonl 且 isSidechain=true 带 agentId)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

_DASH_DIR = str(Path(__file__).resolve().parents[1])
if _DASH_DIR not in sys.path:  # 与 server.py 头部同款:裸 import claude_tasks 两种运行方式都通
    sys.path.insert(0, _DASH_DIR)
import claude_tasks as ct


# ---------- 采集脚本 exec 装载(不触发 _main) ----------

def _collector() -> dict:
    ns: dict = {"__name__": "claude_tasks_script_test"}
    exec(ct.CLAUDE_TASKS_SCRIPT, ns)  # noqa: S102 — 受控常量,非外部输入
    return ns


# ---------- fixture:本机真实 jsonl 摘样(结构对齐 2026-10-15 实测) ----------

MAIN_HEAD = [
    '{"type":"mode","mode":"normal","sessionId":"11111111-1111-1111-1111-111111111111"}',
    '{"type":"permission-mode","permissionMode":"bypassPermissions",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}',
    '{"parentUuid":null,"isSidechain":false,"promptId":"p1","type":"user",'
    '"message":{"role":"user","content":"唤起平台侧开发agent team mate，用worktree 槽位开发"},'
    '"uuid":"u1","timestamp":"2026-10-15T07:45:42.567Z","cwd":"C:\\\\Users\\\\dev1",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}',
    '{"type":"ai-title","aiTitle":"Claude 任务视窗","sessionId":"11111111-1111-1111-1111-111111111111"}',
]
MAIN_TAIL = [
    '{"parentUuid":"a1","isSidechain":false,"type":"assistant",'
    '"message":{"role":"assistant","model":"claude-sonnet-5","content":[{"type":"text","text":"ok"}]},'
    '"uuid":"a2","timestamp":"2026-10-15T09:55:10.000Z","cwd":"C:\\\\Users\\\\dev1",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}',
    '{"parentUuid":"a2","isSidechain":false,"type":"user",'
    '"message":{"role":"user","content":"继续"},'
    '"uuid":"u2","timestamp":"2026-10-15T09:56:30.000Z","cwd":"C:\\\\Users\\\\dev1",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}',
]
SUB_FIRST = (
    '{"parentUuid":null,"isSidechain":true,'
    '"agentId":"aclaude-mon-dev-b155a9121aeb2372","type":"user",'
    '"message":{"role":"user","content":"<teammate-message teammate_id=\\"team-lead\\">\\n你是平台侧开发 teammate"},'
    '"uuid":"s1","timestamp":"2026-10-15T08:00:00.000Z","cwd":"E:\\\\uiautotest-wt\\\\wt2",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}'
)
SUB_TAIL = [
    '{"parentUuid":"s9","isSidechain":true,"agentId":"aclaude-mon-dev-b155a9121aeb2372",'
    '"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-5",'
    '"content":[{"type":"text","text":"..."}]},'
    '"uuid":"s10","timestamp":"2026-10-15T09:57:00.000Z","cwd":"E:\\\\uiautotest-wt\\\\wt2",'
    '"sessionId":"11111111-1111-1111-1111-111111111111"}',
]


@pytest.fixture
def projects_dir(tmp_path: Path) -> Path:
    """projects/ 结构:一个主会话(活跃)+一个子代理(活跃)+一个冷会话(超窗)。"""
    base = tmp_path / "projects" / "C--Users-dev1"
    base.mkdir(parents=True)
    main_lines = MAIN_HEAD + MAIN_TAIL
    (base / "11111111-1111-1111-1111-111111111111.jsonl").write_text(
        "\n".join(main_lines) + "\n", encoding="utf-8")
    sub_dir = base / "11111111-1111-1111-1111-111111111111" / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-aclaude-mon-dev-b155a9121aeb2372.jsonl").write_text(
        SUB_FIRST + "\n" + "\n".join(SUB_TAIL) + "\n", encoding="utf-8")
    cold = base / "22222222-2222-2222-2222-222222222222.jsonl"
    cold.write_text(MAIN_HEAD[0] + "\n", encoding="utf-8")
    now = time.time()
    os.utime(base / "11111111-1111-1111-1111-111111111111.jsonl", (now - 60, now - 60))
    os.utime(sub_dir / "agent-aclaude-mon-dev-b155a9121aeb2372.jsonl", (now - 120, now - 120))
    os.utime(cold, (now - 3600, now - 3600))
    ancient = base / "44444444-4444-4444-4444-444444444444.jsonl"
    ancient.write_text(MAIN_HEAD[0] + chr(10), encoding="utf-8")
    os.utime(ancient, (now - 90000, now - 90000))  # 25h 前:超空闲窗不列
    return base.parent  # projects 根(采集器按 projects/<proj>/**.jsonl 两层遍历)


# ---------- 解析器单测(真实摘样) ----------

def test_sessions_active_kinds_and_fields(projects_dir: Path):
    ns = _collector()
    sessions = ns["_sessions_active"](base_dir=str(projects_dir))
    kinds = {s["session_id"]: s for s in sessions}
    assert set(kinds) == {"11111111-1111-1111-1111-111111111111",
                          "agent-aclaude-mon-dev-b155a9121aeb2372",
                          "22222222-2222-2222-2222-222222222222"}  # 25h 古会话不入;1h 冷会话空闲仍列
    main = kinds["11111111-1111-1111-1111-111111111111"]
    sub = kinds["agent-aclaude-mon-dev-b155a9121aeb2372"]
    assert main["kind"] == "main" and sub["kind"] == "subagent"
    assert main["title"] == "Claude 任务视窗"          # ai-title 优先于首问
    assert main["cwd"] == "C:\\Users\\dev1"
    assert main["model"] == "claude-sonnet-5"
    assert main["last_event_at"] == "2026-10-15T09:56:30.000Z"
    assert main["first_event_at"] == "2026-10-15T07:45:42.567Z"
    assert sub["agent"] == "claude-mon-dev"           # agentId 去尾随机段+前缀 a
    assert sub["parent_id"] == "11111111-1111-1111-1111-111111111111"  # 子代理挂父会话 id(缩进展示依据)
    assert sub["title"] is None                        # teammate 包裹行不算真实首问 → 无 ai-title 则空
    assert main["active"] is True and sub["active"] is True
    assert kinds["22222222-2222-2222-2222-222222222222"]["active"] is False  # 1h 前=空闲


def test_sessions_active_alive_filter(projects_dir: Path):
    ns = _collector()
    proj = [p for p in projects_dir.iterdir() if p.is_dir()][0]
    now = time.time()
    import calendar
    proc_start = calendar.timegm(time.strptime("2026-10-15T07:45:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    sessions = ns["_sessions_active"](base_dir=str(projects_dir), now=now,
                                      procs=[(proj.name, proc_start)])
    ids = {s["session_id"] for s in sessions}
    assert ids == {"11111111-1111-1111-1111-111111111111",
                   "agent-aclaude-mon-dev-b155a9121aeb2372"}  # 冷/古会话无进程背书,不列
    # 巡检泄漏回归(2026-10-16):常驻老进程不得给晚生会话背书(首条消息超出启动+900s)
    late = proj / "55555555-5555-5555-5555-555555555555.jsonl"
    late.write_text(json.dumps({
        "type": "user", "timestamp": "2026-10-15T08:50:00.000Z", "cwd": "C:\\Users\\dev1",
        "message": {"role": "user", "content": "晚生的无头会话"},
        "sessionId": "55555555-5555-5555-5555-555555555555",
    }) + "\n", encoding="utf-8")
    ids2 = {s["session_id"] for s in ns["_sessions_active"](
        base_dir=str(projects_dir), now=now, procs=[(proj.name, proc_start)])}
    assert "55555555-5555-5555-5555-555555555555" not in ids2
    assert ns["_sessions_active"](base_dir=str(projects_dir), now=now, procs=[]) == []  # 零进程=零存活会话

def test_partial_tail_line_dropped(tmp_path: Path):
    """大文件尾部 4KB 切在行中间:残行必须丢弃,不产生脏解析。"""
    ns = _collector()
    base = tmp_path / "projects" / "p"
    base.mkdir(parents=True)
    filler = '{"type":"file-history-snapshot","pad":"' + "x" * 6000 + '"}'
    lines = [filler] + MAIN_TAIL
    f = base / "33333333-3333-3333-3333-333333333333.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    now = time.time()
    os.utime(f, (now - 30, now - 30))
    sessions = ns["_sessions_active"](base_dir=str(base.parent))
    assert len(sessions) == 1
    assert sessions[0]["last_event_at"] == "2026-10-15T09:56:30.000Z"  # 尾部真实行解析成功


def test_friendly_agent_variants():
    ns = _collector()
    friendly = ns["_friendly_agent"]
    assert friendly("aclaude-mon-dev-b155a9121aeb2372") == "claude-mon-dev"
    assert friendly("agent-aclaude-suite-os-dev-f641a927f22e9c8a") == "claude-suite-os-dev"
    assert friendly(None) is None
    assert friendly("") is None


def test_empty_base_dir_returns_empty(tmp_path: Path):
    ns = _collector()
    assert ns["_sessions_active"](base_dir=str(tmp_path / "nope")) == []


# ---------- 编排:单机失败隔离 + 缓存(全程 fake,不碰 ssh) ----------

@pytest.fixture(autouse=True)
def _reset_cache():
    ct._claude_tasks_cache["ts"] = 0.0
    ct._claude_tasks_cache["payload"] = None
    yield
    ct._claude_tasks_cache["ts"] = 0.0
    ct._claude_tasks_cache["payload"] = None


def test_fetch_machine_entry_offline_isolation(monkeypatch):
    def boom(machine_id):
        raise ct.ClaudeTasksMachineError("ssh 连不上")
    monkeypatch.setattr(ct, "claude_tasks_fetch", boom)
    entry = ct._fetch_machine_entry("176")
    assert entry["online"] is False
    assert "ssh 连不上" in entry["error"]
    assert entry["data"] is None and entry["machine_id"] == "176"


def test_summary_all_machines_and_cache(monkeypatch):
    calls: list[str] = []

    def fake_fetch(machine_id):
        calls.append(machine_id)
        return {"ok": True, "processes": [], "sessions": [], "tmux": [],
                "hostname": "h-" + machine_id, "notes": []}

    monkeypatch.setattr(ct, "claude_tasks_fetch", fake_fetch)
    out = ct.claude_tasks_summary()
    assert sorted(m["machine_id"] for m in out["machines"]) == ["167", "176"]  # 229 按用户拍板不进默认清单
    assert all(m["online"] for m in out["machines"])
    assert out["machines"][0]["data"]["hostname"] == "h-167"
    again = ct.claude_tasks_summary()          # 10s 缓存内不重拉
    assert calls.count("167") == 1 and again is out
    forced = ct.claude_tasks_summary(force=True)  # force 绕缓存
    assert forced is not out and calls.count("167") == 2


# ---------- 端点联测(真 HTTP,fake 采集) ----------

def _request(base_url: str, path: str, cookie: str | None = None):
    headers = {"Cookie": cookie} if cookie else {}
    req = urllib.request.Request(f"{base_url}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_endpoint_returns_per_machine_payload(tmp_path: Path, monkeypatch):
    from dashboard.server import create_server

    def fake_fetch(machine_id):
        if machine_id == "176":
            raise ct.ClaudeTasksMachineError("fake ssh down")
        return {"ok": True, "hostname": "h-" + machine_id, "processes":
                [{"pid": 1, "command": "claude", "cwd": "/home/jks",
                  "started_at": time.time() - 60, "rss_bytes": 1024}],
                "sessions": [], "tmux": ["leader"], "notes": []}

    monkeypatch.setattr(ct, "claude_tasks_fetch", fake_fetch)
    server = create_server("127.0.0.1", 0, tmp_path / "dash.sqlite3")
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # 登录闸:未登录 401(浏览器通道豁免不含本端点)
        with pytest.raises(urllib.request.HTTPError) as err:
            _request(base_url, "/api/machines/claude-tasks")
        assert err.value.code == 401
        login = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "yumiao"}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(login, timeout=5) as resp:
            cookie = resp.headers.get("Set-Cookie", "").split(";")[0]
        status, body = _request(base_url, "/api/machines/claude-tasks", cookie)
        assert status == 200
        by_id = {m["machine_id"]: m for m in body["machines"]}
        assert by_id["176"]["online"] is False and "fake ssh down" in by_id["176"]["error"]
        assert by_id["167"]["online"] is True
        assert by_id["167"]["data"]["tmux"] == ["leader"]
    finally:
        server.shutdown()
        server.server_close()


def test_zh_title_leader_bootstrap(tmp_path: Path):
    """英文系统引导首问 → 中文描述(2026-10-15 用户反馈:主会话描述须中文)。"""
    ns = _collector()
    base = tmp_path / "projects" / "C--home-jks"
    base.mkdir(parents=True)
    lines = [
        '{"type":"user","message":{"role":"user","content":"Read ~/leader/LEADER.md and follow it. '
        'You are this machine small leader. Reply OK when read."}}',
    ]
    p = base / "33333333-3333-3333-3333-333333333333.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    now = time.time()
    os.utime(p, (now - 60, now - 60))
    sessions = ns["_sessions_active"](base_dir=str(tmp_path / "projects"))
    assert sessions[0]["title"] == "值守小领导"
    assert ns["_zh_title"]("This session is being continued from a previous conversation") == "续接会话"
    assert ns["_zh_title"]("普通中文首问不改写") == "普通中文首问不改写"
