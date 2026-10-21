from __future__ import annotations

import gc
import json
import os
import tempfile
import threading
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard.server import (
    DashboardStore,
    _jenkins_build_times,
    create_server,
)
from dashboard.tests.test_dashboard import request


def test_jenkins_build_times_helper():
    """timestamp/duration(epoch 毫秒)→ UTC ISO 起止;缺 duration/构建中终值为空串。"""
    ts = 1758682632000
    start = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    assert _jenkins_build_times({"timestamp": ts, "duration": 120_000}) == (
        start.isoformat(timespec="seconds"),
        datetime.fromtimestamp(ts / 1000 + 120, tz=timezone.utc).isoformat(timespec="seconds"),
    )
    # 构建中(duration=0)/缺 duration:终值空串,起值正常
    assert _jenkins_build_times({"timestamp": ts, "duration": 0})[1] == ""
    assert _jenkins_build_times({"timestamp": ts})[1] == ""
    # 缺 timestamp/坏类型:整体空串
    assert _jenkins_build_times({}) == ("", "")
    assert _jenkins_build_times({"timestamp": "x", "duration": 1}) == ("", "")


def test_pipeline_job_time_overrides():
    """对账/回查落库时间口径:create 用构建真实开始,finish 用构建真实结束。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        store = DashboardStore(Path(temporary_directory) / "dashboard.sqlite3")
        job = store.create_pipeline_job({
            "trigger_source": "build", "platform": "flutter",
            "branch": "dev", "case_filter": "", "apk_id": "",
            "created_at": "2026-09-24T01:00:00+00:00",
        })
        assert job["created_at"] == "2026-09-24T01:00:00+00:00"
        # finished_at 覆盖:终态落构建真实结束时刻
        done = store.finish_build_pipeline_job(
            job["job_id"], "succeeded", {}, finished_at="2026-09-24T01:05:00+00:00")
        assert done["finished_at"] == "2026-09-24T01:05:00+00:00"
        # 缺省(空串)仍落平台时刻,不落 NULL
        job2 = store.create_pipeline_job({"trigger_source": "build", "platform": "flutter"})
        done2 = store.finish_build_pipeline_job(job2["job_id"], "succeeded", {}, finished_at="")
        assert done2["finished_at"]

        # 存量修复:created_at=finished_at 的坏行按真实起止回填;修好的行再调不再变(幂等)
        bad = store.create_pipeline_job({"trigger_source": "webhook", "platform": "flutter"})
        store.mark_jenkins_dispatch(bad["job_id"], jenkins_job="swan-build-android",
                                    jenkins_build=7, result_extra={})
        store.finish_build_pipeline_job(bad["job_id"], "succeeded", {})
        row = store.get_pipeline_job(bad["job_id"])
        assert row["created_at"] == row["finished_at"]  # 首版对账补行的时间病
        fixed = store.fix_reconciled_job_times(
            bad["job_id"], "2026-09-24T02:00:00+00:00", "2026-09-24T02:03:00+00:00")
        assert fixed["created_at"] == "2026-09-24T02:00:00+00:00"
        assert fixed["finished_at"] == "2026-09-24T02:03:00+00:00"
        again = store.fix_reconciled_job_times(
            bad["job_id"], "2026-09-24T09:00:00+00:00", "2026-09-24T09:30:00+00:00")
        assert again["created_at"] == "2026-09-24T02:00:00+00:00"  # 条件不再命中,幂等

        # finished_at 空串保持原值:同秒闭环的正常行不被抹成 NULL
        bad2 = store.create_pipeline_job({"trigger_source": "build", "platform": "flutter",
                                          "created_at": "2026-09-24T03:00:00+00:00"})
        store.finish_build_pipeline_job(bad2["job_id"], "succeeded", {},
                                        finished_at="2026-09-24T03:00:00+00:00")
        kept = store.fix_reconciled_job_times(
            bad2["job_id"], "2026-09-24T02:50:00+00:00", finished_at="")
        assert kept["created_at"] == "2026-09-24T02:50:00+00:00"
        assert kept["finished_at"] == "2026-09-24T03:00:00+00:00"
        del store
        gc.collect()  # Windows 删库文件需 sqlite 句柄已关


def test_case_defs_batch_update():
    """批量编辑(2026-09-24):仅 priority/module;module 自动登记;非法入参 400。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for case_id in ("CASE-B1", "CASE-B2"):
                status, _, _ = request(base_url, "/api/case-defs", "POST", {
                    "case_id": case_id, "case_type": "auto",
                    "title": f"用例 {case_id}", "module": "login", "priority": "P2",
                })
                assert status == 201

            # 改优先级:两行全中;旧档 L1 归一为 P1
            status, _, body = request(base_url, "/api/case-defs/batch-update", "POST", {
                "case_ids": ["CASE-B1", "CASE-B2"], "patch": {"priority": "P0"}})
            assert status == 200 and json.loads(body) == {"updated": 2}
            status, _, body = request(base_url, "/api/case-defs/batch-update", "POST", {
                "case_ids": ["CASE-B1"], "patch": {"priority": "L1"}})
            assert status == 200

            # 改模块:自动登记进 modules(与 upsert_case_def 同口径)
            status, _, body = request(base_url, "/api/case-defs/batch-update", "POST", {
                "case_ids": ["CASE-B1", "CASE-B2"], "patch": {"module": "batch-new-mod"}})
            assert status == 200 and json.loads(body)["updated"] == 2
            status, _, body = request(base_url, "/api/modules")
            assert any(m["slug"] == "batch-new-mod" for m in json.loads(body)["items"])
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["CASE-B1"]["priority"] == "P1"
            assert items["CASE-B2"]["module"] == "batch-new-mod"

            # 非法入参一律 400
            for payload in (
                {"case_ids": [], "patch": {"priority": "P1"}},
                {"case_ids": "CASE-B1", "patch": {"priority": "P1"}},
                {"case_ids": ["CASE-B1"], "patch": {}},
                {"case_ids": ["CASE-B1"], "patch": {"title": "不该被改的字段"}},
            ):
                try:
                    status, _, _ = request(base_url, "/api/case-defs/batch-update", "POST", payload)
                except urllib.error.HTTPError as error:
                    status = error.code
                assert status == 400
            # 不存在的 id 自然跳过,不报错
            status, _, body = request(base_url, "/api/case-defs/batch-update", "POST", {
                "case_ids": ["CASE-NOPE"], "patch": {"priority": "P3"}})
            assert status == 200 and json.loads(body) == {"updated": 0}
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


