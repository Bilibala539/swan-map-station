from __future__ import annotations

import gc
import json
import os
import re
import tempfile
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard.server import create_server

# 平台登录闸(2026-09-29)后的测试适配:浏览器面 API 未登录 401,这里对 401 自动
# 用预设账号登录一次(按 base_url 缓存 cookie)重试。机器通道豁免不受影响。
_SESSION_COOKIES: dict[str, str] = {}


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")

    def send(cookie: str | None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request_object = urllib.request.Request(
            f"{base_url}{path}", data=body, method=method, headers=headers)
        with urllib.request.urlopen(request_object, timeout=5) as response:
            return response.status, response.headers.get_content_type(), response.read()

    try:
        return send(_SESSION_COOKIES.get(base_url))
    except urllib.error.HTTPError as error:
        if error.code != 401 or path.startswith("/api/auth/"):
            raise
        login = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "yumiao"}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(login, timeout=5) as response:
            _SESSION_COOKIES[base_url] = response.headers.get("Set-Cookie", "").split(";")[0]
        return send(_SESSION_COOKIES[base_url])


def test_dashboard_run_lifecycle_and_history():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, body = request(base_url, "/api/health")
            assert status == 200
            assert json.loads(body)["status"] == "ok"

            status, _, _ = request(base_url, "/api/runs", "POST", {
                "run_id": "run-android-1",
                "platform": "android",
                "app_version": "1.0.0",
                "device": "device-1",
                "started_at": "2026-09-04T01:00:00+00:00",
            })
            assert status == 201

            for case_status in ("passed", "failed", "skipped"):
                status, _, _ = request(base_url, "/api/runs/run-android-1/cases", "POST", {
                    "case_id": f"case-{case_status}",
                    "name": f"Case {case_status}",
                    "status": case_status,
                    "duration_seconds": 2.5,
                    "log": "OpenCV/OCR result",
                    "artifacts": {"screenshot": "case.png"},
                })
                assert status == 201

            status, _, body = request(base_url, "/api/runs/run-android-1/complete", "POST", {
                "status": "failed",
                "ended_at": "2026-09-04T01:01:00+00:00",
            })
            assert status == 200
            run = json.loads(body)
            assert run["total_cases"] == 3
            assert run["passed_cases"] == 1
            assert run["failed_cases"] == 1
            assert run["skipped_cases"] == 1
            assert run["pass_rate"] == 33.3
            assert len(run["cases"]) == 3

            status, _, body = request(base_url, "/api/runs?platform=android")
            assert status == 200
            assert [item["run_id"] for item in json.loads(body)["items"]] == ["run-android-1"]

            status, content_type, body = request(base_url, "/")
            assert status == 200
            assert content_type == "text/html"
            assert "HiVi 测试平台" in body.decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


WEBHOOK_SECRET = "hook-secret"
FULL_SHA = "ab12cd34ef56" * 3 + "ab12"


def test_gitlab_webhook_auto_build():
    with tempfile.TemporaryDirectory() as temporary_directory:
        # mock Jenkins:对 buildWithParameters 回 201+Location,捕获 form 参数
        captured = []

        class MockJenkins(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                captured.append(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode("utf-8"))))
                self.send_response(201)
                self.send_header("Location", "http://mock-jenkins/queue/item/88/")
                self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN", "GITLAB_WEBHOOK_SECRET")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
            "GITLAB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path: str, payload: dict, token: str | None = WEBHOOK_SECRET):
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["X-GitLab-Token"] = token
            request_object = urllib.request.Request(
                f"{base_url}{path}", data=json.dumps(payload).encode("utf-8"),
                method="POST", headers=headers)
            try:
                with urllib.request.urlopen(request_object, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        def push(ref: str = "refs/heads/dev", after: str = FULL_SHA,
                 user: str = "wujianneng", kind: str = "push") -> dict:
            return {"object_kind": kind, "ref": ref, "after": after, "user_name": user,
                    "project": {"path_with_namespace": "swan-app/swan-audio/hivi-network-flutterapp"}}

        try:
            # 未带/带错 token → 401(非 2xx 交 GitLab 丢弃重试策略)
            status, _ = post("/api/webhooks/gitlab", push(), token=None)
            assert status == 401
            status, _ = post("/api/webhooks/gitlab", push(), token="wrong")
            assert status == 401
            assert captured == []

            # 非 push 事件 / 映射外分支 → 恒 200 skip,不碰 Jenkins
            status, body = post("/api/webhooks/gitlab", push(kind="note"))
            assert status == 200 and body["action"] == "skipped"
            status, body = post("/api/webhooks/gitlab", push(ref="refs/heads/harmony"))
            assert status == 200 and body["action"] == "skipped"
            status, body = post("/api/webhooks/gitlab", push(after="0" * 40))
            assert status == 200 and body["action"] == "skipped"
            assert captured == []

            # dev 合入推送 → 触发 Jenkins,参数服务端组装
            status, body = post("/api/webhooks/gitlab", push())
            assert status == 200 and body["action"] == "triggered"
            assert body["job"] == "swan-build-android"
            assert body["commit"] == FULL_SHA[:7]
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "test",
                                    "COMMIT": FULL_SHA, "NOTES": "webhook%3Awujianneng"}

            # webhook 触发同样落流水线任务行(2026-09-24):流水线页"最近任务"可见
            assert body["pipeline_job"]
            status, _, jobs_body = request(base_url, "/api/pipeline/jobs?limit=30")
            rows = json.loads(jobs_body)["items"]
            assert len(rows) == 1
            row = rows[0]
            assert row["trigger_source"] == "webhook" and row["status"] == "pending"
            assert row["branch"] == "dev" and row["jenkins_job"] == "swan-build-android"
            assert row["result"]["jenkins_queue_id"] == 88
            assert row["result"]["build"]["commit"] == FULL_SHA[:7]

            # master 合入推送 → formal(正式服/release)自动打包
            status, body = post("/api/webhooks/gitlab", push(ref="refs/heads/master"))
            assert status == 200 and body["action"] == "triggered"
            assert captured[-1] == {"BRANCH": "master", "SERVER_TYPE": "formal",
                                    "COMMIT": FULL_SHA, "NOTES": "webhook%3Awujianneng"}

            # pusher 带命令特殊字符 → 不进 notes,回退固定前缀(坑 15 注入面)
            status, body = post("/api/webhooks/gitlab", push(user="x & dir"))
            assert status == 200 and body["action"] == "triggered"
            assert captured[-1]["NOTES"] == "webhook"

            # 同 commit 已登记 → 去重 skip(先手工登记同 commit 行)
            status, _, _ = request(base_url, "/api/apks", "POST", {
                "apk_id": f"swan-dev-{FULL_SHA[:7]}-20260922-test", "branch": "dev",
                "commit_hash": FULL_SHA[:7], "server_type": "test",
                "file_path": "/tmp/x.apk", "source": "auto-build",
            })
            assert status == 201
            status, body = post("/api/webhooks/gitlab", push())
            assert status == 200 and body["action"] == "skipped"
            assert "已登记" in body["reason"]

            # core 仓(dev)推送(2026-09-23):同 sha 已按 app 口径登记也不去重,
            # 触发打包但不传 COMMIT(COMMIT 是 app 仓要签出的 commit,core sha 不适用)
            def push_core(ref: str = "refs/heads/dev") -> dict:
                return {"object_kind": "push", "ref": ref, "after": FULL_SHA,
                        "user_name": "wujianneng",
                        "project": {"path_with_namespace":
                                    "swan-app/swan-audio/hivi_network_flutter_core"}}

            status, body = post("/api/webhooks/gitlab", push_core())
            assert status == 200 and body["action"] == "triggered"
            assert "commit" not in body
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "test",
                                    "NOTES": "webhook%3Awujianneng"}
            # core master → formal;映射外分支照旧 skip
            status, body = post("/api/webhooks/gitlab", push_core(ref="refs/heads/master"))
            assert status == 200 and body["action"] == "triggered"
            assert captured[-1] == {"BRANCH": "master", "SERVER_TYPE": "formal",
                                    "NOTES": "webhook%3Awujianneng"}
            status, body = post("/api/webhooks/gitlab", push_core(ref="refs/heads/harmony"))
            assert status == 200 and body["action"] == "skipped"

            # webhook 行累计 5 条:app dev/master/坏 pusher + core dev/master;skip 不落行
            status, _, jobs_body = request(base_url, "/api/pipeline/jobs?limit=30")
            rows = json.loads(jobs_body)["items"]
            assert len(rows) == 5
            assert all(row["trigger_source"] == "webhook" for row in rows)
            assert sorted(row["branch"] for row in rows) == ["dev"] * 3 + ["master"] * 2
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关



