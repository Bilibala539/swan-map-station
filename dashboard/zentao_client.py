"""禅道客户端(步骤 13):平台服务侧提单。

凭据只从环境变量读取(部署机 .env.local,不进 git):
    ZENTAO_URL / ZENTAO_ACCOUNT / ZENTAO_PASSWORD
    ZENTAO_PROJECT_ID / ZENTAO_PRODUCT_ID(测试单回写用;bug 单的 product 由调用方/页面选择传入)

禅道 22.1 走 REST API v1:
    POST {url}/api.php/v1/tokens        {"account":..., "password":...} -> {token}
    POST {url}/api.php/v1/products/{pid}/bugs   JSON 字段 -> bug 单
    POST {url}/api.php/v1/testtasks             JSON 字段 -> 测试单(M6 结果回写)
    PUT  {url}/api.php/v1/testtasks/{id}        更新测试单(重复回写=更新 desc)

调用纪律:提单不可逆,只允许在页面"建单"按钮人工确认后触发;
环境缺陷类(网关不通/设备掉线)不建业务 bug 单。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class ZentaoError(RuntimeError):
    """禅道 API 调用失败(未配置/认证失败/HTTP 错误)。"""


def _config() -> tuple[str, str, str]:
    url = os.environ.get("ZENTAO_URL", "").rstrip("/")
    account = os.environ.get("ZENTAO_ACCOUNT", "")
    password = os.environ.get("ZENTAO_PASSWORD", "")
    if not url or not account or not password:
        raise ZentaoError("ZENTAO_URL / ZENTAO_ACCOUNT / ZENTAO_PASSWORD 未配置(部署机 .env.local)")
    return url, account, password


def is_configured() -> bool:
    return bool(os.environ.get("ZENTAO_URL", "")
                and os.environ.get("ZENTAO_ACCOUNT", "")
                and os.environ.get("ZENTAO_PASSWORD", ""))


def _request(path: str, method: str, payload: dict | None = None,
             token: str = "") -> dict:
    url, account, password = _config()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Token"] = token
    request = urllib.request.Request(
        f"{url}/api.php/v1{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ZentaoError(f"禅道 API {path} -> HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ZentaoError(f"禅道不可达({url}): {error}") from error


def _token() -> str:
    data = _request("/tokens", "POST", {"account": _config()[1],
                                        "password": _config()[2]})
    token = str(data.get("token") or "")
    if not token:
        raise ZentaoError(f"禅道认证失败: {json.dumps(data, ensure_ascii=False)[:200]}")
    return token


def list_products() -> list[dict]:
    """产品列表(供建单弹窗选择)。"""
    data = _request("/products", "GET", token=_token())
    items = data.get("products") if isinstance(data, dict) else data
    result = []
    for item in items or []:
        result.append({"id": item.get("id"), "name": item.get("name")})
    return result


def create_bug(product_id: int, title: str, steps: str,
               severity: int = 3, pri: int = 3,
               opened_build: str = "trunk", module_id: int = 0,
               keywords: str = "自动化") -> dict:
    """创建 bug 单(人工确认后才调用)。返回禅道响应(含 bug id)。

    平台创建的 bug 单默认在"关键词"字段填"自动化",便于禅道侧筛选自动化发现的缺陷。
    """
    payload = {
        "product": int(product_id),
        "title": title,
        "steps": steps,
        "severity": int(severity),
        "pri": int(pri),
        "type": "codeerror",
        "openedBuild[0]": str(opened_build or "trunk"),
        "module": int(module_id),
        "keywords": str(keywords or "自动化"),
    }
    return _request(f"/products/{int(product_id)}/bugs", "POST",
                    payload, token=_token())


def create_testtask(project_id: int, product_id: int, name: str,
                    build: str, desc: str) -> dict:
    """创建禅道测试单(仅页面"回写禅道测试单"按钮人工触发,禁止任何自动回写)。

    返回禅道响应(含测试单 id)。owner 取 ZENTAO_ACCOUNT(测试单负责人=平台对接账号)。
    """
    payload = {
        "project": int(project_id),
        "product": int(product_id),
        "name": str(name),
        "build": str(build or "trunk"),
        "desc": str(desc),
        "owner": _config()[1],
    }
    return _request("/testtasks", "POST", payload, token=_token())


def update_testtask(task_id: int, desc: str) -> dict:
    """更新禅道测试单 desc(重复回写=更新,幂等;同样仅人工触发)。"""
    return _request(f"/testtasks/{int(task_id)}", "PUT",
                    {"desc": str(desc)}, token=_token())