def test_reconcile_backfill_uses_build_times():
    """对账补行/存量修复都落 Jenkins 构建真实起止,不再"创建时间=结束时间"。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        ts41 = 1_758_680_000_000   # 构建起止由 timestamp+duration 还原
        ts42 = ts41 + 600_000

        def iso(ms: float) -> str:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")

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
                        {"number": 41, "result": "SUCCESS", "building": False,
                         "timestamp": ts41, "duration": 120_000},
                        {"number": 42, "result": "FAILURE", "building": False,
                         "timestamp": ts42, "duration": 60_000}]})
                elif "tree=actions[parameters" in self.path:
                    reply({"actions": [{"parameters": [
                        {"name": "BRANCH", "value": "dev"},
                        {"name": "SERVER_TYPE", "value": "test"}]}]})
                else:
                    reply({"building": False, "result": "SUCCESS"})

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

        # 预置首版对账的"病行":created_at == finished_at(同一同步时刻)
        import sqlite3
        connection = sqlite3.connect(str(Path(temporary_directory) / "dashboard.sqlite3"))
        connection.execute(
            "INSERT INTO pipeline_jobs (job_id, trigger_source, status, created_at,"
            " finished_at, jenkins_job, jenkins_build)"
            " VALUES ('job-seed-bad', 'build', 'succeeded', ?, ?, 'swan-build-android', 41)",
            ("2026-09-24T02:17:12+00:00", "2026-09-24T02:17:12+00:00"))
        connection.commit()
        connection.close()

        try:
            status, _, body = request(base_url, "/api/pipeline/jobs?limit=30")
            assert status == 200
            rows = {j["jenkins_build"]: j for j in json.loads(body)["items"]
                    if j.get("jenkins_job") == "swan-build-android"}
            # 41:病行被存量修复回填为构建真实起止
            assert rows[41]["created_at"] == iso(ts41)
            assert rows[41]["finished_at"] == iso(ts41 + 120_000)
            # 42:无主构建补行,新建即带真实开始时间,终态带真实结束时间
            assert rows[42]["created_at"] == iso(ts42)
            assert rows[42]["finished_at"] == iso(ts42 + 60_000)
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
