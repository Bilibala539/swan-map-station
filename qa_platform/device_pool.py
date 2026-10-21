"""设备池执行侧客户端(P3)。

执行机(本机)在会话开始时:
  1. 把 adb 在线设备注册进平台池(register)
  2. acquire 领取一台空闲设备(平台按"最久未用优先"平均分配)
  3. 把领到的 serial 写入 ANDROID_DEVICE_SERIAL(驱动自动使用)
会话结束 release 归还。环境阻塞(无空闲设备)→ 会话拒跑。

用法(conftest 已接):
    from qa_platform.device_pool import DevicePoolSession
    pool = DevicePoolSession(platform="flutter", run_id=...)
    device = pool.acquire()      # 失败抛 DevicePoolError
    ...测试...
    pool.release()
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request


class DevicePoolError(RuntimeError):
    """设备池操作失败(无可领设备/平台不可达等)。"""


def _platform_url() -> str:
    url = os.environ.get("DASHBOARD_URL", "http://192.168.12.167:8080").rstrip("/")
    return url


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{_platform_url()}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = f"HTTP {error.code}"
        raise DevicePoolError(detail) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DevicePoolError(f"平台不可达: {error}") from error


def adb_serials() -> list[tuple[str, str]]:
    """返回 [(serial, model), ...]。"""
    try:
        proc = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise DevicePoolError(f"adb devices 失败: {error}") from error
    pairs = []
    for line in proc.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            model = ""
            for field in fields[2:]:
                if field.startswith("model:"):
                    model = field.split(":", 1)[1]
            pairs.append((fields[0], model))
    return pairs


def register_local_devices(platform: str = "flutter") -> list[str]:
    """把本机 adb 在线设备全部注册进平台池,返回 serial 列表。"""
    serials = []
    for serial, model in adb_serials():
        _post("/api/devices/register", {
            "device_id": serial,
            "name": model or serial,
            "platform": platform,
            "model": model,
            # COMPUTERNAME 是 Windows 变量,Linux 回落 HOSTNAME/主机名
            "metadata": {"host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname()},
        })
        serials.append(serial)
    return serials


class DevicePoolSession:
    """一次测试会话的设备领取/归还。"""

    def __init__(self, platform: str, run_id: str):
        self.platform = platform
        self.run_id = run_id
        self.device_id: str | None = None

    def acquire(self, prefer: list[str] | None = None) -> str:
        device = _post("/api/devices/acquire", {
            "run_id": self.run_id,
            "platform": self.platform,
            "prefer": prefer or [],
        })
        self.device_id = str(device["device_id"])
        # 驱动层自动读取该环境变量选设备
        os.environ["ANDROID_DEVICE_SERIAL"] = self.device_id
        return self.device_id

    def release(self) -> None:
        if not self.device_id:
            return
        import logging
        try:
            try:
                online = self.device_id in {serial for serial, _ in adb_serials()}
            except DevicePoolError:
                # adb 自身不可用无法判断,按在线归还(误判拔线会让好设备退池);真失联交给平台 4h 自愈兜底
                online = True
            if online:
                _post(f"/api/devices/{self.device_id}/release", {"run_id": self.run_id})
            else:
                # 设备已拔线/失联:置 offline 移出可领队列,避免下个任务领到即失败;
                # 重插上后 register 复活回 idle
                _post(f"/api/devices/{self.device_id}/status", {"status": "offline"})
                logging.getLogger(__name__).warning(
                    f"设备 {self.device_id} adb 不可见,按失联置 offline")
        except DevicePoolError as error:
            # 归还失败只告警:平台侧有 busy 超时自愈空间,不阻塞会话收尾
            logging.getLogger(__name__).warning(f"设备归还失败: {error}")
        finally:
            self.device_id = None
            os.environ.pop("ANDROID_DEVICE_SERIAL", None)
