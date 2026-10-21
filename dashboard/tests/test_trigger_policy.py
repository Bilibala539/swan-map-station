"""触发策略测试:UI 自动化仅允许测试服包(2026-09-29)。

formal 包是 Release 构建,kReleaseMode 锁死正式服,验证码网关(testnetwork)
取不到它的验证码,${otp} 用例必失败(jenkins-swan-autotest #37/#38/#39 实证)。
平台在 POST /api/pipeline/trigger 入口对 formal apk 一律 409 拦截,任务不创建;
测试服包与未登记 apk 维持原行为。
"""
from __future__ import annotations

import gc
import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer  # noqa: F401 — create_server 内部使用
from pathlib import Path

from dashboard.server import create_server


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None,
            cookie: str = ""):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request_object = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request_object, timeout=5) as response:
        return response.status, response.read()


def _login(base_url: str) -> str:
    """平台登录(v1.1 全站 401 闸后,浏览器面 GET 须带 swan_user cookie)。"""
    request_object = urllib.request.Request(
        f"{base_url}/api/auth/login", data=json.dumps({"username": "yumiao"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request_object, timeout=5) as response:
        assert response.status == 200
        return response.headers["Set-Cookie"].split(";")[0]


def _register_apk(base_url: str, apk_id: str, server_type: str) -> None:
    status, body = request(base_url, "/api/apks", "POST", {
        "apk_id": apk_id,
        "app_version": "5.2.0",
        "branch": "dev",
        "commit_hash": "deb9be6",
        "flavor": "release" if server_type == "formal" else "profile",
        "source": "jenkins",
        "file_path": f"/opt/swan-build/apks/{apk_id}.apk",
        "file_hash": "0" * 64,
        "notes": "",
        "server_type": server_type,
    })
    assert status == 201, body


def _jobs_with_apk(base_url: str, apk_id: str, cookie: str) -> list[dict]:
    _, body = request(base_url, "/api/pipeline/jobs?limit=200", cookie=cookie)
    items = json.loads(body)
    if isinstance(items, dict):
        items = items.get("jobs") or items.get("items") or []
    return [job for job in items if job.get("apk_id") == apk_id]


def _trigger(base_url: str, payload: dict):
    try:
        return request(base_url, "/api/pipeline/trigger", "POST", payload)
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_trigger_rejects_formal_apk_and_keeps_test_apk():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            cookie = _login(base_url)  # 任务清单 GET 在登录闸后;trigger/apks POST 走机器通道不受闸
            _register_apk(base_url, "swan-dev-deb9be6-formal", "formal")
            _register_apk(base_url, "swan-dev-deb9be6-test", "test")

            # build-downstream(打包下游冒烟)带 formal 包 → 409,任务不创建
            status, body = _trigger(base_url, {
                "trigger_source": "build-downstream",
                "platform": "flutter",
                "apk_id": "swan-dev-deb9be6-formal",
            })
            assert status == 409, (status, body)
            assert "formal" in body.decode("utf-8")
            assert _jobs_with_apk(base_url, "swan-dev-deb9be6-formal", cookie) == []

            # 手动触发带 formal 包 → 同样 409 拦截
            status, body = _trigger(base_url, {
                "trigger_source": "manual",
                "platform": "flutter",
                "apk_id": "swan-dev-deb9be6-formal",
            })
            assert status == 409, (status, body)
            assert _jobs_with_apk(base_url, "swan-dev-deb9be6-formal", cookie) == []

            # 测试服包 → 正常建任务(无 Jenkins 凭据时留 pending 本地兜底)
            status, body = _trigger(base_url, {
                "trigger_source": "build-downstream",
                "platform": "flutter",
                "apk_id": "swan-dev-deb9be6-test",
            })
            assert status == 201, (status, body)
            jobs = _jobs_with_apk(base_url, "swan-dev-deb9be6-test", cookie)
            assert len(jobs) == 1
            assert jobs[0]["status"] == "pending"

            # apk 未登记(异常路径)→ 不拦截,维持原行为
            status, _ = _trigger(base_url, {
                "trigger_source": "manual",
                "platform": "flutter",
                "apk_id": "swan-dev-unknown-00000000-test",
            })
            assert status == 201, status
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # 例外/回溯帧可能循环引用 sqlite 连接;Windows 删库文件需句柄已关
