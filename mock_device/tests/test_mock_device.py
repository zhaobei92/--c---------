"""模拟设备契约测试:BLE 状态机命令、Wi-Fi 文件面 Range 续传、SHA-256 校验、故障注入。

这些用例同时是 App 侧 DeviceTransport 实现的行为基准(SYNC-08/12 等)。
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from mock_device.device_state import DeviceSimulator
from mock_device.server import build_apps


@pytest.fixture
def env():
    control_app, files_app, dev = build_apps(DeviceSimulator())
    dev.seed_files(2)
    return TestClient(control_app), TestClient(files_app), dev


def cmd(client, name, **params):
    return client.post("/cmd", json={"cmd": name, "params": params, "seq": 1}).json()


# ---------------- 控制面(BLE 状态机)

def test_info_battery_storage(env):
    control, _, dev = env
    info = cmd(control, "info.get")
    assert info["ok"] and info["data"]["sn"] == dev.sn
    assert cmd(control, "battery.get")["data"]["percent"] == dev.battery
    assert cmd(control, "storage.get")["data"]["total_mb"] == dev.total_mb


def test_record_state_machine(env):
    control, _, _ = env
    assert cmd(control, "record.status")["data"]["state"] == "idle"
    assert cmd(control, "record.pause")["error"] == "E_BAD_STATE"   # idle 不能暂停
    assert cmd(control, "record.start", mode="call")["data"]["state"] == "recording"
    assert cmd(control, "record.start")["error"] == "E_BAD_STATE"   # 已在录音
    assert cmd(control, "record.pause")["data"]["state"] == "paused"
    stopped = cmd(control, "record.stop")
    assert stopped["ok"] and stopped["data"]["file"]["mode"] == "call"
    assert cmd(control, "record.status")["data"]["state"] == "idle"


def test_unknown_command_and_file_delete(env):
    control, _, dev = env
    assert cmd(control, "nope")["error"] == "E_BAD_CMD"
    file_id = next(iter(dev.files))
    assert cmd(control, "files.delete", file_id=file_id)["data"]["deleted"]
    assert cmd(control, "files.delete", file_id=file_id)["error"] == "E_NOT_FOUND"


def test_ota_battery_threshold_and_busy(env):
    control, _, _ = env
    cmd(control, "fault.inject", kind="low_battery", value=10)
    assert cmd(control, "ota.enter", version="1.0.0", min_battery=30)["error"] == "E_LOW_BATTERY"
    cmd(control, "fault.inject", kind="low_battery", value=90)
    assert cmd(control, "ota.enter", version="1.0.0", min_battery=30)["data"]["accepted"]
    # OTA 模式中拒绝其他命令
    assert cmd(control, "files.list")["error"] == "E_BUSY"
    assert cmd(control, "reboot")["ok"]
    assert cmd(control, "files.list")["ok"]


# ---------------- 文件面(Wi-Fi)

def test_file_plane_requires_wifi(env):
    control, files, _ = env
    assert files.get("/files").status_code == 503  # 未开热点 = 未加入设备网络
    wifi = cmd(control, "wifi.start")["data"]
    assert wifi["ssid"] and wifi["password"]
    assert files.get("/files").status_code == 200
    cmd(control, "wifi.stop")
    assert files.get("/files").status_code == 503


def test_full_download_and_hash(env):
    control, files, dev = env
    cmd(control, "wifi.start")
    entry = files.get("/files").json()["files"][0]
    body = files.get(f"/files/{entry['id']}").content
    assert len(body) == entry["size"]
    assert hashlib.sha256(body).hexdigest() == entry["sha256"]
    assert files.get(f"/files/{entry['id']}/sha256").json()["sha256"] == entry["sha256"]
    assert files.post(f"/files/{entry['id']}/ack").json()["acked"]
    assert dev.files[entry["id"]].synced is True


def test_range_resume_after_drop(env):
    """SYNC-08:传输中断后用 Range 从检查点续传,拼接结果 Hash 一致。"""
    control, files, _ = env
    cmd(control, "wifi.start")
    entry = files.get("/files").json()["files"][0]
    cmd(control, "fault.inject", kind="wifi_drop_after", value=1000)

    first = files.get(f"/files/{entry['id']}").content
    assert len(first) == 1000  # 中断:只收到 1000 字节

    resume = files.get(f"/files/{entry['id']}", headers={"Range": f"bytes={len(first)}-"})
    assert resume.status_code == 206
    assert resume.headers["Content-Range"].startswith(f"bytes {len(first)}-")
    full = first + resume.content
    assert len(full) == entry["size"]
    assert hashlib.sha256(full).hexdigest() == entry["sha256"]


def test_partial_range_request(env):
    control, files, _ = env
    cmd(control, "wifi.start")
    entry = files.get("/files").json()["files"][0]
    r = files.get(f"/files/{entry['id']}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert len(r.content) == 100
    assert r.headers["Content-Range"] == f"bytes 100-199/{entry['size']}"


def test_corrupt_file_detected_by_hash(env):
    """SYNC-12:内容损坏 → 下载数据与 sha256 不一致,App 应触发重试(DEV_1204 路径)。"""
    control, files, _ = env
    cmd(control, "wifi.start")
    entry = files.get("/files").json()["files"][0]
    cmd(control, "fault.inject", kind="corrupt_file", value=entry["id"])
    body = files.get(f"/files/{entry['id']}").content
    declared = files.get(f"/files/{entry['id']}/sha256").json()["sha256"]
    assert hashlib.sha256(body).hexdigest() != declared
    # 清除故障后重试成功
    cmd(control, "fault.inject", kind="clear")
    body2 = files.get(f"/files/{entry['id']}").content
    assert hashlib.sha256(body2).hexdigest() == declared


def test_download_unknown_file(env):
    control, files, _ = env
    cmd(control, "wifi.start")
    assert files.get("/files/NOPE").status_code == 404
