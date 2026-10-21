"""外部用例 API 适配层(2026-09-28 准备):文字用例将来统一从这里拉取。

背景与定位:
  文字用例现在的入库途径有三种——仓库同步(scripts/sync_cases.py)、页面批量导入
  与页面单条新建(二者 2026-10-16 已下线)。2026-09-28 拍板:文字用例将改为通过
  一个外部 API 接口获取,本模块就是那条统一管线的适配层;接口细节(地址/鉴权/
  报文格式)待用户提供,拿到后只需补全 fetch_text_cases 的请求与 _normalize_case
  的字段映射(见文末「待用户提供清单」),管线与端点不用再动。

管线(fetch -> normalize -> upsert):
  pull_text_cases_into(store)
    1. fetch_text_cases()      拉远端原始报文(未配置时抛 CaseApiError,不产生任何写入)
    2. _normalize_case()       逐条映射到 case_defs 现有字段(不发明新字段)
    3. store.upsert_case_def() 单条校验+入库(复用优先级归一/模块自动登记,与
                               sync_cases.py、页面保存完全同口径),case_id 为锚幂等 upsert
  触发入口:
    - 页面按钮(用例管理页工具栏「从 API 拉取」) -> POST /api/case-defs/pull-api
    - 脚本:python -m dashboard.case_api_client --url http://127.0.0.1:8080

配置(部署机 .env.local,不进 git;server.py 启动时已注入环境变量):
    CASE_API_URL            列表接口基地址(空=未配置,整条管线明确跳过)
                            已对接值:http://192.168.12.167:8090/api/review
    CASE_API_TOKEN          鉴权凭据(用例人审工作站无鉴权,留空;字段预留)
    CASE_API_AUTH_HEADER    凭据请求头名,默认 Authorization(需 Bearer 前缀时把前缀写进 token 值)
    CASE_API_TIMEOUT        请求超时秒数,默认 30
    CASE_API_STATUS_FILTER  远端状态过滤,默认 stored,pending(2026-09-29 需求变更:
                            pending 待审也进平台,靠 case_defs.human_confirmed=0 区分;
                            rejected 已驳回仍默认不进);逗号分隔多值,all/空=不过滤;
                            报文无 status 字段的条目一律保留

stub 行为:
  CASE_API_URL 为空 => is_configured()=False,拉取端点返回 skipped=true 与
  「等待配置 API 接口」提示,不报错、不写库;已配置但请求失败 => CaseApiError
  (端点转 502),同样不写库。任何失败路径都不产生半截数据。

已校准(2026-09-29,「Swan 功能地图工作站·用例人审 API」,schema=cases/0.3):
  数据源=功能地图工作站两道门人审工作台(门一=用例定义,门二=编译脚本,
  双门通过且 pass=true 后 status=stored 即正式入库用例);
  GET {URL}/cases 全量返回、无分页、无鉴权、拉取模式(平台主动调);
  字段映射见 _normalize_case:id→case_id、title→title、priority→priority(P0-P2,
  upsert 统一归一)、moduleL1/L2→module(中文原样入 slug,后续可在模块管理改名归并)、
  definition.steps[{no,action,locator,data,expect}]与 overallExpect→content、
  script.file/hash 只留索引行(脚本代码本体不进 content)、SWAN-BUG-* 正则照旧。

需求变更(2026-09-29,人工确认字段):
  case_defs 加 human_confirmed 列(0/1)=「经人审工作站两道门确认」,与其他来源
  无关(sync_cases/页面手工/批量导入不传一律 0);_normalize_case 按远端 status
  映射(stored→1,pending/无状态→0),每次拉取显式携带,upsert 显式值覆盖原值,
  工作站改判(如 stored→pending)重拉即如实反映;状态过滤默认改为 stored,pending。
仍未定(不阻塞使用,待拍板):
  - 增量拉取:接口无更新时间戳,当前全量 upsert(幂等),量大后再谈
  - 同步频率:目前手动触发(页面按钮/脚本/curl 同一端点),要定时再加
  - status 过滤默认 stored,pending:rejected 不进用例管理,要放开改 CASE_API_STATUS_FILTER
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# bug 编号提取规则与 sync_cases.py 保持一致
BUG_PATTERN = re.compile(r"SWAN-BUG-\d{8}-\d+")
# API 来源行的 source 标记:将来切换 API 源后可据此识别/清理旧同步行
SOURCE_TAG = "case-api"


class CaseApiError(RuntimeError):
    """外部用例 API 调用失败(未配置/认证失败/HTTP 错误/报文不合法)。"""


def _load_dotenv_local() -> None:
    """读项目根 .env.local 注入环境变量(已存在的环境变量优先)。

    与 server.py 的同名函数同逻辑但不 import server(避免拉起 jenkins 等重依赖),
    供 python -m dashboard.case_api_client 独立运行时补配置。
    """
    env_file = PROJECT_ROOT / ".env.local"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _config() -> dict[str, str]:
    _load_dotenv_local()
    return {
        "url": os.environ.get("CASE_API_URL", "").rstrip("/"),
        "token": os.environ.get("CASE_API_TOKEN", ""),
        "auth_header": os.environ.get("CASE_API_AUTH_HEADER", "Authorization"),
        "timeout": os.environ.get("CASE_API_TIMEOUT", "30"),
    }


def is_configured() -> bool:
    return bool(_config()["url"])


def _request(path: str, method: str = "GET", payload: dict | None = None) -> Any:
    """调外部用例 API;非 2xx/网络失败/报文非 JSON 一律抛 CaseApiError。"""
    config = _config()
    headers = {"Accept": "application/json"}
    if config["token"]:
        headers[config["auth_header"]] = config["token"]  # 凭据原样放头,方案待定后校准
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{config['url']}{path}", data=data,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=int(config["timeout"])) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise CaseApiError(f"用例 API {path} -> HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CaseApiError(f"用例 API 不可达({config['url']}): {error}") from error
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise CaseApiError(f"用例 API 返回不是 JSON: {body[:200]}") from error


# ---- 规范化:远端报文 -> case_defs 现有字段 ----
# 报文格式未知,下面是按常见命名的最佳猜测映射;拿到样例报文后以实际字段为准校准,
# 原则:只映射到 case_defs 已有字段,映射不到的远端字段直接忽略,不发明新字段。
_FIELD_CANDIDATES = {
    # case_defs 字段: 远端报文里按顺序尝试的候选 key
    "case_id": ("case_id", "id", "code", "caseCode", "case_code"),
    "title": ("title", "name", "summary", "subject", "caseName"),
    "module": ("module", "module_name", "moduleName", "category"),
    # 人审工作站报文无上述 module 键,由 _normalize_case 回退取 moduleL1/moduleL2
    "priority": ("priority", "level", "pri"),
}
# ---- 有义展示编号(2026-09-30):wifi-001 / login-001 式 ----
# 模块名(中文原样,来自 module/moduleL1) -> 编号前缀 slug。映射不到返回 "",
# 该条不分配编号,展示回退工作站原始编号;新模块出现时在此补一行即可。
_MODULE_SLUG_MAP = {
    "登录": "login",
    "wi-fi": "wifi", "wifi": "wifi",
    "网络": "network",
    "播放器": "player",
    "基建": "infra",
    "平台集成": "platform",
    "其它": "misc", "其他": "misc",
}
_BIZ_NO_PATTERN = re.compile(r"^([a-z][a-z0-9]*)-(\d+)$")
# 工作站改号过渡字段(2026-09-30):报文里标记"本条旧编号"的候选 key
_FORMER_ID_KEYS = ("formerId", "former_id", "oldId", "old_id", "previousId")


def module_slug(module: Any) -> str:
    """模块名 -> 有义编号前缀(login/wifi/...);映射不到返回空串。

    宽容匹配:大小写归一,剥掉「设备模块/模块/层」等后缀再查表
    (登录模块→login、Wi-Fi 设备模块→wifi、网络层→network)。
    """
    text = _as_text(module)
    if not text:
        return ""
    key = re.sub(r"(设备模块|模块|层)$", "", text.strip()).strip().lower()
    return _MODULE_SLUG_MAP.get(key, "")


def next_biz_no(slug: str, counters: dict[str, int]) -> str:
    """分配模块内下一个编号(调用方保证 slug 非空);counters 就地 +1。

    counters 由管线从库中已分配编号取各前缀 max 初始化,重拉/补号幂等。
    """
    counters[slug] = counters.get(slug, 0) + 1
    return f"{slug}-{counters[slug]:03d}"


_STEPS_KEYS = ("steps", "step", "procedure", "test_steps")
_PRE_KEYS = ("precondition", "preconditions", "pre_condition", "preReq")
_EXPECT_KEYS = ("expected", "expected_result", "expectation", "expect")


def _as_text(value: Any) -> str:
    """远端字段值统一转成字符串;列表/字典拍平成可读文本,None/空归空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        lines = []
        for item in value:
            if isinstance(item, dict):
                # {step/expected} 结构按「步骤 -> 预期」拼行,常见用例库格式
                step = _as_text(item.get("step") or item.get("desc")
                                or item.get("action") or item.get("content"))
                expect = _as_text(item.get("expected") or item.get("expect") or "")
                lines.append(f"{step} -> {expect}" if expect else step)
            else:
                lines.append(_as_text(item))
        return "\n".join(line for line in lines if line)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def _build_content(raw: dict[str, Any]) -> str:
    """组装 content(详情抽屉展示用):远端自带 content 字段则直取;其次按
    前置/步骤/预期结构化拼接;结构化字段一个都没有时兜底存整条原始报文
    JSON,保证任何报文都不丢信息。"""
    if "content" in raw:
        direct = _as_text(raw["content"])
        if direct:
            return direct
    precondition = next((_as_text(raw[k]) for k in _PRE_KEYS if k in raw), "")
    steps = next((_as_text(raw[k]) for k in _STEPS_KEYS if k in raw), "")
    expected = next((_as_text(raw[k]) for k in _EXPECT_KEYS if k in raw), "")
    if not (precondition or steps or expected):
        return json.dumps(raw, ensure_ascii=False, indent=2)
    parts: list[str] = []
    if precondition:
        parts.append(f"前置:{precondition}")
    if steps:
        parts.append(f"步骤:\n{steps}")
    if expected:
        parts.append(f"预期:{expected}")
    return "\n\n".join(parts)