def test_manual_build_notes_prefix():
    """手动打包(包管理页"主动打包",POST /api/builds)NOTES 记 manual: 前缀
    (2026-09-29 来源三分类):带备注 manual:<备注>,无备注固定 manual,
    与 pipeline:/daily:/webhook: 并列供前端分类展示。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        captured = []

        class MockJenkins(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                captured.append(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode("utf-8"))))
                self.send_response(201)
                self.send_header("Location", "http://mock-jenkins/queue/item/88/")
                self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        try:
            # 带备注 → manual:<备注>(中文经 quote 编码;备注黑名单字符已在上游拒绝)
            status, _, body = request(base_url, "/api/builds", "POST", {
                "branch": "dev", "server_type": "test", "notes": "冒烟"})
            assert status == 200 and json.loads(body)["ok"] is True
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "test",
                                    "NOTES": "manual%3A%E5%86%92%E7%83%9F"}

            # 无备注 → 固定 manual(前端仍按前缀归"流水线主动打包")
            status, _, body = request(base_url, "/api/builds", "POST",
                                      {"branch": "dev", "server_type": "formal"})
            assert status == 200 and json.loads(body)["ok"] is True
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "formal",
                                    "NOTES": "manual"}
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关



def test_webhook_build_resolver():
    """webhook 触发的流水线任务行:队列项已被清后按 NOTES webhook 前缀回查闭环(2026-09-24)。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        # 构建时间戳取"未来 1 分钟":回查按 timestamp >= 任务 created_at 筛候选,
        # 测试启动时刻若晚于任务创建会误伤,这里给足余量
        build_ts = int(time.time() * 1000) + 60_000

        class MockJenkins(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                self.rfile.read(length)
                self.send_response(201)
                self.send_header("Location", "http://mock-jenkins/queue/item/77/")
                self.end_headers()

            def do_GET(self):
                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                if "/queue/item/77/" in self.path:
                    self.send_response(404)  # 队列项已被清 → 走第二遍候选回查
                    self.end_headers()
                elif "tree=actions[parameters" in self.path:
                    # build_parameters 读原生 actions 平铺;NOTES 保持触发时的 URL 编码态
                    reply({"actions": [{"parameters": [
                        {"name": "NOTES", "value": "webhook%3Awujianneng"}]}]})
                elif "tree=builds[" in self.path:
                    reply({"builds": [{"number": 21, "result": None,
                                       "building": False, "timestamp": build_ts}]})
                elif "/job/swan-build-android/21/" in self.path:
                    reply({"building": False, "result": "SUCCESS"})
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN", "GITLAB_WEBHOOK_SECRET")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
            "GITLAB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        try:
            # core 仓推送触发(无 COMMIT):任务行落库,queue_id=77
            payload = {"object_kind": "push", "ref": "refs/heads/dev", "after": FULL_SHA,
                       "user_name": "wujianneng",
                       "project": {"path_with_namespace":
                                   "swan-app/swan-audio/hivi_network_flutter_core"}}
            request_object = urllib.request.Request(
                f"{base_url}/api/webhooks/gitlab",
                data=json.dumps(payload).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json",
                         "X-GitLab-Token": WEBHOOK_SECRET})
            with urllib.request.urlopen(request_object, timeout=5) as response:
                body = json.loads(response.read())
            assert body["action"] == "triggered" and body["queue_id"] == 77
            job_id = body["pipeline_job"]
            assert job_id

            # 列表读取 → queue 404 → 候选回查(NOTES webhook 前缀)→ 构建 21 SUCCESS
            status, _, jobs_body = request(base_url, "/api/pipeline/jobs?limit=30")
            rows = json.loads(jobs_body)["items"]
            row = next(j for j in rows if j["job_id"] == job_id)
            assert row["trigger_source"] == "webhook"
            assert row["jenkins_build"] == 21
            assert row["status"] == "succeeded" and row["finished_at"]
            assert row["result"]["build"]["server_type"] == "test"
            assert row["result"]["jenkins_queue_id"] == 77
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关


