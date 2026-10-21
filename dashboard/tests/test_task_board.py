"""平台登录 + AI 任务看板 API 测试(2026-09-29 v1.1,设计 docs/AI任务与平台登录设计.md)。

覆盖:登录闸 401/公开端点/停用即失效/机器通道豁免、账号台账(重复 409/停用启用)、
任务提交校验、筛选分页统计用量、疑问闭环(答复仅提交人)、worker 内部通道状态机
(分析→执行→合并冲突→放弃/完成,含 token 累计与非法迁移 409)。
"""
from __future__ import annotations

import gc
import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from dashboard.server import create_server


def start_server(temporary_directory: str):
    server = create_server("127.0.0.1", 0, Path(temporary_directory) / "dashboard.sqlite3")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, thread, base_url


def stop_server(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    gc.collect()  # sqlite 连接句柄未全关时 Windows 删不掉临时目录


class Client:
    """带 cookie 罐的最小 API 客户端;HTTPError 统一返回 (code, body)。"""

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
        try:
            with urllib.request.urlopen(request_object, timeout=5) as response:
                set_cookie = response.headers.get("Set-Cookie")
                if set_cookie:  # 只在成功响应上接 cookie(登录失败不覆盖旧值)
                    self.cookie = set_cookie.split(";")[0]
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw else None)

    def login(self, username: str):
        return self.req("/api/auth/login", "POST", {"username": username})


def test_auth_gate_login_and_users():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base = start_server(temporary_directory)
        anon = Client(base)
        try:
            # 未登录:浏览器面 API 一律 401,公开端点与机器通道例外
            status, body = anon.req("/api/tasks")
            assert status == 401 and body["error"] == "未登录"
            assert anon.req("/api/auth/me")[0] == 401
            assert anon.req("/api/auth/users")[0] == 401
            status, body = anon.req("/api/auth/presets")
            assert status == 200
            assert [u["username"] for u in body["items"]] == [
                "yumiao", "fushenghao", "wuhouju", "menglingzhao"]  # 空表自动播种
            assert [u["surname"] for u in body["items"]] == [
                "yu", "fu", "wu", "meng"]  # 姓氏全拼随名单下发(v1.2 §B14)
            # 机器通道豁免:无 cookie 照常(逐请求登录闸不闸机器面)
            status, _ = anon.req("/api/runs", "POST", {
                "run_id": "run-machine", "platform": "android", "started_at": "2026-09-29T00:00:00+00:00"})
            assert status == 201
            assert anon.req("/api/pipeline-configs")[0] == 200
            assert anon.req("/api/runs/run-machine")[0] == 200  # GET /api/runs/ 前缀豁免(证据清理用)

            # 登录:名单外 404"用户名不存在";预设名成功并带 swan_user cookie
            status, body = anon.login("nobody")
            assert status == 404 and body["error"] == "用户名不存在"
            status, body = anon.login("yumiao")
            assert status == 200 and body == {"username": "yumiao", "surname": "yu"}
            assert anon.cookie.startswith("swan_user=yumiao")
            assert anon.req("/api/auth/me") == (200, {"username": "yumiao", "surname": "yu"})
            assert anon.req("/api/tasks")[0] == 200

            # 账号台账:重复 409;新增即时进名单;停用即退出名单且会话失效
            status, body = anon.req("/api/auth/users", "POST", {"username": "yumiao"})
            assert status == 409 and body["error"] == "用户名已存在"
            status, _ = anon.req("/api/auth/users", "POST",
                                 {"username": "xinren", "wecom_userid": "xinren-wx"})
            assert status == 201
            presets = [u["username"] for u in anon.req("/api/auth/presets")[1]["items"]]
            assert "xinren" in presets
            status, _ = anon.req("/api/auth/users/xinren", "POST",
                                 {"wecom_userid": "ren-wx2"})
            assert status == 200
            status, _ = anon.req("/api/auth/users/xinren", "POST", {"status": "disabled"})
            assert status == 200
            presets = [u["username"] for u in anon.req("/api/auth/presets")[1]["items"]]
            assert "xinren" not in presets
            assert Client(base).login("xinren")[0] == 404  # 停用=登录页不存在

            # 退出:无请求体 POST(页面原样)也须 200 并清 cookie 后 me 401
            status, body = anon.req("/api/auth/logout", "POST")
            assert status == 200 and body == {"ok": True}
            assert anon.req("/api/auth/me")[0] == 401
        finally:
            stop_server(server, thread)