def _render_workbench_steps(steps: Any) -> str:
    """人审工作站 definition.steps[{no,action,locator,data,expect}] -> 可读步骤行:
    「1. 动作(定位:x;数据:y)→ 预期:z」;locator/data 为空或「无」时省略。"""
    if not isinstance(steps, list):
        return _as_text(steps)
    lines: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            lines.append(_as_text(step))
            continue
        no = str(step.get("no") or "").strip()
        action = _as_text(step.get("action"))
        detail = ";".join(
            f"{label}:{value}"
            for label, key in (("定位", "locator"), ("数据", "data"))
            for value in (_as_text(step.get(key)),)
            if value and value not in ("无", "—")
        )
        expect = _as_text(step.get("expect"))
        head = f"{no}. {action}" if no else action
        if detail:
            head += f"({detail})"
        if expect:
            head += f"→ 预期:{expect}"
        lines.append(head)
    return "\n".join(line for line in lines if line)


def _build_workbench_content(raw: dict[str, Any]) -> str:
    """人审工作站报文(cases/0.3)的 content 组装:嵌套步骤 + 总预期 + 脚本索引行。

    script 只留 file/hash 索引便于将来关联自动化,代码本体(单条可达数万字)不进 content。
    """
    definition = raw.get("definition") if isinstance(raw.get("definition"), dict) else {}
    parts: list[str] = []
    steps = _render_workbench_steps(definition.get("steps"))
    if steps:
        parts.append(f"步骤:\n{steps}")
    overall = _as_text(definition.get("overallExpect"))
    if overall:
        parts.append(f"预期:{overall}")
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    script_file = _as_text(script.get("file"))
    if script_file:
        tail = f"脚本:{script_file}"
        script_hash = _as_text(script.get("hash"))
        if script_hash:
            tail += f"(hash {script_hash[:12]})"
        parts.append(tail)
    return "\n\n".join(parts)