def test_jenkins_build_sync():
    """Jenkins↔平台对账(2026-09-24):手动构建/历史遗留构建同步可见,全状态映射。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        build_ts = int(time.time() * 1000) + 60_000
        state = {"b31": {"building": True, "result": None}}

        class MockJenkins(BaseHTTPRequestHandler):
            def do_GET(self):
                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                if "tree=builds[" in self.path:
                    reply({"builds": [
                        {"number": 31, "result": state["b31"]["result"],
                         "building": state["b31"]["building"], "timestamp": build_ts},
                        {"number": 32, "result": "SUCCESS", "building": False,
                         "timestamp": build_ts}]})
                elif "tree=actions[parameters" in self.path:
                    # 构建入参:31 手动构建(无 NOTES);32 webhook 遗留构建
                    if "/31/" in self.path:
                        reply({"actions": [{"parameters": [
                            {"name": "BRANCH", "value": "dev"},
                            {"name": "SERVER_TYPE", "value": "test"}]}]})
                    else:
                        reply({"actions": [{"parameters": [
                            {"name": "BRANCH", "value": "dev"},
                            {"name": "SERVER_TYPE", "value": "test"},
                            {"name": "NOTES", "value": "webhook%3Awujianneng"}]}]})
                elif "/job/swan-build-android/31/" in self.path:
                    reply({"building": state["b31"]["building"],
                           "result": state["b31"]["result"]})
                elif "/job/swan-build-android/32/" in self.path:
                    reply({"building": False, "result": "SUCCESS"})
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        # 撞号回归(2026-09-24 线上发现):预置一条 swan-autotest 测试执行行,
        # 其 jenkins_build=32 与打包构建 32 号撞号——占用只认同一 Jenkins 任务
        import sqlite3
        connection = sqlite3.connect(str(Path(temporary_directory) / "dashboard.sqlite3"))
        connection.execute(
            "INSERT INTO pipeline_jobs (job_id, trigger_source, status, created_at,"
            " finished_at, jenkins_job, jenkins_build)"
            " VALUES ('job-seed-autotest', 'manual', 'succeeded', ?, ?, 'swan-autotest', 32)",
            ("2026-09-01T00:00:00+00:00", "2026-09-01T00:01:00+00:00"))
        connection.commit()
        connection.close()

        try:
            # 平台从未触发过任何打包任务:列表读取即对账,31/32 全部补行可见
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            items = json.loads(body)["items"]
            assert len(items) == 3  # 2 条对账补行 + 1 条预置测试行
            rows = {j["jenkins_build"]: j for j in items
                    if j.get("jenkins_job") == "swan-build-android"}
            assert set(rows) == {31, 32}
            # 31:Jenkins 手动构建(building)→ build 来源 + 运行中
            assert rows[31]["trigger_source"] == "build"
            assert rows[31]["status"] == "running"
            assert rows[31]["branch"] == "dev"
            assert rows[31]["result"]["build"]["notes"] == "jenkins-manual"
            # 32:webhook 遗留构建(NOTES 前缀归类)→ webhook 来源 + 成功
            assert rows[32]["trigger_source"] == "webhook"
            assert rows[32]["status"] == "succeeded" and rows[32]["finished_at"]

            # 31 构建完成 → 下次列表读取闭环为成功
            state["b31"] = {"building": False, "result": "SUCCESS"}
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            rows = {j["jenkins_build"]: j for j in json.loads(body)["items"]
                    if j.get("jenkins_job") == "swan-build-android"}
            assert rows[31]["status"] == "succeeded" and rows[31]["finished_at"]
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关


def test_pipeline_reconcile_no_dup_window():
    """对账占用判定不能走"最近 N 行"窗口(2026-09-28 线上教训):

    补录行 created_at 落的是构建开始时间,按 created_at 排序永远沉在窗口外,
    旧版用窗口判占用导致同一构建被反复补行(线上 #1 积到 9 行)。占用须全量
    查;存量重复行由对账入口自愈去重(保 created_at 最早的一行)。
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        old_ms = int(time.time() * 1000) - 90 * 24 * 3600 * 1000  # 90 天前:必在窗口外
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(old_ms / 1000))

        class MockJenkins(BaseHTTPRequestHandler):
            def do_GET(self):
                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                if "tree=builds[" in self.path:
                    reply({"builds": [
                        {"number": 1, "result": "FAILURE", "building": False,
                         "timestamp": old_ms},
                        {"number": 2, "result": "SUCCESS", "building": False,
                         "timestamp": old_ms}]})
                elif "tree=actions[parameters" in self.path:
                    reply({"actions": [{"parameters": [
                        {"name": "BRANCH", "value": "dev"},
                        {"name": "SERVER_TYPE", "value": "test"}]}]})
                elif "/job/swan-build-android/1/" in self.path:
                    reply({"building": False, "result": "FAILURE"})
                elif "/job/swan-build-android/2/" in self.path:
                    reply({"building": False, "result": "SUCCESS"})
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        # 预置线上病态数据:构建 1 号两行(旧版对账反复补行所致,created_at=
        # 构建开始,沉在窗口外)+ 55 条近期测试执行行灌满 50 行窗口
        import sqlite3
        connection = sqlite3.connect(str(Path(temporary_directory) / "dashboard.sqlite3"))
        for suffix in ("a", "b"):
            connection.execute(
                "INSERT INTO pipeline_jobs (job_id, trigger_source, status, created_at,"
                " finished_at, jenkins_job, jenkins_build)"
                " VALUES (?, 'build', 'failed', ?, ?, 'swan-build-android', 1)",
                (f"job-dup-{suffix}", old_iso, old_iso))
        now_ms = time.time()
        for index in range(55):
            iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                time.gmtime(now_ms - index))
            connection.execute(
                "INSERT INTO pipeline_jobs (job_id, trigger_source, status, created_at,"
                " finished_at, jenkins_job, jenkins_build)"
                " VALUES (?, 'manual', 'succeeded', ?, ?, 'swan-autotest', ?)",
                (f"job-filler-{index:02d}", iso, iso, 7000 + index))
        connection.commit()
        connection.close()

        try:
            # 第一次读取:重复行自愈(2 行留 job-dup-a)+ 为 2 号补行,撞号任务不受影响
            _, _, body = request(base_url, "/api/pipeline/jobs?limit=100")
            items = json.loads(body)["items"]
            build_rows = [j for j in items if j.get("jenkins_job") == "swan-build-android"]
            assert sorted(j["jenkins_build"] for j in build_rows) == [1, 2]
            assert next(j for j in build_rows
                        if j["jenkins_build"] == 1)["job_id"] == "job-dup-a"
            assert len([j for j in items
                        if j.get("jenkins_job") == "swan-autotest"]) == 55

            # 第二次读取:1/2 号行的 created_at 已在 50 行窗口外,占用判定仍须
            # 命中——旧版在此会再补一行 1 号
            _, _, body = request(base_url, "/api/pipeline/jobs?limit=100")
            items = json.loads(body)["items"]
            build_rows = [j for j in items if j.get("jenkins_job") == "swan-build-android"]
            assert sorted(j["jenkins_build"] for j in build_rows) == [1, 2]
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关