def test_task_submit_validate_list_and_usage():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base = start_server(temporary_directory)
        yumiao = Client(base)
        yumiao.login("yumiao")
        fusheng = Client(base)
        fusheng.login("fushenghao")
        try:
            # 校验:空标题/超长标题/空描述 → 400,字段说明可直接展示
            status, body = yumiao.req("/api/tasks", "POST", {"title": "", "description": "x"})
            assert status == 400 and "标题" in body["error"]
            status, body = yumiao.req("/api/tasks", "POST", {"title": "t" * 61, "description": "x"})
            assert status == 400
            status, body = yumiao.req("/api/tasks", "POST", {"title": "t", "description": ""})
            assert status == 400
            status, body = yumiao.req("/api/tasks", "POST",
                                      {"title": "t", "description": "x", "desired_time": "不是时间"})
            assert status == 400

            # 提交:任务号日序号自生成,submitter 取登录人不收前端值
            status, body = yumiao.req("/api/tasks", "POST", {
                "title": "修一下登录用例的选择器",
                "description": "LOGIN-L2-POS-002 最近三天必失败",
                "desired_time": "2026-10-02T18:00",
                "submitter": "hacker"})
            assert status == 201
            task_id = body["task_id"]
            assert task_id.startswith("TASK-") and task_id.endswith("-001")
            assert body["status"] == "pending_analysis"
            detail = yumiao.req(f"/api/tasks/{task_id}")[1]
            assert detail["submitter"] == "yumiao"
            assert detail["events"][0]["event_type"] == "submitted"
            assert detail["events"][0]["actor"] == "yumiao"

            status, second = fusheng.req("/api/tasks", "POST",
                                         {"title": "给 1.2.0 出一个测试服包", "description": "x"})
            assert status == 201

            # 列表:bucket/搜索/分页(created_at 同秒内靠 task_id 倒序兜底,新任务在前)
            items = yumiao.req("/api/tasks?bucket=active")[1]["items"]
            assert len(items) == 2 and items[0]["task_id"] == second["task_id"]
            assert items[1]["task_id"] == task_id
            assert set(items[0]) >= {"task_id", "title", "status", "task_type", "risk",
                                     "target_machine", "submitter", "tokens_total", "created_at"}
            assert not [i for i in yumiao.req("/api/tasks?bucket=done")[1]["items"]]
            assert len(yumiao.req("/api/tasks?q=" + quote("选择器"))[1]["items"]) == 1
            assert len(yumiao.req(f"/api/tasks?q={task_id}")[1]["items"]) == 1
            assert len(yumiao.req("/api/tasks?q=fushenghao")[1]["items"]) == 1
            page = yumiao.req("/api/tasks?pagesize=1&page=2")[1]
            assert page["total"] == 2 and len(page["items"]) == 1

            # 详情 404;统计卡(全员口径,mine 按登录人)
            assert yumiao.req("/api/tasks/TASK-20990101-999")[0] == 404
            stats = yumiao.req("/api/tasks/stats")[1]
            assert stats == {"active": 2, "mine": 0, "done": 0, "tokens_total": 0}

            # 用量:全部账号列出,token 合计 0
            usage = {u["username"]: u for u in yumiao.req("/api/tasks/usage")[1]["items"]}
            assert usage["yumiao"]["submitted"] == 1 and usage["fushenghao"]["submitted"] == 1
            assert usage["yumiao"]["tokens_total"] == 0
        finally:
            stop_server(server, thread)


