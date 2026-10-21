"""执行侧「端」识别与设备过滤测试(2026-10-15)。

覆盖:
  - detect_device_os:getprop 判法(荣耀 MagicOS=安卓 / HUAWEI=鸿蒙 / 标准安卓兜底 /
    识别不出="");adb 故障降级为识别不出;
  - filter_serials_by_os:匹配/排除分组,识别不出端一律排除;
  - register_local_devices:指定端只注册匹配设备(带 os_type metadata),未指定全注册;
  - DevicePoolSession.acquire:指定端优先领匹配设备 + 领取后复核,不匹配即归还拒跑;
  - preflight.device_os_match:指定端无匹配设备 → 阻塞,未指定 → 跳过。
"""
from __future__ import annotations

import logging

import pytest

from qa_platform import device_pool, preflight
from qa_platform.device_pool import (
    DevicePoolError,
    DevicePoolSession,
    detect_device_os,
    filter_serials_by_os,
    register_local_devices,
)


def _getprop_output(props: dict[str, str]) -> str:
    """造 adb shell getprop 的标准输出(每行 [key]: [value])。"""
    return "".join(f"[{key}]: [{value}]\n" for key, value in props.items())


PROP_ANDROID = {"ro.product.manufacturer": "Xiaomi", "ro.build.version.release": "14"}
PROP_HONOR = {"ro.product.manufacturer": "HONOR", "ro.build.version.release": "12",
              "ro.build.version.magic": "MagicFull-8.0.0"}  # 荣耀 MagicOS:安卓端,不是鸿蒙
PROP_HUAWEI = {"ro.product.manufacturer": "HUAWEI", "ro.build.version.release": "12"}
PROP_HARMONY_EMUI = {"ro.build.version.emui": "EmotionUI_14.0.0"}
PROP_HARMONY_HWSC = {"hw_sc.build.platform.version": "4.2.0"}
PROP_UNRECOGNIZABLE = {"ro.product.model": "Some-Board"}  # 无任何判据


@pytest.fixture(autouse=True)
def _no_real_adb(monkeypatch):
    """默认拦掉真实 adb 调用:单测不依赖插设备;需要自定义输出的用例再覆盖。"""
    def _forbid(*_args, **_kwargs):
        pytest.fail("测试试图调用真实 adb")
    monkeypatch.setattr(device_pool.subprocess, "run", _forbid)


def _patch_getprop(monkeypatch, outputs: dict[str, str]):
    """按 serial 返回预设 getprop 输出;未登记的 serial 返回空输出。"""
    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = 0
            stdout = outputs.get(cmd[2], "")  # cmd = [adb, -s, <serial>, shell, getprop]
        return Proc()
    monkeypatch.setattr(device_pool.subprocess, "run", fake_run)


def _patch_adb_serials(monkeypatch, pairs: list[tuple[str, str]]):
    monkeypatch.setattr(device_pool, "adb_serials", lambda: list(pairs))


# ==================== detect_device_os ====================

def test_detect_honor_magicos_is_android_not_harmony(monkeypatch):
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR)})
    assert detect_device_os("H1") == "android"  # 荣耀 MagicOS 归安卓端(硬要求)


def test_detect_huawei_props_are_harmony(monkeypatch):
    _patch_getprop(monkeypatch, {"W1": _getprop_output(PROP_HUAWEI),
                                 "W2": _getprop_output(PROP_HARMONY_EMUI),
                                 "W3": _getprop_output(PROP_HARMONY_HWSC)})
    assert detect_device_os("W1") == "harmony"
    assert detect_device_os("W2") == "harmony"
    assert detect_device_os("W3") == "harmony"


def test_detect_plain_android_and_unrecognizable(monkeypatch):
    _patch_getprop(monkeypatch, {"A1": _getprop_output(PROP_ANDROID),
                                 "U1": _getprop_output(PROP_UNRECOGNIZABLE)})
    assert detect_device_os("A1") == "android"
    assert detect_device_os("U1") == ""  # 识别不出