def _normalize_case(raw: Any) -> dict[str, Any]:
    """单条远端报文 -> case_defs 字段口径的 dict(供 store.upsert_case_def 消费)。

    case_type 固定 text(本管线只管文字用例);source 标 SOURCE_TAG 以区分
    API 来源行;file_path 留空(API 用例没有仓库文件);linked_case_id 留空
    (text 用例不做反向关联);link_strength 走 upsert 默认 strong。
    缺 case_id/标题时抛 ValueError,由管线计入 failed,不写库。
    """
    if not isinstance(raw, dict):
        raise ValueError("报文条目必须是对象")
    fields: dict[str, Any] = {}
    for name, candidates in _FIELD_CANDIDATES.items():
        fields[name] = next((_as_text(raw[k]) for k in candidates
                             if k in raw and _as_text(raw[k])), "")
    if not fields["case_id"]:
        raise ValueError("缺少用例 ID(case_id/id/code 均未命中)")
    if not fields["title"]:
        raise ValueError("缺少标题(title/name 均未命中)")
    # bug 编号:对整条报文做与 sync_cases 同规则的正则提取,命中取第一个
    bugs = sorted(set(BUG_PATTERN.findall(json.dumps(raw, ensure_ascii=False))))
    # 模块:优先报文显式 module 键;人审工作站报文回退 moduleL1(粗粒度)、再 moduleL2
    module = fields["module"] or next((_as_text(raw[k]) for k in ("moduleL1", "moduleL2")
                                       if k in raw and _as_text(raw[k])), "")
    definition = raw.get("definition")
    if isinstance(definition, dict) and (definition.get("steps") or definition.get("overallExpect")):
        content = _build_workbench_content(raw)
    else:
        content = _build_content(raw)
    return {
        "case_id": fields["case_id"],
        "case_type": "text",
        "title": fields["title"],
        "module": module,
        "priority": fields["priority"],  # P0-P3 归一交给 upsert_case_def,与页面/同步同口径
        "source": SOURCE_TAG,
        "source_bug": bugs[0] if bugs else "",
        "file_path": "",
        "content": content,
        "linked_case_id": "",
        # 人工确认(2026-09-29):远端 status=stored(过两道门人审)才标 1,
        # pending 及无状态来源标 0;每次拉取显式携带,工作站改判重拉即如实回写
        "human_confirmed": 1 if str(raw.get("status", "")).strip().lower() == "stored" else 0,
    }


