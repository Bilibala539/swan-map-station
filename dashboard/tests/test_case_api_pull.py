"""外部用例 API 适配层测试(2026-09-28):规范化映射 + 拉取管线 + 管理端点 stub 行为。
2026-10-10 起兼载 case_scope 用例范围字段测试(借本文件的临时服务基建)。"""
from __future__ import annotations

import gc
import json
import os
import sqlite3
import tempfile
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard.case_api_client import (
    CaseApiError,
    _normalize_case,
    fetch_text_cases,
    is_configured,
    module_slug,
)
from dashboard.server import create_server
from dashboard.tests.test_dashboard import request


@pytest.fixture()
def _case_api_env(monkeypatch):
    """CASE_API_* 环境变量密闭化:显式设空,防部署机 .env.local 渗入测试。"""
    for key in ("CASE_API_URL", "CASE_API_TOKEN", "CASE_API_AUTH_HEADER"):
        monkeypatch.setenv(key, "")


def test_normalize_case_maps_to_existing_fields():
    """远端报文 -> case_defs 现有字段:候选 key 命中/步骤列表拼行/bug 提取/来源标记。"""
    raw = {
        "id": "LOGIN-L2-POS-001",
        "name": "密码登录成功",
        "moduleName": "login",
        "level": "L2",
        "precondition": "已进入登录页",
        "steps": [
            {"step": "输入正确账号密码", "expected": "输入框回显"},
            "点击登录按钮",
        ],
        "expected_result": "进入首页",
        "tags": ["regression", "SWAN-BUG-20260901-003"],
    }
    case = _normalize_case(raw)
    assert case["case_id"] == "LOGIN-L2-POS-001"
    assert case["case_type"] == "text"
    assert case["title"] == "密码登录成功"
    assert case["module"] == "login"
    assert case["priority"] == "L2"  # P0-P3 归一交给 upsert_case_def,这里透传
    assert case["source"] == "case-api"
    assert case["source_bug"] == "SWAN-BUG-20260901-003"
    assert case["file_path"] == "" and case["linked_case_id"] == ""
    assert case["human_confirmed"] == 0  # 无 status 字段的来源默认未确认
    assert "前置:已进入登录页" in case["content"]
    assert "输入正确账号密码 -> 输入框回显" in case["content"]
    assert "点击登录按钮" in case["content"]
    assert "预期:进入首页" in case["content"]


def test_normalize_case_fallback_and_validation():
    """无结构化字段时 content 兜底存原始报文;缺 ID/标题/非对象行报 ValueError。"""
    raw = {"code": "X-1", "subject": "仅标题用例", "unknown_field": {"a": 1}}
    case = _normalize_case(raw)
    assert case["case_id"] == "X-1" and case["title"] == "仅标题用例"
    assert '"unknown_field"' in case["content"]  # 兜底 JSON 不丢信息
    for bad, snippet in (
        ({"name": "缺 ID"}, "缺少用例 ID"),
        ({"id": "X-2"}, "缺少标题"),
        ("not-a-dict", "必须是对象"),
    ):
        with pytest.raises(ValueError, match=snippet):
            _normalize_case(bad)


def test_pull_endpoint_unconfigured_skips_cleanly(_case_api_env):
    """stub 行为:CASE_API_URL 为空 => 200 + skipped=true 明确跳过,不报错、不产生脏数据。"""
    assert not is_configured()
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            assert status == 200
            result = json.loads(body)
            assert result["skipped"] is True and result["ok"] is True
            assert "等待配置" in result["message"]
            status, _, body = request(base_url, "/api/case-defs")
            assert json.loads(body)["items"] == []  # 未写任何用例
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需 sqlite 句柄已关