def test_build_pipeline_config():
    """打包型流水线(kind=build):配置校验 / run 触发打包 job / agent 不认领 /
    列表读取时向 Jenkins 惰性回查终态。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        captured = []
        build_result = {"building": False, "result": "SUCCESS"}

        class MockJenkins(BaseHTTPRequestHandler):
            queue_counter = 8  # 类属性:handler 每请求新建实例,计数不能挂 self

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                captured.append(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode("utf-8"))))
                MockJenkins.queue_counter += 1
                self.send_response(201)
                self.send_header("Location",
                                 f"http://mock-jenkins/queue/item/{MockJenkins.queue_counter}/")
                self.end_headers()

            def do_GET(self):
                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                queue_match = re.search(r"/queue/item/(\d+)/", self.path)
                build_match = re.search(r"/job/swan-build-android/(\d+)/", self.path)
                if queue_match:
                    # 构建号=队列项号(9,10,11…):每次触发各得一个构建号。不能共用——
                    # 同构建号双行会被对账去重删一行(4cf7a32),测试会随机挂
                    reply({"executable": {"number": int(queue_match.group(1))}})
                elif build_match:
                    reply(build_result)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        def create(payload: dict):
            # 400 属预期分支,request() 不捕 HTTPError,这里照 webhook 测试自行捕
            try:
                return request(base_url, "/api/pipeline-configs", "POST", payload)
            except urllib.error.HTTPError as error:
                return error.code, None, error.read()

        try:
            # 校验:server_type 非法 / branch 带命令字符(坑 15)→ 400
            status, _, _ = create({
                "config_id": "PIPE-BUILD-BAD", "name": "bad", "kind": "build",
                "schedule_type": "daily", "schedule_param": {"time": "04:17"},
                "build_param": {"branch": "dev", "server_type": "staging"}})
            assert status == 400
            status, _, _ = create({
                "config_id": "PIPE-BUILD-BAD", "name": "bad", "kind": "build",
                "schedule_type": "daily", "schedule_param": {"time": "04:17"},
                "build_param": {"branch": "dev & dir", "server_type": "test"}})
            assert status == 400

            # 合法创建:suite_id/include_build 强制置空,build_param 回显
            status, _, body = create({
                "config_id": "PIPE-BUILD-TEST", "name": "每日打包-测试服", "kind": "build",
                "schedule_type": "daily", "schedule_param": {"time": "04:17"},
                "build_param": {"branch": "dev", "server_type": "test"}})
            assert status == 201
            config = json.loads(body)
            assert config["kind"] == "build" and config["suite_id"] == ""
            assert config["include_build"] == 0
            assert config["build"] == {"branch": "dev", "server_type": "test"}
            assert config["next_run_at"]  # 调度器靠它到点触发

            # run:触发 swan-build-android(NOTES 编码同 /api/builds),任务行 build 来源
            status, _, body = request(base_url, "/api/pipeline-configs/PIPE-BUILD-TEST/run",
                                      "POST", {})
            assert status == 201  # run 端点惯例返回 CREATED
            job = json.loads(body)["job"]
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "test",
                                    "NOTES": "pipeline%3APIPE-BUILD-TEST"}
            assert job["trigger_source"] == "build" and job["status"] == "pending"
            assert job["jenkins_job"] == "swan-build-android"  # 派发已回填
            assert job["result"]["build"]["pipeline"] == "PIPE-BUILD-TEST"

            # 轮询认领不碰 build 任务(无 agent 跑用例)→ 无可认领任务
            try:
                request(base_url, "/api/pipeline/claim", "POST", {"agent": "t"})
                claim_status = 200
            except urllib.error.HTTPError as error:
                claim_status = error.code
            assert claim_status == 404

            # 列表读取 → resolver 惰性回查:queue→构建号 9 → SUCCESS → succeeded
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            assert status == 200
            jobs = json.loads(body)["items"]
            target = next(j for j in jobs if j["trigger_source"] == "build")
            assert target["status"] == "succeeded" and target["jenkins_build"] == 9
            assert target["finished_at"]

            # 构建失败同样闭环:FAILURE → failed
            build_result.update({"result": "FAILURE"})
            status, _, body = request(base_url, "/api/pipeline-configs/PIPE-BUILD-TEST/run",
                                      "POST", {})
            assert status == 201  # run 端点惯例返回 CREATED
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            jobs = json.loads(body)["items"]
            failed_job = next(j for j in jobs if j["trigger_source"] == "build"
                              and j["status"] != "succeeded")
            assert failed_job["status"] == "failed"
            assert "FAILURE" in failed_job["result"]["error"]

            # 定时通道:调度器带 {"source":"schedule"} → NOTES 记 daily: 前缀(定时自动构建)
            build_result.update({"result": "SUCCESS"})
            status, _, body = request(base_url, "/api/pipeline-configs/PIPE-BUILD-TEST/run",
                                      "POST", {"source": "schedule"})
            assert status == 201
            assert captured[-1] == {"BRANCH": "dev", "SERVER_TYPE": "test",
                                    "NOTES": "daily%3APIPE-BUILD-TEST"}

            # 测试流水线行为不变:kind 缺省 suite,run 照旧走执行任务通道
            status, _, body = create({
                "config_id": "PIPE-SUITE-OK", "name": "普通", "kind": "suite",
                "schedule_type": "once", "schedule_param": {"run_at": "2099-01-01T09:00"}})
            assert status == 201
            config = json.loads(body)
            assert config["kind"] == "suite" and config["build"] == {}

            # 打包流水线按平台选 job(2026-10-15):platform=flutter-ios → swan-build-IOS。
            # 放测试末尾:该 job 的构建状态查询路径(swam-build-IOS)不在本 mock 的
            # build_match 内,若插在中段会以 pending 态污染后续"取最新 build 任务"断言
            status, _, body = create({
                "config_id": "PIPE-BUILD-IOS", "name": "iOS 每日打包", "kind": "build",
                "platform": "flutter-ios",
                "schedule_type": "daily", "schedule_param": {"time": "04:17"},
                "build_param": {"branch": "dev", "server_type": "test"}})
            assert status == 201
            status, _, body = request(base_url, "/api/pipeline-configs/PIPE-BUILD-IOS/run",
                                      "POST", {})
            assert status == 201
            job = json.loads(body)["job"]
            assert job["platform"] == "flutter-ios"
            assert job["jenkins_job"] == "swan-build-IOS"  # 派发回填 iOS job
            assert job["result"]["build"]["pipeline"] == "PIPE-BUILD-IOS"
            # 配置回读保留 flutter-ios(VALID_PLATFORMS 已收录)
            status, _, body = request(base_url, "/api/pipeline-configs/PIPE-BUILD-IOS")
            assert json.loads(body)["platform"] == "flutter-ios"
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关


def test_build_resolver_same_config_collision():
    """同 config 一天多次触发且队列项已被 Jenkins 清掉:组内按触发序先到先得,
    老任务对老构建、新任务对新构建(2026-09-23 线上事故:04:17 任务错配到
    13:31 同 config 构建 12/13 的根因即回查只取最新一条)。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        build_status = {9: "FAILURE"}  # 构建 8 → SUCCESS(默认),9 → FAILURE

        class MockJenkins(BaseHTTPRequestHandler):
            queue_counter = 900  # 类属性:handler 每请求新建实例,计数不能挂 self

            def do_POST(self):
                MockJenkins.queue_counter += 1
                self.send_response(201)
                self.send_header(
                    "Location", f"http://mock-jenkins/queue/item/{MockJenkins.queue_counter}/")
                self.end_headers()

            def do_GET(self):
                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                if "/queue/item/" in self.path:
                    # 队列项一律 404:模拟已出结果被清,逼回查路径
                    self.send_response(404)
                    self.end_headers()
                elif "tree=actions[parameters" in self.path:
                    # 参数查询(build_parameters 与 job_status 同路径,靠 tree 区分)
                    reply({"actions": [{"parameters": [
                        {"name": "NOTES", "value": "pipeline%3APIPE-COLLIDE"}]}]})
                elif self.path.startswith("/job/swan-build-android/api/json"):
                    now_ms = int(time.time() * 1000)
                    reply({"builds": [
                        {"number": 9, "result": None, "building": False,
                         "timestamp": now_ms + 10},
                        {"number": 8, "result": None, "building": False,
                         "timestamp": now_ms}]})
                elif self.path.startswith("/job/swan-build-android/"):
                    number = int(self.path.split("/job/swan-build-android/")[1].split("/")[0])
                    reply({"building": False, "result": build_status.get(number, "SUCCESS")})
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, _ = request(base_url, "/api/pipeline-configs", "POST", {
                "config_id": "PIPE-COLLIDE", "name": "同日多跑", "kind": "build",
                "schedule_type": "daily", "schedule_param": {"time": "04:17"},
                "build_param": {"branch": "dev", "server_type": "test"}})
            assert status == 201

            # 同 config 连跑两次 → 两个 pending 任务,queue_id 递增
            queue_ids = []
            for _ in range(2):
                status, _, body = request(base_url, "/api/pipeline-configs/PIPE-COLLIDE/run",
                                          "POST", {})
                assert status == 201  # run 端点惯例返回 CREATED
                job = json.loads(body)["job"]
                queue_ids.append(job["result"]["jenkins_queue_id"])
            assert queue_ids == sorted(queue_ids) and len(set(queue_ids)) == 2

            # 列表读取触发 resolver:队列项 404 → 组内候选 [8, 9] 按触发序先到先得
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            assert status == 200
            jobs = json.loads(body)["items"]
            by_queue = {j["result"]["jenkins_queue_id"]: j for j in jobs
                        if j["trigger_source"] == "build"}
            assert sorted(by_queue) == queue_ids
            first, second = (by_queue[q] for q in queue_ids)
            # 老任务(先触发)对老构建 8 且独立闭环为成功,而非抢到最新构建 9
            assert first["jenkins_build"] == 8 and first["status"] == "succeeded"
            assert first["finished_at"]
            assert second["jenkins_build"] == 9 and second["status"] == "failed"
            assert "FAILURE" in second["result"]["error"]
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关