def test_detect_adb_failure_degrades_to_unknown(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("adb not found")
    monkeypatch.setattr(device_pool.subprocess, "run", boom)
    assert detect_device_os("X1") == ""  # 故障降级=识别不出,指定端执行时排除


# ==================== filter_serials_by_os ====================

def test_filter_splits_matching_and_excluded(monkeypatch, caplog):
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR),
                                 "W1": _getprop_output(PROP_HUAWEI),
                                 "U1": _getprop_output(PROP_UNRECOGNIZABLE)})
    with caplog.at_level(logging.INFO, logger="qa_platform.device_pool"):
        matching, excluded = filter_serials_by_os(["H1", "W1", "U1"], "android")
    assert matching == ["H1"]
    assert len(excluded) == 2
    assert any("识别不出端" in item for item in excluded)   # U1:识别不出端,排除
    assert any("端为 harmony" in item for item in excluded)  # W1:端不匹配,排除
    # 排除必须留痕(跑错比漏跑严重,排查依赖日志)
    assert sum("指定端 android 执行时排除" in r.getMessage() for r in caplog.records) == 2


# ==================== register_local_devices ====================

def test_register_filters_by_os_when_specified(monkeypatch):
    monkeypatch.setenv("DEVICE_OS", "android")
    _patch_adb_serials(monkeypatch, [("H1", "Honor-X"), ("W1", "HUAWEI-Mate")])
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR),
                                 "W1": _getprop_output(PROP_HUAWEI)})
    registered = []
    monkeypatch.setattr(device_pool, "_post",
                        lambda path, payload: registered.append((path, payload)) or {})
    result = register_local_devices(platform="flutter")
    assert result == ["H1"]  # 只有安卓端设备进池
    assert [payload["device_id"] for _, payload in registered] == ["H1"]
    assert registered[0][1]["metadata"]["os_type"] == "android"  # 端写进 metadata 供平台侧查看


def test_register_all_devices_when_os_unspecified(monkeypatch):
    monkeypatch.delenv("DEVICE_OS", raising=False)
    _patch_adb_serials(monkeypatch, [("H1", "Honor-X"), ("W1", "HUAWEI-Mate")])
    registered = []
    monkeypatch.setattr(device_pool, "_post",
                        lambda path, payload: registered.append(payload["device_id"]) or {})
    assert register_local_devices() == ["H1", "W1"]  # 不限端:旧行为,全注册
    assert registered == ["H1", "W1"]


# ==================== DevicePoolSession.acquire ====================

def _patch_acquire_post(monkeypatch, acquired: dict):
    monkeypatch.setattr(device_pool, "_post", lambda path, payload: acquired)


def test_acquire_rejects_mismatched_device_and_releases(monkeypatch):
    """平台返回非匹配设备(池内残留历史注册):复核发现 → 归还 + 拒跑。

    本机枚举有匹配端(pass 过第一步),但平台领取返回的是另一台非匹配设备
    (残留注册行),靠领取后复核拦下。
    """
    monkeypatch.setenv("DEVICE_OS", "android")
    _patch_adb_serials(monkeypatch, [("H1", "Honor-X"), ("W1", "HUAWEI")])
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR),
                                 "W1": _getprop_output(PROP_HUAWEI)})
    payloads = []
    released = []
    monkeypatch.setattr(device_pool, "_post", lambda path, payload:
                        payloads.append(payload) or {"device_id": "W1"}
                        if path == "/api/devices/acquire"
                        else released.append(path) or {})
    session = DevicePoolSession(platform="flutter", run_id="run-1")
    with pytest.raises(DevicePoolError, match="端为 harmony.*不符"):
        session.acquire()
    assert "/api/devices/W1/release" in released  # 领取到的设备已归还
    assert "ANDROID_DEVICE_SERIAL" not in device_pool.os.environ  # 不落驱动环境变量
    assert payloads[0]["prefer"] == ["H1"]  # prefer 是本机识别的匹配端