def test_pull_pipeline_with_mock_api(_case_api_env):
    """配置指向 mock API:拉取->规范化->upsert 全链路;幂等重拉计更新;坏行进 failed。"""
    rows = [
        {"id": "CASE-API-001", "name": "播放音乐", "moduleName": "player",
         "level": "L1", "steps": "点播放", "expected_result": "出声"},
        {"case_id": "CASE-API-002", "title": "暂停播放", "module": "player",
         "priority": "P0", "content": "手工内容"},
        {"name": "缺 ID 的坏行"},  # 计入 failed,不影响其他行
    ]

    class MockCaseApi(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"items": rows}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    api_server = ThreadingHTTPServer(("127.0.0.1", 0), MockCaseApi)
    threading.Thread(target=api_server.serve_forever, daemon=True).start()
    os.environ["CASE_API_URL"] = f"http://127.0.0.1:{api_server.server_address[1]}"
    try:
        with tempfile.TemporaryDirectory() as temporary_directory:
            server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
                assert status == 200
                result = json.loads(body)
                assert result["ok"] is True and "skipped" not in result
                assert (result["total"], result["created"], result["updated"]) == (3, 2, 0)
                assert len(result["failed"]) == 1
                assert result["failed"][0]["row"] == 3
                assert "缺少用例 ID" in result["failed"][0]["reason"]

                # 入库字段核对:规范化映射 + 优先级归一 + 模块自动登记
                status, _, body = request(base_url, "/api/case-defs")
                items = {c["case_id"]: c for c in json.loads(body)["items"]}
                assert set(items) == {"CASE-API-001", "CASE-API-002"}
                first = items["CASE-API-001"]
                assert first["case_type"] == "text"
                assert first["title"] == "播放音乐"
                assert first["module"] == "player"
                assert first["priority"] == "P1"  # L1 归一为 P1(与页面/同步同口径)
                assert first["source"] == "case-api"
                assert items["CASE-API-002"]["priority"] == "P0"
                # content 只在单查返回(list 端点不带 content 列),详情口径核对
                status, _, body = request(base_url, "/api/case-defs/CASE-API-001")
                assert "步骤:\n点播放" in json.loads(body)["content"]
                status, _, body = request(base_url, "/api/case-defs/CASE-API-002")
                assert json.loads(body)["content"] == "手工内容"  # 已有 content 字段直取,不兜底 JSON
                status, _, body = request(base_url, "/api/modules")
                assert any(m["slug"] == "player" for m in json.loads(body)["items"])

                # 幂等重拉:同 case_id 全部计更新,不产生重复行
                status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
                result = json.loads(body)
                assert (result["created"], result["updated"]) == (0, 2)
                status, _, body = request(base_url, "/api/case-defs")
                assert len(json.loads(body)["items"]) == 2
            finally:
                server.shutdown()
                server.server_close()
                gc.collect()
    finally:
        os.environ.pop("CASE_API_URL", None)
        api_server.shutdown()
        api_server.server_close()


def test_normalize_workbench_schema():
    """人审工作站 cases/0.3 报文映射:moduleL1 入 module、嵌套步骤渲染、脚本只留索引行。"""
    raw = {
        "id": "AUTH-001",
        "title": "未登录启动显示登录页",
        "priority": "P0",
        "moduleL1": "登录模块",
        "moduleL2": "登录-账号自动登录",
        "definition": {
            "steps": [
                {"no": 1, "action": "清账号状态并启动 App", "locator": "app.launch",
                 "data": "模拟机已安装 debug 包", "expect": "进入登录页"},
                {"no": 2, "action": "等待登录页 marker", "locator": "login_page",
                 "data": "无", "expect": "login_page 可见"},
            ],
            "overallExpect": "未登录启动必达登录页",
        },
        "script": {"compiler": "aw", "hash": "abcdef1234567890",
                   "file": "knowledge/swan/cases/AUTH-001.py", "code": "SCRIPT-CODE-BLOB"},
        "status": "stored",
    }
    case = _normalize_case(raw)
    assert case["case_id"] == "AUTH-001" and case["title"] == "未登录启动显示登录页"
    assert case["module"] == "登录模块"  # 取 moduleL1(粗粒度),中文原样入 slug
    assert case["priority"] == "P0" and case["case_type"] == "text"
    assert case["source"] == "case-api" and case["file_path"] == ""
    assert case["human_confirmed"] == 1  # status=stored(过两道门)=> 已确认
    assert _normalize_case({**raw, "status": "pending"})["human_confirmed"] == 0  # 待审=> 未确认
    assert _normalize_case({**raw, "status": "rejected"})["human_confirmed"] == 0
    content = case["content"]
    assert "1. 清账号状态并启动 App(定位:app.launch;数据:模拟机已安装 debug 包)→ 预期:进入登录页" in content
    assert "2. 等待登录页 marker(定位:login_page)→ 预期:login_page 可见" in content  # data=无/— 略去
    assert "预期:未登录启动必达登录页" in content
    assert "knowledge/swan/cases/AUTH-001.py" in content and "abcdef123456" in content  # hash 截前 12 位
    assert "SCRIPT-CODE-BLOB" not in content  # 脚本代码本体不进 content