def test_apk_reconcile_from_jenkins():
    """包管理↔Jenkins 对账(2026-09-28):SUCCESS 构建缺行补录/手跑行仅修关联。

    覆盖:登记成功行补录(新旧 sha 格式)、登记失败行(产物仍在)补录、
    FAILURE/进行中不动、已存在行绝不覆盖业务字段只回填空缺 jenkins 关联。
    """
    with tempfile.TemporaryDirectory() as temporary_directory:
        from datetime import datetime, timezone

        build_ts = int(time.time() * 1000) + 120_000
        commit_32 = "cafe12a" + "a" * 33
        commit_33 = "def0123" + "b" * 33
        commit_36 = "beef45c" + "c" * 33
        sha64 = "ab" * 32
        apk_32 = "swan-dev-cafe12a-20260928-test"
        apk_33 = "swan-dev-def0123-20260928-formal"
        apk_36 = "swan-dev-beef45c-20260928-test"
        builds = [
            {"number": 36, "result": "SUCCESS", "building": False,
             "timestamp": build_ts + 4000},
            {"number": 35, "result": None, "building": True,
             "timestamp": build_ts + 3000},
            {"number": 34, "result": "FAILURE", "building": False,
             "timestamp": build_ts + 2000},
            {"number": 33, "result": "SUCCESS", "building": False,
             "timestamp": build_ts + 1000},
            {"number": 32, "result": "SUCCESS", "building": False,
             "timestamp": build_ts},
        ]
        consoles = {
            "32": ("== 步骤 1/3 检出源码 ==\n"
                   f"检出完成 commit={commit_32}\n"
                   "== 步骤 2/3 flutter build apk --profile ==\n"
                   "== 步骤 3/3 登记平台(http://x/api/apks server_type=test)==\n"
                   f"登记成功 apk_id={apk_32} sha256=0123456789ab… server_type=test\n"
                   f"归档成功 bytes=82767093 → /opt/swan-build/apks/{apk_32}.apk\n"),
            "33": ("== 步骤 1/3 检出源码 ==\n"
                   f"检出完成 commit={commit_33}\n"
                   "== 步骤 2/3 flutter build apk --release ==\n"
                   "== 步骤 3/3 登记平台(http://x/api/apks server_type=formal)==\n"
                   f"登记失败:HTTP Error 502: bad gateway(产物仍在 /opt/swan-build/apks/{apk_33}.apk)\n"),
            "36": ("== 步骤 1/3 检出源码 ==\n"
                   f"检出完成 commit={commit_36}\n"
                   "== 步骤 2/3 flutter build apk --profile ==\n"
                   f"登记成功 apk_id={apk_36} sha256={sha64} server_type=test\n"
                   f"归档成功 bytes=58935403 → /opt/swan-build/apks/{apk_36}.apk\n"),
        }
        params = {
            "32": [{"name": "BRANCH", "value": "dev"},
                   {"name": "SERVER_TYPE", "value": "test"}],
            "33": [{"name": "BRANCH", "value": "dev"},
                   {"name": "SERVER_TYPE", "value": "formal"}],
            "36": [{"name": "BRANCH", "value": "dev"},
                   {"name": "SERVER_TYPE", "value": "test"}],
        }

        class MockJenkins(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith("/consoleText"):
                    number = self.path.split("/")[-2]
                    if number not in consoles:
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = consoles[number].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                def reply(payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                if "tree=builds[" in self.path:
                    reply({"builds": builds})
                elif "tree=actions" in self.path:
                    number = self.path.split("/")[-3]  # /job/<job>/<n>/api/json
                    reply({"actions": [{"parameters": params.get(number, [])}]})
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN",
                    "JENKINS_BUILD_JOB")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        os.environ.pop("JENKINS_BUILD_JOB", None)  # 走默认 swan-build-android
        server = create_server("127.0.0.1", 0,
                               Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        import sqlite3
        connection = sqlite3.connect(str(Path(temporary_directory) / "dashboard.sqlite3"))
        # 预置手跑补录包行(32 号构建产物,登记时 Jenkins 号不可知→关联为空)
        connection.execute(
            "INSERT INTO apks (apk_id, branch, commit_hash, flavor, source,"
            " file_path, file_hash, notes, server_type, build_time, jenkins_job,"
            " jenkins_build, registered_at)"
            " VALUES (?, '', '', '', 'auto-build', ?, ?, 'MANUAL-CATCHUP-0928',"
            " 'test', NULL, '', '', '2026-09-28T11:00:00+00:00')",
            (apk_32, f"/opt/swan-build/apks/{apk_32}.apk", "f" * 64))
        connection.commit()
        connection.close()

        try:
            status, _, body = request(base_url, "/api/apks?limit=50")
            assert status == 200
            rows = {item["apk_id"]: item for item in json.loads(body)["items"]}
            assert set(rows) == {apk_32, apk_33, apk_36}  # 34 失败/35 进行中不补

            # 手跑行:只回填空缺 jenkins 关联,业务字段原样
            row = rows[apk_32]
            assert row["jenkins_job"] == "swan-build-android"
            assert row["jenkins_build"] == "32"
            assert row["file_hash"] == "f" * 64
            assert row["notes"] == "MANUAL-CATCHUP-0928"
            assert row["source"] == "auto-build"
            assert row["os_type"] == "android"  # 前缀推断兜底(2026-10-15 os_type 字段)
            assert row["jenkins_log_url"]  # 可跳转 Jenkins 日志

            # 登记失败缺口:从"产物仍在"行补录,file_hash 留空注明缺口
            expected_33 = datetime.fromtimestamp(
                (build_ts + 1000) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
            row = rows[apk_33]
            assert row["source"] == "jenkins-reconcile"
            assert row["server_type"] == "formal"
            assert row["os_type"] == "android"
            assert row["branch"] == "dev"
            assert row["commit_hash"] == "def0123"
            assert row["flavor"] == "release"
            assert row["file_path"] == f"/opt/swan-build/apks/{apk_33}.apk"
            assert row["file_hash"] == ""
            assert row["build_time"] == expected_33
            assert row["registered_at"] == expected_33
            assert "登记失败" in row["notes"]

            # 登记成功全量字段(新版脚本 64 位 sha)
            row = rows[apk_36]
            assert row["file_hash"] == sha64
            assert row["commit_hash"] == "beef45c"
            assert row["flavor"] == "profile"
            assert row["file_path"] == f"/opt/swan-build/apks/{apk_36}.apk"
            assert row["jenkins_build"] == "36"
            assert row["os_type"] == "android"
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()


def test_apk_register_guard_no_overwrite():
    """登记覆盖守卫(任务 024,20261016 覆盖事故):同 apk_id 内容不同拒绝覆盖;
    同内容重登记(上传重试)放行并刷新登记时间。"""
    import pytest
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0,
                               Path(temporary_directory) / "dashboard.sqlite3")
        store = server.RequestHandlerClass.store
        full = {"apk_id": "swan-dev-abc1234-20261016-88-test", "branch": "dev",
                "commit_hash": "abc1234", "server_type": "test",
                "file_hash": "aa" * 32, "build_time": "2026-10-16 10:43:00"}
        store.register_apk({**full, "registered_at": "2026-10-16T02:43:00Z"})
        with pytest.raises(ValueError, match="禁止覆盖"):
            store.register_apk({**full, "file_hash": "bb" * 32})
        store.register_apk({**full, "registered_at": "2026-10-16T03:00:00Z"})  # 同内容重试放行
        row = store.get_apk("swan-dev-abc1234-20261016-88-test")
        assert row["registered_at"] == "2026-10-16T03:00:00Z"  # 登记时间随刷新更新,不再留自相矛盾旧值
        assert row["build_time"] == "2026-10-16 10:43:00"
        server.server_close()

def test_apk_os_type_infer_validate_filter():
    """os_type 字段(2026-10-15):显式值优先,缺省按 apk_id 前缀推断(swan-ios- → ios);
    非法值 400;?os_type= 过滤(bogus 忽略、可与 server_type 组合);对账补录路径同样推断。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # swan-ios- 前缀、不带 os_type → 推断 ios
            status, _, body = request(base_url, "/api/apks", "POST",
                                      {"apk_id": "swan-ios-dev-abc1234-20261015-test"})
            assert status == 201 and json.loads(body)["os_type"] == "ios"
            # 普通前缀、不带 os_type → android
            status, _, body = request(base_url, "/api/apks", "POST",
                                      {"apk_id": "swan-dev-def5678-20261015-test"})
            assert status == 201 and json.loads(body)["os_type"] == "android"
            # 显式 os_type 优先于前缀(手动兜底场景:安卓风格 id 显式标 ios)
            status, _, body = request(base_url, "/api/apks", "POST",
                                      {"apk_id": "swan-manual-aaaaaaa-20261015-test",
                                       "os_type": "ios"})
            assert status == 201 and json.loads(body)["os_type"] == "ios"
            # 非法值 → 400 且 error 提 os_type(request() 只自动重试 401,400 自行接)
            try:
                request(base_url, "/api/apks", "POST",
                        {"apk_id": "probe-x", "os_type": "windows"})
                raise AssertionError("expected HTTPError 400")
            except urllib.error.HTTPError as error:
                assert error.code == 400
                assert "os_type" in error.read().decode("utf-8")
            # ?os_type= 过滤;bogus 忽略返回全量;与 server_type 组合
            status, _, body = request(base_url, "/api/apks?os_type=ios")
            assert {i["apk_id"] for i in json.loads(body)["items"]} == {
                "swan-ios-dev-abc1234-20261015-test", "swan-manual-aaaaaaa-20261015-test"}
            status, _, body = request(base_url, "/api/apks?os_type=android")
            assert {i["apk_id"] for i in json.loads(body)["items"]} == {
                "swan-dev-def5678-20261015-test"}
            status, _, body = request(base_url, "/api/apks?os_type=bogus")
            assert len(json.loads(body)["items"]) == 3
            status, _, body = request(base_url, "/api/apks?os_type=ios&server_type=test")
            assert len(json.loads(body)["items"]) == 2
            # 对账补录路径:register_apk_if_absent 不带 os_type 也按前缀推断
            inserted = server.RequestHandlerClass.store.register_apk_if_absent(
                {"apk_id": "swan-ios-x-1111111-20261015-test", "server_type": "test"})
            assert inserted is True
            status, _, body = request(base_url, "/api/apks?os_type=ios&limit=50")
            assert "swan-ios-x-1111111-20261015-test" in {
                i["apk_id"] for i in json.loads(body)["items"]}
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需句柄已关


def test_apk_os_type_backfill_migration():
    """存量库迁移:预建无 os_type 列的旧 schema → 启动自动加列并回填
    (swan-ios- 前缀 → ios,其余 → android),原字段无损;回填只在加列那次执行。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        db_path = Path(temporary_directory) / "dashboard.sqlite3"
        import sqlite3
        connection = sqlite3.connect(str(db_path))
        connection.executescript(
            """
            CREATE TABLE apks (
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
                build_time TEXT,
                jenkins_job TEXT NOT NULL DEFAULT '',
                jenkins_build TEXT NOT NULL DEFAULT '',
                registered_at TEXT NOT NULL
            );
            INSERT INTO apks (apk_id, notes, registered_at) VALUES
                ('swan-ios-dev-abc1234-20261015-formal', 'ios 存量', '2026-10-15T00:00:00+00:00'),
                ('swan-dev-def5678-20261015-test', '安卓存量', '2026-10-15T00:00:01+00:00');
            """
        )
        connection.commit()
        connection.close()
        server = create_server("127.0.0.1", 0, db_path)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, body = request(base_url, "/api/apks?limit=10")
            assert status == 200
            rows = {i["apk_id"]: i for i in json.loads(body)["items"]}
            assert rows["swan-ios-dev-abc1234-20261015-formal"]["os_type"] == "ios"
            assert rows["swan-dev-def5678-20261015-test"]["os_type"] == "android"
            # 原字段无损
            assert rows["swan-ios-dev-abc1234-20261015-formal"]["registered_at"] == \
                "2026-10-15T00:00:00+00:00"
            assert rows["swan-ios-dev-abc1234-20261015-formal"]["notes"] == "ios 存量"
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


def test_apk_download_content_type_by_os():
    """下载 MIME 按 OS 分流:安卓包 android MIME + .apk;iOS 包(显式/前缀推断)
    octet-stream + .ipa;下载名按 os 给扩展名,磁盘归档命名契约不变。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        android_file = Path(temporary_directory) / "fake.apk"
        ios_file = Path(temporary_directory) / "fake.ipa"
        android_file.write_bytes(b"PK\x03\x04android")
        ios_file.write_bytes(b"PK\x03\x04ios-fake-ipa")
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request(base_url, "/api/apks", "POST", {
                "apk_id": "swan-dev-abc-20261015-test", "file_path": str(android_file)})
            request(base_url, "/api/apks", "POST", {
                "apk_id": "swan-manual-abc-20261015-test", "os_type": "ios",
                "file_path": str(ios_file)})
            request(base_url, "/api/apks", "POST", {
                "apk_id": "swan-ios-dev-abc-20261015-test", "file_path": str(android_file)})
            # 安卓包 → android MIME + .apk
            with urllib.request.urlopen(
                    f"{base_url}/api/apks/swan-dev-abc-20261015-test/download",
                    timeout=5) as response:
                assert response.headers["Content-Type"] == \
                    "application/vnd.android.package-archive"
                assert (response.headers.get_filename() or "").endswith(".apk")
            # 显式 ios → octet-stream + .ipa
            with urllib.request.urlopen(
                    f"{base_url}/api/apks/swan-manual-abc-20261015-test/download",
                    timeout=5) as response:
                assert response.headers["Content-Type"] == "application/octet-stream"
                assert (response.headers.get_filename() or "").endswith(".ipa")
            # 前缀推断 ios(产物仍是 .apk 路径)→ octet-stream
            with urllib.request.urlopen(
                    f"{base_url}/api/apks/swan-ios-dev-abc-20261015-test/download",
                    timeout=5) as response:
                assert response.headers["Content-Type"] == "application/octet-stream"
                assert (response.headers.get_filename() or "").endswith(".ipa")
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


def test_build_os_type_selects_jenkins_job():
    """主动打包按 os_type 选 Jenkins job:ios → swan-build-IOS(JENKINS_IOS_BUILD_JOB 可
    覆盖),缺省/android → swan-build-android;非法值 400 不触发;iOS 同受约束。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        captured = []

        class MockJenkins(BaseHTTPRequestHandler):
            def do_POST(self):
                # 必须先读掉请求体再回响应:Windows 上带未读数据的连接关闭会发 RST,
                # 间歇性打挂本测试后续的 mock 连接(表现 502/ConnectionError)
                length = int(self.headers.get("Content-Length", "0") or 0)
                self.rfile.read(length)
                captured.append(self.path)
                self.send_response(201)
                self.send_header("Location", "http://mock-jenkins/queue/item/88/")
                self.end_headers()

            def log_message(self, *args):
                pass

        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        env_keys = ("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_API_TOKEN",
                    "JENKINS_BUILD_JOB", "JENKINS_IOS_BUILD_JOB")
        env_backup = {key: os.environ.get(key) for key in env_keys}
        os.environ.update({
            "JENKINS_BASE_URL": f"http://127.0.0.1:{jenkins_server.server_address[1]}",
            "JENKINS_USER": "mock",
            "JENKINS_API_TOKEN": "mock",
        })
        os.environ.pop("JENKINS_BUILD_JOB", None)        # 走默认 swan-build-android
        os.environ.pop("JENKINS_IOS_BUILD_JOB", None)    # 走默认 swan-build-IOS
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # ios → swan-build-IOS
            status, _, body = request(base_url, "/api/builds", "POST",
                                      {"branch": "dev", "os_type": "ios"})
            assert status == 200 and json.loads(body)["job"] == "swan-build-IOS"
            assert "/job/swan-build-IOS/buildWithParameters" in captured[-1]
            # 缺省 / android → 安卓 job(缺省 android 口径)
            status, _, body = request(base_url, "/api/builds", "POST", {"branch": "dev"})
            assert json.loads(body)["job"] == "swan-build-android"
            status, _, body = request(base_url, "/api/builds", "POST",
                                      {"branch": "dev", "os_type": "android"})
            assert json.loads(body)["job"] == "swan-build-android"
            # 非法值 → 400 且不触发
            count = len(captured)
            try:
                request(base_url, "/api/builds", "POST",
                        {"branch": "dev", "os_type": "windows"})
                raise AssertionError("expected HTTPError 400")
            except urllib.error.HTTPError as error:
                assert error.code == 400
            assert len(captured) == count
            # iOS 同受 branch 白名单约束(注入字符拒绝,不触发)
            try:
                request(base_url, "/api/builds", "POST",
                        {"branch": "dev;evil", "os_type": "ios"})
                raise AssertionError("expected HTTPError 400")
            except urllib.error.HTTPError as error:
                assert error.code == 400
            assert len(captured) == count
            # JENKINS_IOS_BUILD_JOB 覆盖默认
            os.environ["JENKINS_IOS_BUILD_JOB"] = "swan-build-IOS-custom"
            status, _, body = request(base_url, "/api/builds", "POST",
                                      {"branch": "dev", "os_type": "ios"})
            assert json.loads(body)["job"] == "swan-build-IOS-custom"
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()
