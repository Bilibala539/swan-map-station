#!/usr/bin/env python3
"""企微告警发送封装(巡检专用)。

发送范围(用户已批准,调用方负责把关):仅 L1 自愈修复汇报 + L2/L3 发现;全绿一律不发送。
实现:wecom-cli message aibot send --msg-type markdown(机器人"吴彦祖");
凭据经 HOME 隔离:--wecom-home 指定目录(部署为 /opt/patrol/wecom-home),其下需
.config/wecom/credentials.enc(从已授权机器拷贝,凭据不进 git 不进仓库)。
errcode 850003 = 机器人在企微服务端授权过期,需创建者重新授权,本地配置无须改动。

用法:
  python3 wecom_send.py --file msg.md                 # 正文读文件,chat_id 读 wecom.conf
  python3 wecom_send.py --text '内容' --dry-run       # 只本地校验,不实际发送
chat_id 缺省读 /opt/patrol/wecom.conf 的 chat_id= 行(--chat-id 可覆盖)。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_CONF = "/opt/patrol/wecom.conf"
DEFAULT_WECOM_HOME = "/opt/patrol/wecom-home"
MAX_CONTENT_CHARS = 3800  # 企微 markdown 上限约 4096 字节,留 UTF-8 中文余量


def load_chat_id(conf_path: Path) -> str:
    if not conf_path.is_file():
        raise SystemExit(f"wecom.conf 不存在: {conf_path}")
    for line in conf_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("chat_id="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    raise SystemExit(f"wecom.conf 缺少 chat_id= 行: {conf_path}")


def build_command(chat_id: str, content: str, dry_run: bool) -> list:
    markdown = json.dumps({"content": content}, ensure_ascii=False)
    command = ["wecom-cli", "message", "aibot", "send",
               "--chat-id", chat_id, "--msg-type", "markdown", "--markdown", markdown]
    if dry_run:
        command.append("--dry-run")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="巡检企微告警发送(范围:L1 修复汇报+L2/L3 发现)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="消息正文文件(markdown)")
    group.add_argument("--text", help="消息正文(直接传字符串)")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--conf", default=DEFAULT_CONF)
    parser.add_argument("--wecom-home", default=DEFAULT_WECOM_HOME)
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印将执行的命令,不发送")
    args = parser.parse_args()

    content = (Path(args.file).read_text(encoding="utf-8") if args.file else args.text).strip()
    if not content:
        raise SystemExit("消息正文为空,拒绝发送")
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n…(超长截断,详见 .167:/opt/patrol/last_report.md)"

    chat_id = args.chat_id or load_chat_id(Path(args.conf))
    command = build_command(chat_id, content, args.dry_run)

    if args.dry_run:
        print("[dry-run] 将执行:")
        print(subprocess.list2cmdline(command)[:500])
        print("[dry-run] 正文预览(前 300 字):")
        print(content[:300])
        return 0

    env = os.environ.copy()
    wecom_home = Path(args.wecom_home)
    if (wecom_home / ".config" / "wecom").is_dir():
        env["HOME"] = str(wecom_home)  # 仅 wecom 子进程换 HOME,不影响外层 claude 凭据
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
    output = (result.stdout or "") + (result.stderr or "")
    print(output.strip()[-800:])
    if "850003" in output:
        print("[提示] errcode 850003:机器人服务端授权过期,需创建者在企微后台重新授权(本地无须改动)",
              file=sys.stderr)
        return 2
    if result.returncode != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