def test_fetch_filters_by_status(monkeypatch):
    """状态过滤:默认收 stored,pending(2026-09-29 需求变更,pending 进平台靠
    human_confirmed 区分,rejected 不进);无 status 字段的条目保留;all=不过滤。"""
    rows = [
        {"id": "A", "title": "t1", "status": "stored"},
        {"id": "B", "title": "t2", "status": "pending"},
        {"id": "C", "title": "t3", "status": "rejected"},
        {"id": "D", "title": "t4"},  # 无 status 字段(兼容无状态来源)一律保留
    ]
    monkeypatch.setattr("dashboard.case_api_client._request",
                        lambda path: {"schema": "cases/0.3", "cases": rows})
    monkeypatch.delenv("CASE_API_STATUS_FILTER", raising=False)
    assert [c["id"] for c in fetch_text_cases()] == ["A", "B", "D"]
    monkeypatch.setenv("CASE_API_STATUS_FILTER", "all")
    assert [c["id"] for c in fetch_text_cases()] == ["A", "B", "C", "D"]
    monkeypatch.setenv("CASE_API_STATUS_FILTER", "stored")
    assert [c["id"] for c in fetch_text_cases()] == ["A", "D"]  # 显式收窄;无 status 字段的 D 仍放行


def test_upsert_human_confirmed_retention(_case_api_env):
    """human_confirmed 保留语义(照 link_strength 模式):payload 不带字段时 UPDATE
    不冲掉已确认标志;显式带值(含字符串真值)才覆盖;其他来源缺省 INSERT 0。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # 不带 human_confirmed 新建 => 缺省 0(sync_cases/页面手工同口径)
            status, _, body = request(base_url, "/api/case-defs", "POST", {
                "case_id": "HC-1", "case_type": "text", "title": "普通新建"})
            assert status == 201 and json.loads(body)["human_confirmed"] == 0
            # API 源显式带 1(模拟 stored 拉取)
            status, _, body = request(base_url, "/api/case-defs/HC-1", "PUT", {
                "case_id": "HC-1", "case_type": "text", "title": "已过两道门",
                "human_confirmed": 1})
            assert json.loads(body)["human_confirmed"] == 1
            # 页面编辑/sync 重跑(不带该字段)=> UPDATE 保留原值,不被冲掉
            status, _, body = request(base_url, "/api/case-defs/HC-1", "PUT", {
                "case_id": "HC-1", "case_type": "text", "title": "页面改标题"})
            assert json.loads(body)["human_confirmed"] == 1
            # API 拉取显式带 0(工作站改判 stored→pending 重拉)=> 如实回写未确认
            status, _, body = request(base_url, "/api/case-defs/HC-1", "PUT", {
                "case_id": "HC-1", "case_type": "text", "title": "改判回待审",
                "human_confirmed": 0})
            assert json.loads(body)["human_confirmed"] == 0
            # 字符串真值兼容("true" => 1)
            status, _, body = request(base_url, "/api/case-defs/HC-1", "PUT", {
                "case_id": "HC-1", "case_type": "text", "title": "字符串真值",
                "human_confirmed": "true"})
            assert json.loads(body)["human_confirmed"] == 1
            # 列表与单查都带出该字段
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["HC-1"]["human_confirmed"] == 1
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需 sqlite 句柄已关


def test_module_slug_mapping():
    """模块名 -> 有义编号前缀:后缀剥离 + 大小写归一;映射不到返回空串。"""
    assert module_slug("登录模块") == "login"
    assert module_slug("Wi-Fi 设备模块") == "wifi"
    assert module_slug("wifi 设备模块") == "wifi"
    assert module_slug("网络层") == "network"
    assert module_slug("播放器模块") == "player"
    assert module_slug("基建") == "infra"
    assert module_slug("平台集成") == "platform"
    assert module_slug("其它") == "misc"
    assert module_slug("神秘模块") == ""  # 未映射:不分配编号,展示回退原始编号
    assert module_slug("") == ""


def test_pull_assigns_biz_no(monkeypatch, _case_api_env):
    """有义编号(2026-09-30):拉取管线按模块分配 wifi-001 式编号;存量空编号行
    (biz_no 上线前的库)重拉自动补号;编号终身不变、模块内接着 max+1;
    未映射模块不分配;页面编辑(payload 不带 biz_no)不冲掉已分配编号。"""
    monkeypatch.setenv("CASE_API_URL", "http://case-api-stub")
    rows = [
        {"id": "TS-001", "title": "WiFi 扫描列表正常", "moduleL1": "Wi-Fi 设备模块", "status": "stored"},
        {"id": "TS-002", "title": "WiFi 连接后图标显示", "moduleL1": "Wi-Fi 设备模块"},
        {"id": "AUTH-001", "title": "未登录启动显示登录页", "moduleL1": "登录模块", "status": "stored"},
        {"id": "TS-003", "title": "神秘模块用例", "moduleL1": "神秘模块"},
    ]
    monkeypatch.setattr("dashboard.case_api_client._request",
                        lambda path: {"schema": "cases/0.3", "cases": list(rows)})
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # 存量空编号行(biz_no 上线前的库):TS-001 已在库、编号为空
            status, _, body = request(base_url, "/api/case-defs", "POST", {
                "case_id": "TS-001", "case_type": "text", "title": "存量空编号行",
                "module": "Wi-Fi 设备模块"})
            assert status == 201
            # 首拉:TS-001 走补号 wifi-001;其余新行分配 wifi-002/login-001;
            # 未映射模块不分配(留空,展示回退原始编号)
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            assert (json.loads(body)["created"], json.loads(body)["updated"]) == (3, 1)
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["TS-001"]["biz_no"] == "wifi-001"
            assert items["TS-002"]["biz_no"] == "wifi-002"
            assert items["AUTH-001"]["biz_no"] == "login-001"
            assert items["TS-003"]["biz_no"] == ""
            # 幂等重拉:编号终身不变,不重排
            request(base_url, "/api/case-defs/pull-api", "POST", {})
            status, _, body = request(base_url, "/api/case-defs")
            items2 = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert [items2[k]["biz_no"] for k in ("TS-001", "TS-002", "AUTH-001")] ==                    [items[k]["biz_no"] for k in ("TS-001", "TS-002", "AUTH-001")]
            # 补拉新用例:模块内接着 max+1
            rows.append({"id": "AUTH-002", "title": "密码登录成功",
                         "moduleL1": "登录模块", "status": "stored"})
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            result = json.loads(body)
            assert (result["created"], result["updated"]) == (1, 4)
            status, _, body = request(base_url, "/api/case-defs")
            items3 = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items3["AUTH-002"]["biz_no"] == "login-002"
            # 页面编辑(payload 不带 biz_no)=> UPDATE 保留原值
            status, _, body = request(base_url, "/api/case-defs/AUTH-001", "PUT", {
                "case_id": "AUTH-001", "case_type": "text", "title": "页面改标题"})
            assert json.loads(body)["biz_no"] == "login-001"
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需 sqlite 句柄已关



def test_pull_reanchors_former_id(monkeypatch, _case_api_env):
    """工作站改号重锚(2026-09-30):报文携带 formerId 时旧行原地改新编号,
    biz_no/人工确认态平移、套件成员与双向关联引用同步改写,不产生重复行;
    幂等重拉;目标编号已存在时该条计入 failed,库不受影响。"""
    monkeypatch.setenv("CASE_API_URL", "http://case-api-stub")
    rows_old = [{"id": "AUTH-001", "title": "未登录启动显示登录页",
                 "moduleL1": "登录模块", "status": "stored"}]
    monkeypatch.setattr("dashboard.case_api_client._request",
                        lambda path: {"schema": "cases/0.3", "cases": list(rows_old)})
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # 旧编号首拉:biz_no 按模块分配;挂套件 + 建关联 auto 用例
            request(base_url, "/api/case-defs/pull-api", "POST", {})
            request(base_url, "/api/suites", "POST", {"suite_id": "S1", "name": "套件一"})
            request(base_url, "/api/suites/S1/cases", "POST", {"case_id": "AUTH-001"})
            request(base_url, "/api/case-defs", "POST", {
                "case_id": "AUTO-1", "case_type": "auto", "title": "脚本用例",
                "linked_case_id": "AUTH-001"})
            # 工作站改号:AUTH-001 -> login-001(报文带 formerId 过渡字段)
            rows_new = [{"id": "login-001", "formerId": "AUTH-001",
                         "title": "未登录启动显示登录页", "moduleL1": "登录模块",
                         "status": "stored"}]
            monkeypatch.setattr("dashboard.case_api_client._request",
                                lambda path: {"schema": "cases/0.3", "cases": list(rows_new)})
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            result = json.loads(body)
            assert (result["created"], result["updated"], result["failed"]) == (0, 1, [])
            status, _, body = request(base_url, "/api/case-defs?limit=100")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert set(items) == {"login-001", "AUTO-1"}  # 无重复行
            assert items["login-001"]["biz_no"] == "login-001"  # 编号平移不重排
            assert items["login-001"]["human_confirmed"] == 1
            assert items["login-001"]["suites"] == ["S1"]  # 套件成员跟着改
            assert items["AUTO-1"]["linked_case_id"] == "login-001"  # 关联引用跟着改
            # 幂等重拉:旧行已不在库,按正常更新处理,不重复
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            result = json.loads(body)
            assert (result["created"], result["updated"]) == (0, 1)
            status, _, body = request(base_url, "/api/case-defs?limit=100")
            assert len(json.loads(body)["items"]) == 2
            # 冲突:重锚目标编号已被别的行占用 => 该条 failed,库不受影响
            rows_conflict = [{"id": "AUTO-1", "formerId": "login-001",
                              "title": "撞号用例", "moduleL1": "登录模块"}]
            monkeypatch.setattr("dashboard.case_api_client._request",
                                lambda path: {"schema": "cases/0.3", "cases": list(rows_conflict)})
            status, _, body = request(base_url, "/api/case-defs/pull-api", "POST", {})
            result = json.loads(body)
            assert len(result["failed"]) == 1 and "重锚冲突" in result["failed"][0]["reason"]
            status, _, body = request(base_url, "/api/case-defs?limit=100")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["login-001"]["title"] == "未登录启动显示登录页"  # 原行未被动
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()  # Windows 删库文件需 sqlite 句柄已关


# ---------- case_scope 用例范围(2026-10-10):校验/保留语义/REG- 迁移/auto 内容置空 ----------

def test_case_scope_validation_and_retention(_case_api_env):
    """case_scope 校验与保留语义:非法值报 400;payload 不携带 UPDATE 保留人工归类、
    显式携带(含''=未分类)才生效;REG- 前缀新入库未携带默认 regression;
    auto 用例 content 强制置空防脏数据;batch-update 白名单含 case_scope。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            # REG- 前缀新入库未携带 => 默认 regression;普通编号显式带 full
            status, _, body = request(base_url, "/api/case-defs", "POST", {
                "case_id": "REG-L2-POS-001", "case_type": "text", "title": "回归冒烟"})
            assert status == 201 and json.loads(body)["case_scope"] == "regression"
            status, _, body = request(base_url, "/api/case-defs", "POST", {
                "case_id": "LOGIN-001", "case_type": "text", "title": "普通用例",
                "case_scope": "full"})
            assert json.loads(body)["case_scope"] == "full"
            # 页面编辑/sync 重跑(payload 不携带)=> UPDATE 保留人工归类
            status, _, body = request(base_url, "/api/case-defs/LOGIN-001", "PUT", {
                "case_id": "LOGIN-001", "case_type": "text", "title": "页面改标题"})
            assert json.loads(body)["case_scope"] == "full"
            # 显式清空('')=> 未分类
            status, _, body = request(base_url, "/api/case-defs/LOGIN-001", "PUT", {
                "case_id": "LOGIN-001", "case_type": "text", "title": "改回未分类",
                "case_scope": ""})
            assert json.loads(body)["case_scope"] == ""
            # 非法值 => 400
            with pytest.raises(urllib.error.HTTPError) as error_info:
                request(base_url, "/api/case-defs", "POST", {
                    "case_id": "X-1", "case_type": "text", "title": "坏范围",
                    "case_scope": "bogus"})
            assert error_info.value.code == 400
            # auto 用例 content 强制置空(防脏数据);file_path/关联不受影响
            status, _, body = request(base_url, "/api/case-defs", "POST", {
                "case_id": "AUTO-1", "case_type": "auto", "title": "脚本用例",
                "content": "print('脏数据')", "linked_case_id": "REG-L2-POS-001"})
            created = json.loads(body)
            assert status == 201 and created["content"] == ""
            status, _, body = request(base_url, "/api/case-defs/AUTO-1")
            assert json.loads(body)["content"] == ""
            # batch-update 白名单含 case_scope;非法值报 400
            status, _, body = request(base_url, "/api/case-defs/batch-update", "POST", {
                "case_ids": ["REG-L2-POS-001"], "patch": {"case_scope": "full"}})
            assert json.loads(body)["updated"] == 1
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["REG-L2-POS-001"]["case_scope"] == "full"
            with pytest.raises(urllib.error.HTTPError) as error_info:
                request(base_url, "/api/case-defs/batch-update", "POST", {
                    "case_ids": ["REG-L2-POS-001"], "patch": {"case_scope": "bogus"}})
            assert error_info.value.code == 400
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()