def _status_allowed() -> set[str] | None:
    """CASE_API_STATUS_FILTER -> 允许的远端状态集合;all/空 = 不过滤(返回 None)。

    默认 stored,pending(2026-09-29 需求变更):pending(待审)也进平台,靠
    case_defs.human_confirmed=0 区分;rejected(已驳回)仍默认不进,要放开改配置。
    报文条目没有 status 字段时一律保留(兼容无状态来源,见 fetch_text_cases)。
    """
    raw = os.environ.get("CASE_API_STATUS_FILTER", "stored,pending").strip().lower()
    if raw in ("", "all"):
        return None
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def fetch_text_cases() -> list[dict[str, Any]]:
    """拉取远端文字用例原始列表(2026-09-29 已校准:用例人审工作站 cases/0.3)。

    GET {CASE_API_URL}/cases,全量返回、无分页;按 CASE_API_STATUS_FILTER 过滤
    status(报文无 status 字段的条目保留)。接口无更新时间戳,增量暂不做,全量幂等。
    """
    data = _request("/cases")
    items: Any = data
    if isinstance(data, dict):
        for key in ("cases", "items", "data", "list"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not isinstance(items, list):
        raise CaseApiError("用例 API 返回里找不到用例列表(cases/items/data/list 均未命中)")
    allowed = _status_allowed()
    if allowed is not None:
        items = [row for row in items
                 if not isinstance(row, dict) or "status" not in row
                 or str(row.get("status", "")).strip().lower() in allowed]
    return items


def pull_text_cases_into(store: Any) -> dict[str, Any]:
    """管线入口:拉取 -> 规范化 -> 逐条 upsert 入库。返回导入结果摘要。

    计数口径(沿用已下线的批量导入):库中已有 case_id 计 updated,
    新 case_id 计 created;单条失败(缺 ID/标题等)只记 failed 不影响其他行。
    """
    raw_items = fetch_text_cases()
    with store.connect() as connection:
        existing_biz = {
            row["case_id"]: (row["biz_no"] or "")
            for row in connection.execute("SELECT case_id, biz_no FROM case_defs").fetchall()
        }
    # 有义编号(2026-09-30):新行/老行空编号时按模块分配 wifi-001 式,已分配的
    # 终身不变(重拉不重排);计数器从库中现有编号取 max+1,补号不撞号
    biz_counters: dict[str, int] = {}
    for biz in existing_biz.values():
        matched = _BIZ_NO_PATTERN.match(biz)
        if matched:
            biz_counters[matched.group(1)] = max(
                biz_counters.get(matched.group(1), 0), int(matched.group(2)))
    created = updated = 0
    failed: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        try:
            case = _normalize_case(raw)
            # 工作站改号重锚(2026-09-30):报文携带 formerId=旧编号且旧行在库时,
            # 旧行原地改新编号(套件/关联引用同步改写,biz_no 等字段平移),再走
            # 正常 upsert;已重锚过(旧行不在库)或新旧同号则直接 upsert,幂等
            former = next((_as_text(raw[k]) for k in _FORMER_ID_KEYS
                           if k in raw and _as_text(raw[k])), "")
            if former and former != case["case_id"] and former in existing_biz:
                if case["case_id"] in existing_biz:
                    raise ValueError(f"重锚冲突:目标编号已存在 {case['case_id']}")
                try:
                    case["biz_no"] = store.reanchor_case_def(former, case["case_id"])
                except KeyError as error:
                    raise ValueError(str(error)) from error
                existing_biz[case["case_id"]] = case["biz_no"]
                del existing_biz[former]
            if not existing_biz.get(case["case_id"], ""):
                slug = module_slug(case["module"])
                if slug:
                    case["biz_no"] = next_biz_no(slug, biz_counters)
            store.upsert_case_def(case)
            if case["case_id"] in existing_biz:
                updated += 1
            else:
                created += 1
                existing_biz[case["case_id"]] = case.get("biz_no", "")
        except (ValueError, CaseApiError) as error:
            bad_id = raw.get("case_id", raw.get("id", "")) if isinstance(raw, dict) else ""
            failed.append({"row": index + 1, "case_id": str(bad_id), "reason": str(error)})
    print(f"[case-api] 拉取完成: 共 {len(raw_items)} 条, 新增 {created}, "
          f"更新 {updated}, 失败 {len(failed)}", flush=True)
    return {"ok": True, "total": len(raw_items), "created": created,
            "updated": updated, "failed": failed, "source": SOURCE_TAG}


def main() -> int:
    """脚本触发入口:调平台管理端点 POST /api/case-defs/pull-api(与页面按钮同一条路)。"""
    import argparse
    import sys
    _load_dotenv_local()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="从外部用例 API 拉取文字用例(走平台管理端点)")
    parser.add_argument("--url", default=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args()
    base = args.url.rstrip("/")
    request = urllib.request.Request(
        f"{base}/api/case-defs/pull-api", data=b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        print(f"拉取失败: HTTP {error.code}: {detail}")
        return 1
    if result.get("skipped"):
        print(f"已跳过: {result.get('message', '')}")
        return 0
    print(f"拉取完成: 共 {result.get('total', 0)} 条, 新增 {result.get('created', 0)}, "
          f"更新 {result.get('updated', 0)}, 失败 {len(result.get('failed') or [])}")
    for item in result.get("failed") or []:
        print(f"  [fail] 行 {item.get('row')} {item.get('case_id') or '-'}: {item.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
