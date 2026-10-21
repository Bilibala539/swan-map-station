"""套件「端」(os_type)与任务端透传(device_os)测试(2026-10-15)。

覆盖平台侧四层:
  1. validate_test_suite 枚举校验/缺省 android;
  2. 套件 CRUD 带 os_type + 存量库迁移默认 android;
  3. 任务建行带 device_os、认领响应透传、非法值 400;
  4. Jenkins 派发参数带 DEVICE_OS;流水线配置执行按套件解析端(payload 覆盖优先)。
"""
from __future__ import annotations

import gc
import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard.server import SUITE_OS_TYPES, create_server, validate_test_suite

# 平台登录闸后的测试适配(同 test_dashboard.py):401 自动登录一次重试
_SESSION_COOKIES: dict[str, str] = {}


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None):
    """返回 (status, body);4xx/5xx 同样以 (code, 错误 body) 返回,供负路径断言;
    唯独 401 自动登录一次重试(登录闸测试适配,同 test_dashboard.py)。"""
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def send(cookie: str | None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request_object = urllib.request.Request(
            f"{base_url}{path}", data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request_object, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else {}

    status, result = send(_SESSION_COOKIES.get(base_url))
    if status != 401:
        return status, result
    login = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"username": "yumiao"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(login, timeout=5) as response:
        _SESSION_COOKIES[base_url] = response.headers.get("Set-Cookie", "").split(";")[0]
    return send(_SESSION_COOKIES[base_url])


def _start_server(temporary_directory: str):
    server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, thread, base_url


# ==================== 1. validate_test_suite ====================

def test_validate_suite_os_type_default_and_enum():
    fields = validate_test_suite({"suite_id": "S1", "name": "套件"})
    assert fields["os_type"] == "android"  # 缺省安卓(存量迁移同语义)
    for os_type in SUITE_OS_TYPES:
        assert validate_test_suite({"suite_id": "S1", "name": "n", "os_type": os_type})["os_type"] == os_type
    # 大写归一
    assert validate_test_suite({"suite_id": "S1", "name": "n", "os_type": "Android"})["os_type"] == "android"
    with pytest.raises(ValueError):
        validate_test_suite({"suite_id": "S1", "name": "n", "os_type": "windows"})
    with pytest.raises(ValueError):
        validate_test_suite({"suite_id": "S1", "name": "n", "os_type": "harmonyos"})  # 严防别名混入


# ==================== 2. 套件 CRUD + 存量迁移 ====================

def test_suite_crud_os_type_roundtrip():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base_url = _start_server(temporary_directory)
        try:
            status, suite = request(base_url, "/api/suites", "POST", {"suite_id": "S-A", "name": "不传端"})
            assert status == 201 and suite["os_type"] == "android"
            status, suite = request(base_url, "/api/suites", "POST",
                                    {"suite_id": "S-H", "name": "鸿蒙套件", "os_type": "harmony"})
            assert status == 201 and suite["os_type"] == "harmony"
            status, _ = request(base_url, "/api/suites", "POST",
                                {"suite_id": "S-X", "name": "坏端", "os_type": "magician"})
            assert status == 400
            # 部分更新:只带 os_type,其他字段沿用库里现值
            status, suite = request(base_url, "/api/suites/S-H", "PUT", {"os_type": "ios"})
            assert status == 200 and suite["os_type"] == "ios" and suite["name"] == "鸿蒙套件"
            # 列表带端
            status, data = request(base_url, "/api/suites")
            os_map = {s["suite_id"]: s["os_type"] for s in data["items"]}
            assert os_map == {"S-A": "android", "S-H": "ios"}
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需 sqlite 句柄已关


def test_existing_suite_db_migrates_os_type_default_android():
    """存量库(无 os_type 列)启动迁移:补列 + 存量行默认 android,读旧数据不炸。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        db_path = Path(temporary_directory) / "dashboard.sqlite3"
        # 手工造一个旧版库:test_suites 按 os_type 之前的老 DDL 建表并塞一行
        connection = sqlite3.connect(db_path)
        connection.executescript("""
            CREATE TABLE test_suites (
                suite_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                suite_type TEXT NOT NULL DEFAULT 'regression',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO test_suites (suite_id, name, created_at, updated_at)
            VALUES ('S-OLD', '老套件', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
        """)
        connection.commit()
        connection.close()
        server, thread, base_url = _start_server(temporary_directory)
        try:
            status, suite = request(base_url, "/api/suites/S-OLD")
            assert status == 200 and suite["os_type"] == "android"
            # 迁移后新套件照常建,不炸
            status, suite = request(base_url, "/api/suites", "POST",
                                    {"suite_id": "S-NEW", "name": "新套件", "os_type": "harmony"})
            assert status == 201 and suite["os_type"] == "harmony"
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


# ==================== 3. 任务 device_os + 认领透传 ====================

def test_pipeline_trigger_device_os_passthrough_and_claim():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base_url = _start_server(temporary_directory)
        try:
            status, job = request(base_url, "/api/pipeline/trigger", "POST", {
                "trigger_source": "build-downstream", "platform": "flutter",
                "case_filter": "tests/flutter/test_case_driver.py", "device_os": "harmony"})
            assert status == 201 and job["device_os"] == "harmony"
            # 未带 device_os 的触发(旧脚本/nightly)→ ''=不限端,旧行为
            status, job2 = request(base_url, "/api/pipeline/trigger", "POST", {
                "trigger_source": "nightly", "platform": "flutter",
                "case_filter": "tests/flutter/test_case_driver.py"})
            assert status == 201 and job2["device_os"] == ""
            # 非法端 400
            status, _ = request(base_url, "/api/pipeline/trigger", "POST", {
                "trigger_source": "manual", "device_os": "windows"})
            assert status == 400
            # 认领响应透传端(agent 执行侧的数据源)
            status, claimed = request(base_url, "/api/pipeline/claim", "POST", {
                "agent": "test-agent", "job_id": job["job_id"]})
            assert status == 200 and claimed["device_os"] == "harmony"
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


# ==================== 4. Jenkins 派发参数 + 配置执行解析端 ====================

class _MockJenkins(BaseHTTPRequestHandler):
    """记录 buildWithParameters 收到的 form 参数;201 + Location 走派发成功分支。"""
    captured: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _MockJenkins.captured["params"] = dict(urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8")))
        self.send_response(201)
        self.send_header("Location", "http://jenkins/queue/item/99/")
        self.end_headers()

    def do_GET(self):
        # queue_item 轮询:直接给 build 号,让派发闭环
        body = json.dumps({"executable": {"number": 7}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_jenkins_dispatch_carries_device_os(monkeypatch):
    _MockJenkins.captured.clear()
    with tempfile.TemporaryDirectory() as temporary_directory:
        monkeypatch.setattr("dashboard.server.jenkins_client.config",
                            lambda: {"base": "http://jenkins", "user": "u", "token": "t"})
        jenkins_server = ThreadingHTTPServer(("127.0.0.1", 0), _MockJenkins)
        threading.Thread(target=jenkins_server.serve_forever, daemon=True).start()
        monkeypatch.setattr("dashboard.server.jenkins_client._request",
                            lambda method, url, data=None, headers=None, timeout=10:
                            _proxy_jenkins_request(jenkins_server, method, url, data, headers, timeout))
        server, thread, base_url = _start_server(temporary_directory)
        try:
            status, job = request(base_url, "/api/pipeline/trigger", "POST", {
                "trigger_source": "build-downstream", "case_filter": "x.py",
                "device_os": "android"})
            assert status == 201
            assert _MockJenkins.captured["params"]["JOB_ID"] == [job["job_id"]]
            assert _MockJenkins.captured["params"]["DEVICE_OS"] == ["android"]
        finally:
            server.shutdown()
            server.server_close()
            jenkins_server.shutdown()
            jenkins_server.server_close()
            gc.collect()


def _proxy_jenkins_request(jenkins_server, method, url, data, headers, timeout):
    """把 jenkins_client 的请求转发到本地 mock(jenkins_client._request 打桩用)。"""
    path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
    request_object = urllib.request.Request(f"http://127.0.0.1:{jenkins_server.server_address[1]}{path}",
                                            data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request_object, timeout=timeout) as response:
        return response.status, dict(response.headers), response.read().decode("utf-8")


def test_run_pipeline_config_resolves_device_os_from_suite():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base_url = _start_server(temporary_directory)
        try:
            status, suite = request(base_url, "/api/suites", "POST",
                                    {"suite_id": "S-H", "name": "鸿蒙", "os_type": "harmony"})
            assert status == 201
            status, config = request(base_url, "/api/pipeline-configs", "POST", {
                "config_id": "C1", "name": "配置一", "suite_id": "S-H", "schedule_type": "daily",
                "schedule_param": {"time": "03:33"}})
            assert status == 201
            # 配置执行:端取套件 os_type
            status, result = request(base_url, "/api/pipeline-configs/C1/run", "POST", {"source": "test"})
            assert status == 201 and result["job"]["device_os"] == "harmony"
            # payload 覆盖优先于套件
            status, result = request(base_url, "/api/pipeline-configs/C1/run", "POST",
                                     {"source": "test", "device_os": "android"})
            assert status == 201 and result["job"]["device_os"] == "android"
            # 无套件配置 → ''(不限端,旧行为)
            request(base_url, "/api/pipeline-configs", "POST", {
                "config_id": "C2", "name": "无套件", "schedule_type": "daily",
                "schedule_param": {"time": "04:44"}})
            status, result = request(base_url, "/api/pipeline-configs/C2/run", "POST", {"source": "test"})
            assert status == 201 and result["job"]["device_os"] == ""
            # 套件已删:回落 '' 不阻塞(不 404)
            request(base_url, "/api/suites/S-H", "DELETE")
            status, result = request(base_url, "/api/pipeline-configs/C1/run", "POST", {"source": "test"})
            assert status == 201 and result["job"]["device_os"] == ""
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


def test_suite_case_paths_exposes_device_os():
    """case-paths 响应带 device_os(trigger_smoke 的透传数据源)。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base_url = _start_server(temporary_directory)
        try:
            request(base_url, "/api/suites", "POST",
                    {"suite_id": "S-EMPTY", "name": "空套件", "os_type": "harmony"})
            status, body = request(base_url, "/api/suites/S-EMPTY/case-paths")
            assert status == 200 and body["device_os"] == "harmony" and body["paths"] == []
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()