def test_question_loop_answer_only_submitter():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base = start_server(temporary_directory)
        yumiao = Client(base)
        yumiao.login("yumiao")
        fusheng = Client(base)
        fusheng.login("fushenghao")
        worker = Client(base)  # 无 cookie:dispatch 通道是机器豁免
        try:
            task_id = yumiao.req("/api/tasks", "POST", {
                "title": "每日回归为什么全挂了", "description": "176 上回归全红"})[1]["task_id"]

            # 大领导分析后提问 → 疑问待答,挂起前阶段=待分析
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "analyze", "task_id": task_id, "task_type": "troubleshoot",
                "risk": "mid", "target_machine": "176"})[0] == 200
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "question", "task_id": task_id,
                "question": "目标机上的 dev 分支是否可以直接 rebase?"})
            assert status == 200 and body["status"] == "questioning"
            assert body["paused_phase"] == "pending_analysis"
            assert body["questions"][-1]["status"] == "open"

            # 需我处理=疑问待答且提交人=登录人
            assert len(yumiao.req("/api/tasks?bucket=mine")[1]["items"]) == 1
            assert not [i for i in fusheng.req("/api/tasks?bucket=mine")[1]["items"]]
            assert yumiao.req("/api/tasks/stats")[1]["mine"] == 1

            # 答复:非提交人 403(工作流语义服务端同校验);空答复 400;提交人答复回挂起前阶段
            status, body = fusheng.req(f"/api/tasks/{task_id}/answer", "POST", {"answer": "可以"})
            assert status == 403
            assert yumiao.req(f"/api/tasks/{task_id}/answer", "POST", {"answer": "  "})[0] == 400
            status, body = yumiao.req(f"/api/tasks/{task_id}/answer", "POST",
                                      {"answer": "可以,直接 rebase origin/dev"})
            assert status == 200 and body["status"] == "pending_analysis"
            assert body["paused_phase"] is None
            assert body["questions"][-1]["status"] == "answered"
            assert yumiao.req("/api/tasks/stats")[1]["mine"] == 0

            # 二轮:执行中提问 → 答复回执行中(问答历史全保留)
            assert worker.req("/api/dispatch/update", "POST",
                              {"action": "start", "task_id": task_id})[0] == 200
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "question", "task_id": task_id, "question": "二问"})[0] == 200
            detail = yumiao.req(f"/api/tasks/{task_id}")[1]
            assert detail["paused_phase"] == "running" and len(detail["questions"]) == 2
            status, body = yumiao.req(f"/api/tasks/{task_id}/answer", "POST", {"answer": "二答"})
            assert status == 200 and body["status"] == "running"
            events = [e["event_type"] for e in body["events"]]
            assert events.count("question") == 2 and events.count("answered") == 2

            # 答复后状态不对(非疑问待答)→ 409
            assert yumiao.req(f"/api/tasks/{task_id}/answer", "POST", {"answer": "x"})[0] == 409
        finally:
            stop_server(server, thread)


