"""APK 注册脚本(步骤 10 构建产物入口)。

把一个 APK 文件注册进平台包管理,返回 apk_id(供执行会话/流水线任务关联)。
apk_id 规则与 build_swan_apk.py 一致:swan-<branch>-<shortsha>-<yyyymmdd>-<server_type>,
shortsha 取 --commit 前 7 位(未传则退化为产物 sha256 前 7 位);也可 --apk-id 手动指定。
用法:
  python scripts/register_apk.py /opt/swan-build/apks/swan-dev-1.2.3.apk \
      --version 1.2.3 --branch main --flavor prod --source dev
  python scripts/register_apk.py swan-audio-1.2.3.apk --server-type formal
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PLATFORM_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080").rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description="注册 APK 进平台包管理")
    parser.add_argument("apk_path", help="APK 文件路径")
    parser.add_argument("--version", default="", help="应用版本号")
    parser.add_argument("--branch", default="", help="来源分支")
    parser.add_argument("--flavor", default="",
                        help="渠道/flavor(缺省按 server_type 映射:test→profile/formal→release)")
    parser.add_argument("--commit", default="", help="来源 commit(取前 7 位拼 apk_id)")
    parser.add_argument("--apk-id", default="", help="手动指定 apk_id(默认按规则自动生成)")
    parser.add_argument("--source", default="manual", help="来源(manual/pipeline/dev)")
    # 与 build_swan_apk.py 同口径:formal=正式服 / test=测试服,默认测试服
    parser.add_argument("--server-type", choices=["formal", "test"], default="test",
                        help="包对应的服务器环境(formal=正式服 / test=测试服,默认 test)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    apk = Path(args.apk_path)
    if not apk.is_file():
        print(f"文件不存在: {apk}")
        return 1
    file_hash = hashlib.sha256(apk.read_bytes()).hexdigest()
    short = args.commit[:7] if args.commit else file_hash[:7]
    # apk_id 是平台必填的 upsert 键;分支缺省用 manual,避免 apk_id 出现空段;
    # server_type 段与 build_swan_apk.py 口径一致:同分支同日 formal/test 两包不互相覆盖
    apk_id = args.apk_id or (
        f"swan-{args.branch or 'manual'}-{short}-{time.strftime('%Y%m%d')}-{args.server_type}")
    # flavor 缺省按构建模式口径填(映射与 build_swan_apk.py:287 一致,档案坑 13:
    # server_type=test→profile 默认连测试服 / formal→release 锁正式服);显式传 --flavor 时以用户值为准
    build_mode = "profile" if args.server_type == "test" else "release"

    payload = {
        "apk_id": apk_id,
        "app_version": args.version,
        "branch": args.branch,
        "commit_hash": short,
        "flavor": args.flavor or build_mode,
        "source": args.source,
        "server_type": args.server_type,
        "file_path": str(apk),
        "file_hash": file_hash,
        "notes": f"size={apk.stat().st_size}",
    }
    request = urllib.request.Request(
        f"{PLATFORM_URL}/api/apks",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    print(f"已注册 apk_id={result.get('apk_id')} hash={file_hash[:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