def test_case_scope_migration_backfills_reg_prefix():
    """存量库启动迁移(照 human_confirmed/biz_no 补列写法):补 case_scope 列并把
    REG- 前缀存量行一次性归类 regression,非 REG- 行留未分类;幂等可重复启动。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        db_path = Path(temporary_directory) / "dashboard.sqlite3"
        # 手工造一个 case_scope 上线前的存量库(仅 case_defs 表,其余表由 create_server 自建)
        connection = sqlite3.connect(db_path)
        connection.executescript(
            """
            CREATE TABLE case_defs (
                case_id TEXT PRIMARY KEY, case_type TEXT NOT NULL DEFAULT 'auto',
                title TEXT NOT NULL DEFAULT '', module TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                source_bug TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '', linked_case_id TEXT NOT NULL DEFAULT '',
                link_strength TEXT NOT NULL DEFAULT 'strong',
                human_confirmed INTEGER NOT NULL DEFAULT 0,
                biz_no TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO case_defs (case_id, case_type, title, created_at, updated_at)"
            " VALUES (?, 'text', ?, '2026-01-01', '2026-01-01')",
            [("REG-L2-POS-001", "存量回归一"), ("REG-L2-NEG-002", "存量回归二"),
             ("LOGIN-L1-POS-001", "普通用例")],
        )
        connection.commit()
        connection.close()
        server = create_server("127.0.0.1", 0, db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["REG-L2-POS-001"]["case_scope"] == "regression"
            assert items["REG-L2-NEG-002"]["case_scope"] == "regression"
            assert items["LOGIN-L1-POS-001"]["case_scope"] == ""
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()
        # 幂等:同库重启服务,迁移不重复执行、结果稳定
        server = create_server("127.0.0.1", 0, db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            status, _, body = request(base_url, "/api/case-defs")
            items = {c["case_id"]: c for c in json.loads(body)["items"]}
            assert items["REG-L2-POS-001"]["case_scope"] == "regression"
            assert items["LOGIN-L1-POS-001"]["case_scope"] == ""
        finally:
            server.shutdown()
            server.server_close()
            gc.collect()