def test_dispatch_worker_state_machine():
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base = start_server(temporary_directory)
        yumiao = Client(base)
        yumiao.login("yumiao")
        worker = Client(base)
        try:
            task_id = yumiao.req("/api/tasks", "POST", {
                "title": "修一下登录用例的选择器", "description": "改 2 个 YAML 选择器"})[1]["task_id"]

            # 队列:活跃任务全量含问答;分析写不合法枚举 → 400
            queue = worker.req("/api/tasks/queue")[1]["items"]
            assert [t["task_id"] for t in queue] == [task_id]
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "analyze", "task_id": task_id,
                "task_type": "hack", "risk": "high"})[0] == 400
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "finish", "task_id": task_id, "ok": True})[0] == 409  # 未开始不能收尾

            # 分析 → 开始执行(代码类默认 merge_status=pending)
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "analyze", "task_id": task_id, "task_type": "code_change",
                "risk": "high", "target_machine": "176",
                "analysis_note": "修改 cases/driver/login 下 2 个 YAML 的选择器",
                "tokens_in": 12000, "tokens_out": 800})
            assert status == 200 and body["status"] == "pending_analysis"
            assert body["task_type"] == "code_change" and body["risk"] == "high"
            assert body["tokens_total"] == 12800
            assert worker.req("/api/dispatch/update", "POST",
                              {"action": "start", "task_id": task_id})[0] == 200
            detail = yumiao.req(f"/api/tasks/{task_id}")[1]  # 详情是浏览器面,worker 只走队列通道
            assert detail["status"] == "running" and detail["merge_status"] == "pending"

            # 合并冲突:任务保持执行中(冲突是属性不是状态),可重试后仍冲突
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "merge", "task_id": task_id, "event": "merge_conflict"})
            assert status == 200 and body["status"] == "running" and body["merge_status"] == "conflict"
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "merge", "task_id": task_id, "event": "merge_retry"})[0] == 200

            # 进展事件 + token 增量
            assert worker.req("/api/dispatch/update", "POST", {
                "action": "progress", "task_id": task_id,
                "detail": "已提交修复,回归 12/12 通过", "tokens_in": 300, "tokens_out": 900})[0] == 200
            assert yumiao.req(f"/api/tasks/{task_id}")[1]["tokens_total"] == 14000

            # AI 放弃合并 → 失败终态(merge_abandon + finished 事件)
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "finish", "task_id": task_id, "ok": False,
                "merge_status": "abandoned",
                "error_message": "合并冲突未解决,AI 放弃合并"})
            assert status == 200
            assert body["status"] == "failed" and body["finished_at"]
            assert body["merge_status"] == "abandoned"
            assert body["error_message"] == "合并冲突未解决,AI 放弃合并"
            events = [(e["event_type"], e["actor"]) for e in body["events"]]
            assert ("merge_abandon", "小领导") in events and ("finished", "大领导") in events

            # 终态后:队列清空、再收尾 409、bucket=done 可见
            assert not [i for i in worker.req("/api/tasks/queue")[1]["items"]]
            assert worker.req("/api/dispatch/update", "POST",
                              {"action": "finish", "task_id": task_id, "ok": True})[0] == 409
            assert [i["task_id"] for i in yumiao.req("/api/tasks?bucket=done")[1]["items"]] == [task_id]

            # note:只追加事件不改状态(企微通知失败等旁路日志,终态任务也可记)
            assert worker.req("/api/dispatch/update", "POST", {"action": "note", "task_id": task_id})[0] == 400
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "note", "task_id": task_id,
                "event_type": "wecom_failed", "detail": "wecom-cli 不存在,群消息未发"})
            assert status == 200 and body["status"] == "failed"  # 状态纹丝不动
            assert body["events"][-1]["event_type"] == "wecom_failed"
            assert body["events"][-1]["actor"] == "系统"
            usage = {u["username"]: u for u in yumiao.req("/api/tasks/usage")[1]["items"]}
            assert usage["yumiao"]["failed"] == 1 and usage["yumiao"]["tokens_total"] == 14000

            # 成功路径(非代码类):完成,无 merge_status;报告原文入库
            task2 = yumiao.req("/api/tasks", "POST",
                               {"title": "跑一遍冒烟", "description": "SMKE"})[1]["task_id"]
            worker.req("/api/dispatch/update", "POST", {
                "action": "analyze", "task_id": task2, "task_type": "test_run",
                "risk": "low", "target_machine": "167"})
            worker.req("/api/dispatch/update", "POST", {"action": "start", "task_id": task2})
            status, body = worker.req("/api/dispatch/update", "POST", {
                "action": "finish", "task_id": task2, "ok": True,
                "report_md": "## 结论\n12/12 通过"})
            assert status == 200 and body["status"] == "done"
            assert body["merge_status"] is None and "12/12" in body["report_md"]
        finally:
            stop_server(server, thread)