def test_acquire_no_matching_device_blocks_locally(monkeypatch):
    """指定端本机无匹配设备:不占平台领取,直接 env_blocked。"""
    monkeypatch.setenv("DEVICE_OS", "harmony")
    _patch_adb_serials(monkeypatch, [("H1", "Honor-X")])
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR)})
    calls = []
    monkeypatch.setattr(device_pool, "_post",
                        lambda path, payload: calls.append(path) or {})
    session = DevicePoolSession(platform="flutter", run_id="run-1")
    with pytest.raises(DevicePoolError, match="指定端 harmony 无在线匹配设备"):
        session.acquire()
    assert calls == []  # 没碰平台领取


def test_acquire_matching_device_passes_and_sets_serial(monkeypatch):
    monkeypatch.setenv("DEVICE_OS", "android")
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR)})
    _patch_adb_serials(monkeypatch, [("H1", "Honor-X")])
    payloads = []
    monkeypatch.setattr(device_pool, "_post", lambda path, payload:
                        payloads.append(payload) or {"device_id": "H1"})
    session = DevicePoolSession(platform="flutter", run_id="run-1")
    assert session.acquire() == "H1"
    assert device_pool.os.environ["ANDROID_DEVICE_SERIAL"] == "H1"
    assert payloads[0]["prefer"] == ["H1"]  # 匹配端作为 prefer 传平台


def test_acquire_without_device_os_keeps_legacy_behavior(monkeypatch):
    """未指定端:不识别不过滤(旧行为),领取后也不复核。"""
    monkeypatch.delenv("DEVICE_OS", raising=False)
    _patch_adb_serials(monkeypatch, [("ANY1", "Unknown")])
    payloads = []
    monkeypatch.setattr(device_pool, "_post", lambda path, payload:
                        payloads.append(payload) or {"device_id": "ANY1"})
    session = DevicePoolSession(platform="flutter", run_id="run-1")
    assert session.acquire() == "ANY1"
    assert payloads[0]["prefer"] == []  # 不注 prefer


# ==================== preflight.device_os_match ====================

def test_preflight_device_os_match_blocking(monkeypatch):
    monkeypatch.setenv("DEVICE_OS", "android")
    monkeypatch.setattr(device_pool, "adb_serials",
                        lambda: [("H1", "Honor"), ("U1", "?")])
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR),
                                 "U1": _getprop_output(PROP_UNRECOGNIZABLE)})
    ok, detail = preflight.check_device_os_match()
    assert ok and "H1" in detail and "识别不出端" in detail  # 识别不出的进排除明细

    monkeypatch.setattr(device_pool, "adb_serials", lambda: [("W1", "HUAWEI")])
    _patch_getprop(monkeypatch, {"W1": _getprop_output(PROP_HUAWEI)})
    ok, detail = preflight.check_device_os_match()
    assert not ok and "已全部排除" in detail  # 无匹配设备 → 阻塞


def test_preflight_device_os_unspecified_skips(monkeypatch):
    monkeypatch.delenv("DEVICE_OS", raising=False)
    ok, detail = preflight.check_device_os_match()
    assert ok and "跳过" in detail


def test_preflight_run_verdict_blocks_on_os_mismatch(monkeypatch):
    """端到端:run_preflight 里 device_os_match 为阻塞级,无匹配 → ENV_BLOCKED。

    ALL_CHECKS 是模块级函数引用表,整体替换为本用例的桩组合(真检查只留
    device_os_match),其余环境依赖检查全部 mock 放行。
    """
    monkeypatch.setenv("DEVICE_OS", "harmony")
    monkeypatch.setenv("PLATFORM", "flutter")
    monkeypatch.setattr(device_pool, "adb_serials", lambda: [("H1", "Honor")])
    _patch_getprop(monkeypatch, {"H1": _getprop_output(PROP_HONOR)})
    monkeypatch.setattr(preflight, "ALL_CHECKS", [
        ("adb_available", lambda: (True, "mock")),
        ("device_online", lambda: (True, "mock")),
        ("device_os_match", preflight.check_device_os_match),
        ("otp_gateway", lambda: (True, "mock")),
        ("disk_space", lambda: (True, "mock")),
    ])
    verdict = preflight.run_preflight()
    assert verdict["verdict"] == "ENV_BLOCKED"
    assert "device_os_match" in verdict["blocked_by"]