def test_task_board_endpoint():
    """竖版看板单请求(v1.2 §A9):五列写死顺序/counts 全量/stats/item 最小字段集/
    q 过滤/每列 50 条上限;/api/tasks 列表接口保持原样。"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        server, thread, base = start_server(temporary_directory)
        yumiao = Client(base)
        yumiao.login("yumiao")
        try:
            for n in range(1, 56):
                yumiao.req("/api/tasks", "POST",
                           {"title": f"批量任务{n:02d}", "description": "cap"})
            t_done = yumiao.req("/api/tasks", "POST",
                                {"title": "跑一遍冒烟", "description": "SMKE"})[1]["task_id"]
            t_fail = yumiao.req("/api/tasks", "POST",
                                {"title": "查合并冲突", "description": "CF"})[1]["task_id"]
            worker = Client(base)
            worker.login("fushenghao")
            for task_id, task_type in ((t_done, "test_run"), (t_fail, "code_change")):
                worker.req("/api/dispatch/update", "POST", {
                    "action": "analyze", "task_id": task_id,
                    "task_type": task_type, "risk": "low", "target_machine": "167"})
                worker.req("/api/dispatch/update", "POST", {
                    "action": "start", "task_id": task_id,
                    "branch_name": "u/x/t1", "worktree_path": "/tmp/wt"})
            worker.req("/api/dispatch/update", "POST", {
                "action": "finish", "task_id": t_done, "ok": True, "report_md": "ok"})
            worker.req("/api/dispatch/update", "POST", {
                "action": "finish", "task_id": t_fail, "ok": False,
                "merge_status": "abandoned", "error_message": "冲突未解决"})
            # 五列写死顺序;groups 只含五列
            board = yumiao.req("/api/tasks/board")[1]
            assert list(board["groups"].keys()) == [
                "pending_analysis", "questioning", "running", "done", "failed"]
            assert board["counts"]["pending_analysis"] == 55
            assert len(board["groups"]["pending_analysis"]) == 50  # 每列上限(§A8)
            assert board["groups"]["pending_analysis"][0]["title"] == "批量任务55"  # created_at 倒序
            assert [i["task_id"] for i in board["groups"]["done"]] == [t_done]
            assert [i["task_id"] for i in board["groups"]["failed"]] == [t_fail]
            assert board["stats"] == yumiao.req("/api/tasks/stats")[1]  # 统计卡同口径
            item = board["groups"]["done"][0]
            assert sorted(item.keys()) == [
                "created_at", "finished_at", "merge_status", "risk", "status",
                "submitter", "target_machine", "task_id", "task_type", "title"]
            assert item["finished_at"] and item["merge_status"] is None
            failed_item = board["groups"]["failed"][0]  # 冲突放弃终态:failed + abandoned 双标记
            assert failed_item["finished_at"] and failed_item["merge_status"] == "abandoned"
            # q 过滤:计数与分组同口径
            board_q = yumiao.req("/api/tasks/board?q=" + quote("冒烟"))[1]
            assert board_q["counts"] == {"done": 1}
            assert [i["task_id"] for i in board_q["groups"]["done"]] == [t_done]
            # 旧列表接口不受影响(bucket=done 是"已结束"口径=done+failed)
            assert len(yumiao.req("/api/tasks?status=done")[1]["items"]) == 1
            assert len(yumiao.req("/api/tasks?bucket=done")[1]["items"]) == 2
        finally:
            stop_server(server, thread)


def test_users_surname_migration_backfill():
    """users 表轻量迁移(v1.2 §B14):存量老库(无 surname 列)自动补列并把
    预设账号空姓氏回填成全拼;新库 DDL 直含 surname。"""
    import sqlite3
    with tempfile.TemporaryDirectory() as temporary_directory:
        db_path = Path(temporary_directory) / "dashboard.sqlite3"
        legacy = sqlite3.connect(db_path)  # 造老库:users 无 surname 列,4 预设账号齐(空表才播种,须全量自带)
        legacy.execute(
            "CREATE TABLE users (username TEXT PRIMARY KEY,"
            " wecom_userid TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',"
            " created_at TEXT NOT NULL)")
        for name in ("yumiao", "fushenghao", "wuhouju", "menglingzhao"):
            legacy.execute("INSERT INTO users VALUES (?, '', 'active', '2026-09-29')", (name,))
        legacy.commit()
        legacy.close()
        server, thread, base = start_server(temporary_directory)
        anon = Client(base)
        try:
            presets = {u["username"]: u["surname"]
                       for u in anon.req("/api/auth/presets")[1]["items"]}
            assert presets == {"yumiao": "yu", "fushenghao": "fu",
                               "wuhouju": "wu", "menglingzhao": "meng"}  # 老库回填
            # 设置弹窗新增账号:surname 空串下发,由前端回退规则显示(设计定稿)
            yumiao = Client(base)
            yumiao.login("yumiao")  # /api/auth/users 在登录闸后
            yumiao.req("/api/auth/users", "POST", {"username": "xinren"})
            users = {u["username"]: u["surname"]
                     for u in yumiao.req("/api/auth/users")[1]["items"]}
            assert users["xinren"] == "" and users["yumiao"] == "yu"
        finally:
            stop_server(server, thread)
